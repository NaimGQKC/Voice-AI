"""What happens when Libro stops answering.

This is the failure the whole project is exposed to: the agent books real tables
through a reverse-engineered private API behind a long-lived token, on a system
nobody is watching. The token will eventually die or the API will drift, and
when it does the only thing that matters is that the caller is *captured* rather
than dropped — a name and a number in SQLite is a callback the restaurant can
make; dead air is a guest nobody ever knows rang.

Three groups of tests:

  1. Concierge — an outage never claims an outcome, and always captures.
  2. Adapter   — requests are bounded, GETs retry once, writes never retry.
  3. Healthcheck — the drift detectors actually detect drift (offline).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from resto_agent import faq
from resto_agent.concierge import Concierge
from resto_agent.reservation import libro_private as lp
from resto_agent.reservation.errors import (
    BackendAuthError,
    BackendUnavailableError,
    BookingOutcomeUnknownError,
    ReservationError,
    SlotUnavailableError,
)
from resto_agent.reservation.libro_private import LibroPrivateReservationService
from resto_agent.reservation.mock import MockReservationService
from resto_agent.store import CallStore
from tests.conftest import future_date, slot_time

#: A date inside the booking horizon, for the Concierge tests (which validate it).
SOON_DATE = future_date()
SOON = slot_time(SOON_DATE, "19:00")

#: Far-future, for adapter-level tests that never reach the horizon check. Also
#: the owner's standing rule for anything that could ever touch the live floor:
#: a stray booking in 2031 cannot collide with a real guest.
FUTURE = "2031-09-08T19:00:00-04:00"


@pytest.fixture
def store():
    s = CallStore(":memory:")
    yield s
    s.close()


async def _concierge(store):
    svc = MockReservationService.in_process(db_path=":memory:")
    return Concierge(svc, store=store, call_id="call-outage")


def _claims_success(msg: str) -> bool:
    """Any phrasing that would make a caller believe they have a table."""
    low = msg.lower()
    return ("all set" in low or "you're booked" in low or "confirmed for" in low
            or "i've booked" in low)


# ===========================================================================
# 1. Concierge: a Libro outage captures the caller instead of losing them
# ===========================================================================
async def test_libro_outage_mid_booking_captures_the_caller(store):
    """THE test. Libro dies mid-booking; the caller must still be recoverable.

    Everything else in this file supports this one assertion: after the outage
    there is a row with the guest's name and number that a human can act on.
    """
    c = await _concierge(store)
    try:
        async def dead(**kwargs):
            raise BackendUnavailableError("POST /bookings timed out")

        c.service.create_booking = dead
        msg = await c.book_reservation(time=SOON, party_size=4,
                                       first_name="Alex", phone="514-555-1234")
    finally:
        await c.service.aclose()

    # 1. We did NOT tell them they have a table.
    assert not _claims_success(msg)
    # 2. We told them the truth and promised the one thing we can deliver.
    assert "call you right back" in msg.lower()

    # 3. The caller is durable — this is what makes them recoverable.
    del c  # caller hangs up; the Concierge is garbage-collected
    pending = store.pending_messages()
    assert len(pending) == 1
    row = pending[0]
    assert row.kind == "callback"
    assert row.name == "Alex"
    assert row.phone == "+15145551234"  # normalized, so it can be dialled
    assert row.party_size == 4
    assert "COULD NOT CONFIRM" in row.body

    # 4. And the owner can see it in the failed-bookings report.
    failed = store.failed_bookings()
    assert len(failed) == 1 and failed[0]["ok"] == 0
    assert "BackendUnavailable" in failed[0]["error"]


async def test_unknown_write_outcome_is_never_spoken_as_success(store):
    """A create that timed out may have landed. We must not guess either way."""
    c = await _concierge(store)
    try:
        async def unknown(**kwargs):
            raise BookingOutcomeUnknownError("POST /bookings timed out")

        c.service.create_booking = unknown
        msg = await c.book_reservation(time=SOON, party_size=2,
                                       first_name="Sam", phone="514-555-2222")
    finally:
        await c.service.aclose()

    assert not _claims_success(msg)
    # Nor may it claim the opposite — "we're full" is equally invented.
    assert "fully booked" not in msg.lower()
    assert store.pending_messages()[0].kind == "callback"


async def test_unexpected_exception_does_not_end_the_call(store):
    """An exception out of a tool leaves the caller in silence. Never propagate."""
    c = await _concierge(store)
    try:
        async def boom(**kwargs):
            raise RuntimeError("something nobody predicted")

        c.service.create_booking = boom
        msg = await c.book_reservation(time=SOON, party_size=2,
                                       first_name="Riley", phone="514-555-3333")
    finally:
        await c.service.aclose()

    assert isinstance(msg, str) and msg
    assert not _claims_success(msg)
    assert store.pending_messages()[0].name == "Riley"


async def test_outage_plus_unwritable_store_gives_the_restaurant_number(store):
    """Both sides broken: say so honestly rather than promise a callback."""
    c = await _concierge(store)
    try:
        async def dead(**kwargs):
            raise BackendUnavailableError()

        def cannot_write(**kwargs):
            raise RuntimeError("disk full")

        c.service.create_booking = dead
        store.record_message = cannot_write
        msg = await c.book_reservation(time=SOON, party_size=2,
                                       first_name="Casey", phone="514-555-4444")
    finally:
        await c.service.aclose()

    assert not _claims_success(msg)
    assert faq.PHONE in msg               # a human they can reach themselves
    assert "call you right back" not in msg.lower()  # no promise we can't keep


async def test_availability_outage_does_not_say_fully_booked(store):
    """'We're fully booked' when we simply couldn't look sends a guest away."""
    c = await _concierge(store)
    c.state.phone = "+15145555555"
    c.state.name = "Jordan"
    try:
        async def dead(*a, **k):
            raise BackendUnavailableError()

        c.service.check_availability = dead
        msg = await c.check_availability(date=SOON_DATE, party_size=2)
    finally:
        await c.service.aclose()

    assert "fully booked" not in msg.lower()
    assert "trouble reaching" in msg.lower()
    assert c.state.backend_degraded is True
    assert store.pending_messages()[0].kind == "callback"


async def test_availability_outage_asks_for_a_number_when_it_has_none(store):
    """No phone yet = nothing to capture. Ask for the one thing that saves them."""
    c = await _concierge(store)
    try:
        async def dead(*a, **k):
            raise BackendUnavailableError()

        c.service.check_availability = dead
        msg = await c.check_availability(date=SOON_DATE, party_size=2)
    finally:
        await c.service.aclose()

    assert "name and number" in msg.lower()
    assert c.state.pending_waitlist is True
    assert store.pending_messages() == []  # nothing half-recorded


async def test_waitlist_after_an_outage_promises_a_callback_not_a_text(store):
    """A degraded waitlist must not even promise a callback-on-opening."""
    c = await _concierge(store)
    try:
        c.state.backend_degraded = True
        msg = c.join_waitlist(name="Robin", phone="514-555-6666",
                              date=SOON_DATE, party_size=2)
    finally:
        await c.service.aclose()

    assert "text you" not in msg.lower()
    assert "call you back" in msg.lower()
    assert store.pending_messages()[0].kind == "callback"


async def test_normal_waitlist_still_promises_a_text(store):
    """The degraded path must not leak into the healthy one."""
    c = await _concierge(store)
    try:
        msg = c.join_waitlist(name="Robin", phone="514-555-6666",
                              date=SOON_DATE, party_size=2)
    finally:
        await c.service.aclose()

    # NOT "we'll text you": Libro has no future-dated waitlist and we have no
    # opening-watcher, so a human calling back is the only true promise.
    assert "text you" not in msg.lower()
    assert "call you back" in msg.lower()
    assert store.pending_messages()[0].kind == "waitlist"


async def test_cancel_outage_never_sounds_like_a_cancellation(store):
    """If the guest thinks it's cancelled and it isn't, the table sits empty."""
    c = await _concierge(store)
    try:
        booking = await c.service.create_booking(
            time=SOON, party_size=2, first_name="Pat", phone="+15145557777")

        async def dead(*a, **k):
            raise BackendUnavailableError()

        c.service.cancel_booking = dead
        msg = await c.cancel_reservation(booking_id=booking.id)
    finally:
        await c.service.aclose()

    assert "cancelled" not in msg.lower() or "still booked" in msg.lower()
    assert "still booked" in msg.lower()
    assert "done" not in msg.lower()[:6]


async def test_lookup_outage_does_not_deny_an_existing_reservation(store):
    c = await _concierge(store)
    try:
        async def dead(*a, **k):
            raise BackendUnavailableError()

        c.service.list_bookings = dead
        msg = await c.lookup_reservations(phone="514-555-8888")
    finally:
        await c.service.aclose()

    assert "don't see any" not in msg.lower()
    assert store.pending_messages()[0].kind == "callback"


async def test_a_real_no_is_still_spoken_as_a_no(store):
    """Degradation must not swallow genuine answers: 'that slot is gone' stands."""
    c = await _concierge(store)
    try:
        async def gone(**kwargs):
            raise SlotUnavailableError()

        c.service.create_booking = gone
        msg = await c.book_reservation(time=SOON, party_size=2,
                                       first_name="Lee", phone="514-555-9999")
    finally:
        await c.service.aclose()

    assert "isn't available anymore" in msg
    # A real answer is not an outage: no callback row, just the failure log.
    assert store.pending_messages() == []
    assert len(store.failed_bookings()) == 1


# ===========================================================================
# 2. Adapter: bounded timeouts, retry GET only, never retry a booking create
# ===========================================================================
def _service(handler=None) -> LibroPrivateReservationService:
    svc = LibroPrivateReservationService(token="fake-token",
                                         email="owner@example.com",
                                         restaurant_id="<redacted>")
    if handler is not None:
        old = svc._client
        svc._client = httpx.AsyncClient(
            base_url="https://api.libroreserve.com",
            timeout=old.timeout, headers=old.headers,
            transport=httpx.MockTransport(handler),
        )
    return svc


def test_timeouts_are_sized_for_a_live_phone_call():
    """A caller will not wait 30 seconds. Nothing here may be unbounded."""
    svc = _service()
    read, write = svc._read_timeout, svc._write_timeout

    for t in (read, write):
        # httpx defaults every field to None (= wait forever) if unset. None of
        # these may be None: an unbounded field is a hang waiting to happen.
        assert t.connect is not None and t.read is not None
        assert t.write is not None and t.pool is not None
        assert t.connect <= 5.0

    # A read is what happens while the caller waits for "is 7pm free?".
    assert read.read <= 5.0
    # A write gets longer, because it is never retried and giving up early on a
    # create that would have succeeded is the expensive mistake.
    assert write.read > read.read
    # But still inside what a person will sit through.
    assert write.read <= 10.0

    # Worst case for a retried GET stays inside the silence budget.
    assert lp.RETRY_BUDGET_S <= 10.0
    assert lp.GET_MAX_ATTEMPTS == 2


def test_explicit_timeout_override_still_applies_everywhere():
    svc = _service()
    other = LibroPrivateReservationService(token="t", email="e@x.co",
                                           restaurant_id="<redacted>", timeout=1.0)
    assert other._read_timeout.read == 1.0 and other._write_timeout.read == 1.0
    assert svc._read_timeout.read != 1.0


async def test_get_is_retried_exactly_once_then_succeeds(monkeypatch):
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ReadTimeout("first attempt hung", request=request)
        return httpx.Response(200, json={"2031-09-08T19:00:00-04:00": {"2": {"": 4}}})

    svc = _service(handler)
    try:
        avail = await svc.check_availability("2031-09-08", 2)
    finally:
        await svc.aclose()

    assert len(calls) == 2          # one retry, and only one
    assert len(avail.slots) == 1    # the retry's answer is used


async def test_get_gives_up_after_two_attempts(monkeypatch):
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("no route to host", request=request)

    svc = _service(handler)
    try:
        with pytest.raises(BackendUnavailableError):
            await svc.check_availability("2031-09-08", 2)
    finally:
        await svc.aclose()

    assert len(calls) == lp.GET_MAX_ATTEMPTS == 2  # bounded, not a retry storm


async def test_booking_create_is_never_retried(monkeypatch):
    """Re-sending a create can seat one guest at two tables. Never do it.

    There is no idempotency key on this API, so a timed-out POST /bookings has
    an unknowable outcome. One attempt, then hand the caller to a human.
    """
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/people/query":
            return httpx.Response(200, json={"data": [{"id": "P1", "type": "people"}]})
        if request.method == "GET" and request.url.path == "/services":
            return httpx.Response(200, json={"data": [{
                "id": "S1", "type": "services",
                "attributes": {"started-at": "2031-09-08T23:00:00Z"}}]})
        posts.append(request.url.path)
        raise httpx.ReadTimeout("booking create hung", request=request)

    svc = _service(handler)
    try:
        with pytest.raises(BookingOutcomeUnknownError):
            await svc.create_booking(time=FUTURE, party_size=2,
                                     first_name="ZZ", phone="+15145550199")
    finally:
        await svc.aclose()

    assert posts == ["/bookings"]  # exactly one attempt, no retry


async def test_person_create_is_never_retried_either(monkeypatch):
    """A retried POST /people quietly duplicates the guest record."""
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})  # no existing guest
        posts.append(request.url.path)
        raise httpx.ReadTimeout("hung", request=request)

    svc = _service(handler)
    try:
        with pytest.raises(BookingOutcomeUnknownError):
            await svc.create_booking(time=FUTURE, party_size=2,
                                     first_name="ZZ", phone="+15145550199")
    finally:
        await svc.aclose()

    assert posts == ["/people"]


async def test_server_errors_are_retried_but_bounded(monkeypatch):
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="upstream unavailable")

    svc = _service(handler)
    try:
        with pytest.raises(BackendUnavailableError):
            await svc.check_availability("2031-09-08", 2)
    finally:
        await svc.aclose()

    assert len(calls) == 2


async def test_gateway_timeout_on_a_write_is_ambiguous_not_a_failure(monkeypatch):
    """A 504 on POST /bookings may still have been processed by the origin.

    Calling that a definite failure invites the caller to rebook a table they
    might already hold. "Unknown" routes to a human, which is safe either way.
    """
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/people/query":
            return httpx.Response(200, json={"data": [{"id": "P1"}]})
        if request.method == "GET" and request.url.path == "/services":
            return httpx.Response(200, json={"data": [{
                "id": "S1", "type": "services",
                "attributes": {"started-at": "2031-09-08T23:00:00Z"}}]})
        posts.append(request.url.path)
        return httpx.Response(504, text="gateway timeout")

    svc = _service(handler)
    try:
        with pytest.raises(BookingOutcomeUnknownError):
            await svc.create_booking(time=FUTURE, party_size=2,
                                     first_name="ZZ", phone="+15145550199")
    finally:
        await svc.aclose()

    assert posts == ["/bookings"]  # still never retried


async def test_500_is_backend_unavailable_not_a_generic_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"errors": [{"detail": "boom"}]})

    svc = _service(handler)
    try:
        with pytest.raises(BackendUnavailableError):
            await svc.get_booking("123")
    finally:
        await svc.aclose()


async def test_dead_token_is_an_auth_error():
    """The most likely unattended failure: the long-lived token stops working."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"detail": "invalid token"}]})

    svc = _service(handler)
    try:
        with pytest.raises(BackendAuthError) as exc:
            await svc.get_booking("123")
    finally:
        await svc.aclose()

    # Auth failure degrades exactly like an outage mid-call...
    assert isinstance(exc.value, BackendUnavailableError)
    # ...but is a distinct class so the health check can name the real fix.
    assert exc.value.code == "401"


async def test_422_is_still_a_real_slot_answer():
    """Don't over-degrade: a 422 is Libro telling us the slot went. That's true."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"errors": [{"code": 1006}]})

    svc = _service(handler)
    try:
        with pytest.raises(SlotUnavailableError):
            await svc.get_booking("123")
    finally:
        await svc.aclose()


async def test_service_lookup_outage_is_not_reported_as_slot_unavailable(monkeypatch):
    """An outage must not be laundered into 'that time isn't available'.

    `_service_id_for_time` returns "" when it can't find a slot, and
    `create_booking` turns "" into SlotUnavailableError. If a timeout took the
    same path, the agent would tell a caller the restaurant is full because of a
    dropped packet — and the caller would go elsewhere.
    """
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/people/query":
            return httpx.Response(200, json={"data": [{"id": "P1", "type": "people"}]})
        raise httpx.ReadTimeout("services hung", request=request)

    svc = _service(handler)
    try:
        with pytest.raises(BackendUnavailableError) as exc:
            await svc.create_booking(time=FUTURE, party_size=2,
                                     first_name="ZZ", phone="+15145550199")
        assert not isinstance(exc.value, SlotUnavailableError)
    finally:
        await svc.aclose()


async def test_list_bookings_outage_raises_instead_of_returning_empty(monkeypatch):
    """An empty list reads as 'you have no reservation'. During an outage that's
    how a guest with a confirmed table gets told they don't have one."""
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/people/query":
            return httpx.Response(200, json={"data": [{"id": "P1"}]})
        raise httpx.ReadTimeout("hung", request=request)

    svc = _service(handler)
    try:
        with pytest.raises(BackendUnavailableError):
            await svc.list_bookings(phone="+15145550199")
    finally:
        await svc.aclose()


async def test_patch_refuses_to_blank_a_booking_it_could_not_read(monkeypatch):
    """The PATCH echoes the record back. Without the read it would send an empty
    attribute set over a live guest's reservation."""
    monkeypatch.setattr(lp, "RETRY_BACKOFF_S", 0.0)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        raise httpx.ReadTimeout("hung", request=request)

    svc = _service(handler)
    try:
        with pytest.raises(BackendUnavailableError):
            await svc.cancel_booking("<redacted>")
    finally:
        await svc.aclose()

    assert "PATCH" not in seen  # never attempted with an unknown current state


async def test_backend_unavailable_is_a_reservation_error():
    """Existing `except ReservationError` handlers must keep catching it."""
    assert issubclass(BackendUnavailableError, ReservationError)
    assert issubclass(BookingOutcomeUnknownError, BackendUnavailableError)


# ===========================================================================
# 3. Health check: detect API drift, not just auth failure (offline)
# ===========================================================================
from scripts import healthcheck as hc  # noqa: E402

#: The shape verified by direct probe, in the form the adapter parses.
GOOD_AVAILABILITY = {
    "2031-09-08T17:15:00-04:00": {"1": {"": 17}, "2": {"": 14}, "3": {"": 9},
                                  "4": {"": 5}, "5": {"": 2}, "6": {"": 1}},
    "2031-09-08T17:45:00-04:00": {"1": {"": 12}, "2": {"": 8}, "3": {"": 4},
                                  "4": {"": 2}, "5": {}, "6": {}},
}
GOOD_SERVICES = {"data": [
    {"id": "S1", "type": "services",
     "attributes": {"status": "opened", "started-at": "2031-09-08T21:15:00Z"}},
    {"id": "S2", "type": "services",
     "attributes": {"status": "closed", "started-at": "2031-09-08T21:45:00Z"}},
]}


def test_known_good_availability_is_not_flagged():
    assert hc.validate_availability_shape(GOOD_AVAILABILITY, date="2031-09-08") == []


def test_empty_inner_map_means_full_not_drift():
    """`{}` = full for that party size. It is a normal value, not a broken one."""
    body = {"2031-09-08T17:15:00-04:00": {str(i): {} for i in range(1, 7)}}
    assert hc.validate_availability_shape(body) == []


def test_a_new_party_size_key_is_drift():
    """Party sizes are ONLY ever 1-6. A '7' would falsify MAX_ONLINE_PARTY."""
    body = {"2031-09-08T17:15:00-04:00": {**{str(i): {"": 2} for i in range(1, 7)},
                                          "7": {"": 1}}}
    problems = hc.validate_availability_shape(body)
    assert problems and any("NEW party-size keys" in p for p in problems)
    assert any("MAX_ONLINE_PARTY" in p for p in problems)


def test_a_disappearing_party_size_key_is_drift():
    body = {"2031-09-08T17:15:00-04:00": {str(i): {"": 2} for i in range(1, 5)}}
    problems = hc.validate_availability_shape(body)
    assert any("DISAPPEARED" in p for p in problems)


def test_jsonapi_envelope_on_availabilities_is_drift():
    """If /availabilities moved to the v1 dialect the adapter sees zero slots."""
    problems = hc.validate_availability_shape({"data": [{"id": "1"}]})
    assert any("JSON:API envelope" in p for p in problems)


def test_a_list_instead_of_a_map_is_drift():
    problems = hc.validate_availability_shape([{"time": "x"}])
    assert any("LIST" in p for p in problems)


def test_non_datetime_keys_are_drift():
    problems = hc.validate_availability_shape({"lunch": {"2": {"": 1}}})
    assert any("not an ISO-8601 datetime" in p for p in problems)


def test_scalar_counts_replaced_by_something_else_is_drift():
    body = {"2031-09-08T17:15:00-04:00": {str(i): {"": 2} for i in range(1, 7)}}
    body["2031-09-08T17:15:00-04:00"]["4"] = 5          # count, not {area: count}
    assert any("expected {seating-area" in p or "expected" in p
               for p in hc.validate_availability_shape(body))

    body2 = {"2031-09-08T17:15:00-04:00": {str(i): {"": "many"} for i in range(1, 7)}}
    assert any("expected a number" in p for p in hc.validate_availability_shape(body2))


def test_known_good_services_are_not_flagged():
    assert hc.validate_services_shape(GOOD_SERVICES, date="2031-09-08") == []


def test_services_without_started_at_is_drift():
    bad = {"data": [{"id": "S1", "type": "services", "attributes": {}}]}
    assert any("started-at" in p for p in hc.validate_services_shape(bad))


def test_services_without_an_id_is_drift():
    bad = {"data": [{"type": "services",
                     "attributes": {"started-at": "2031-09-08T21:15:00Z"}}]}
    assert any("no 'id'" in p for p in hc.validate_services_shape(bad))


def test_empty_services_list_is_drift():
    assert hc.validate_services_shape({"data": []})


def test_service_as_slot_model_is_verified_read_only():
    """The assumption every booking rests on: a service starts on a slot instant."""
    assert hc.service_matches_a_slot(GOOD_AVAILABILITY, GOOD_SERVICES) is True

    shifted = {"data": [{"id": "S9", "type": "services",
                         "attributes": {"started-at": "2031-09-08T21:07:00Z"}}]}
    assert hc.service_matches_a_slot(GOOD_AVAILABILITY, shifted) is False


def test_has_bookable_cell():
    assert hc.has_bookable_cell(GOOD_AVAILABILITY) is True
    assert hc.has_bookable_cell(
        {"2031-09-08T17:15:00-04:00": {"2": {}, "4": {}}}) is False


def test_healthcheck_exit_codes_are_distinct_and_nonzero_on_failure():
    """Cron only reads the exit code, so the codes are the whole interface."""
    assert hc.EXIT_OK == 0
    codes = {hc.EXIT_DRIFT, hc.EXIT_AUTH, hc.EXIT_UNREACHABLE, hc.EXIT_CONFIG}
    assert 0 not in codes and len(codes) == 4

    r = hc.Report()
    assert r.exit_code == hc.EXIT_OK
    r.drift.append("shape changed")
    assert r.exit_code == hc.EXIT_DRIFT
    r.unreachable.append("timeout")
    assert r.exit_code == hc.EXIT_UNREACHABLE   # can't judge drift if we can't read
    r.auth_failed = True
    assert r.exit_code == hc.EXIT_AUTH          # a dead token is the real headline


# ---- end to end, offline: the exit code is the whole cron interface -------
def _canned(*, availability=None, services=None, status=200, services_status=200):
    """Stand in for the network. Returns (status, body, elapsed) like `_get`."""
    async def _fake(client, path, *, params, accept):
        if path.startswith("/availabilities/"):
            return status, availability, 0.05
        return services_status, services, 0.05
    return _fake


@pytest.fixture
def libro_env(monkeypatch):
    monkeypatch.setenv("LIBRO_PRIVATE_TOKEN", "not-a-real-token")
    monkeypatch.setenv("LIBRO_PRIVATE_EMAIL", "owner@example.com")
    monkeypatch.setenv("LIBRO_PRIVATE_RESTAURANT_ID", "<redacted>")


async def test_healthcheck_passes_on_the_known_good_api(libro_env, monkeypatch):
    monkeypatch.setattr(hc, "_get", _canned(availability=GOOD_AVAILABILITY,
                                            services=GOOD_SERVICES))
    report = await hc.run_health_check(offsets=(2,))
    assert report.drift == [] and report.unreachable == []
    assert report.exit_code == hc.EXIT_OK
    names = {c["check"] for c in report.checks if c["ok"]}
    assert {"auth accepted", "availability shape", "services shape",
            "service-as-slot model holds"} <= names


async def test_healthcheck_reports_a_dead_token_as_auth_failure(libro_env, monkeypatch):
    """The most likely unattended failure gets its own exit code and its own fix."""
    monkeypatch.setattr(hc, "_get", _canned(status=401))
    report = await hc.run_health_check(offsets=(2,))
    assert report.auth_failed is True
    assert report.exit_code == hc.EXIT_AUTH
    note = next(c["note"] for c in report.checks if c["check"] == "auth accepted")
    assert "re-mint" in note.lower()


async def test_healthcheck_reports_shape_drift(libro_env, monkeypatch):
    """A party size Libro has never returned = our ceiling assumption is dead."""
    drifted = {"2031-09-08T17:15:00-04:00":
               {**{str(i): {"": 2} for i in range(1, 7)}, "8": {"": 1}}}
    monkeypatch.setattr(hc, "_get", _canned(availability=drifted,
                                            services=GOOD_SERVICES))
    report = await hc.run_health_check(offsets=(2,))
    assert report.exit_code == hc.EXIT_DRIFT
    assert any("NEW party-size keys" in d for d in report.drift)


async def test_healthcheck_catches_the_service_as_slot_model_breaking(
        libro_env, monkeypatch):
    """The subtlest drift: shapes fine, but no service lands on a slot instant.

    This is the one that would otherwise present as "the agent says we're full".
    """
    shifted = {"data": [{"id": "S9", "type": "services",
                         "attributes": {"started-at": "2031-09-08T21:07:00Z"}}]}
    monkeypatch.setattr(hc, "_get", _canned(availability=GOOD_AVAILABILITY,
                                            services=shifted))
    report = await hc.run_health_check(offsets=(2,))
    assert report.exit_code == hc.EXIT_DRIFT
    assert any("service-as-slot model broken" in d for d in report.drift)


async def test_healthcheck_reports_unreachable_separately(libro_env, monkeypatch):
    async def _boom(client, path, *, params, accept):
        raise OSError("connection refused")

    monkeypatch.setattr(hc, "_get", _boom)
    report = await hc.run_health_check(offsets=(2, 5))
    assert report.exit_code == hc.EXIT_UNREACHABLE
    assert len(report.unreachable) == 2


async def test_healthcheck_does_not_cry_drift_on_one_closed_day(
        libro_env, monkeypatch):
    """The restaurant is legitimately shut some days. Sample several, not one."""
    calls: list[str] = []

    async def _mixed(client, path, *, params, accept):
        if path.startswith("/availabilities/"):
            calls.append(path)
            if len(calls) == 1:
                return 200, {}, 0.05          # closed that day
            return 200, GOOD_AVAILABILITY, 0.05
        return 200, GOOD_SERVICES, 0.05

    monkeypatch.setattr(hc, "_get", _mixed)
    report = await hc.run_health_check(offsets=(2, 5))
    assert report.exit_code == hc.EXIT_OK


async def test_healthcheck_flags_an_endpoint_that_returns_nothing_at_all(
        libro_env, monkeypatch):
    """Every sampled date empty is not 'closed for three weeks'."""
    monkeypatch.setattr(hc, "_get", _canned(availability={}, services=GOOD_SERVICES))
    report = await hc.run_health_check(offsets=(2, 5, 9))
    assert report.exit_code == hc.EXIT_DRIFT
    assert any("EMPTY availability map" in d for d in report.drift)


async def test_healthcheck_without_credentials_is_a_config_error(monkeypatch):
    monkeypatch.delenv("LIBRO_PRIVATE_TOKEN", raising=False)
    monkeypatch.delenv("LIBRO_PRIVATE_EMAIL", raising=False)
    report = await hc.run_health_check(offsets=(2,))
    assert report.unreachable == ["__config__"]
    assert report.checks[0]["ok"] is False
    # The token must never appear in output, present or absent.
    assert "not-a-real-token" not in str(report.checks)


def test_healthcheck_never_prints_the_token(libro_env, capsys, monkeypatch):
    """A cron job's output lands in a mailbox and a log. The token cannot be in it."""
    monkeypatch.setattr(hc, "_get", _canned(availability=GOOD_AVAILABILITY,
                                            services=GOOD_SERVICES))
    monkeypatch.setattr(hc, "_load_env", lambda: None)
    code = hc.main(["--json", "--days", "2"])
    out = capsys.readouterr()
    assert code == hc.EXIT_OK
    assert "not-a-real-token" not in out.out + out.err


def test_healthcheck_quiet_mode_is_silent_when_healthy(libro_env, capsys, monkeypatch):
    """Cron mails whatever a job prints, so a healthy run must print nothing."""
    monkeypatch.setattr(hc, "_get", _canned(availability=GOOD_AVAILABILITY,
                                            services=GOOD_SERVICES))
    monkeypatch.setattr(hc, "_load_env", lambda: None)
    assert hc.main(["--quiet", "--days", "2"]) == hc.EXIT_OK
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(hc, "_get", _canned(status=401))
    assert hc.main(["--quiet", "--days", "2"]) == hc.EXIT_AUTH
    assert "FAILED" in capsys.readouterr().out


def test_healthcheck_is_read_only_by_construction():
    """Owner's standing rule: nothing may write to the live floor uninvited.

    The health check runs unattended on a cron, against the restaurant's real
    reservation book. A textual check is crude, but it is the one that survives
    someone editing this file six months from now without reading the docstring.
    """
    source = Path(hc.__file__).read_text()
    for verb in ('"POST"', "'POST'", '"PATCH"', "'PATCH'", '"PUT"', "'PUT'",
                 '"DELETE"', "'DELETE'", ".post(", ".patch(", ".put(", ".delete("):
        assert verb not in source, f"healthcheck.py must never {verb}"
    # And the only request helper it defines goes through client.get.
    assert "client.get(" in source
