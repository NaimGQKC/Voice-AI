"""Offline tests for the Libro private (dashboard API) adapter.

The live wire shapes are unconfirmed (see scripts/probe_libro_private.py), so
these cover what we can verify without the network: construction/auth, that the
class fully implements the ReservationService interface, and that the tolerant
parsers handle the plausible key spellings (dash-case / underscore / camelCase).
"""

from __future__ import annotations

import pytest

from resto_agent.reservation.base import ReservationService
from resto_agent.reservation.libro_private import LibroPrivateReservationService


def _svc() -> LibroPrivateReservationService:
    return LibroPrivateReservationService(
        token="fake-token", email="owner@example.com", restaurant_id="<redacted>"
    )


def test_requires_token_and_email():
    with pytest.raises(ValueError):
        LibroPrivateReservationService(token="", email="", restaurant_id="<redacted>")


def test_is_a_concrete_reservation_service():
    svc = _svc()
    assert isinstance(svc, ReservationService)  # all abstract methods implemented


def test_auth_header_format():
    svc = _svc()
    auth = svc._client.headers["Authorization"]
    assert auth == 'Token token="fake-token", email="owner@example.com"'
    # Default Accept is v1 (JSON:API endpoints); availabilities override to v2.
    assert svc._client.headers["Accept"] == "application/vnd.libro-private-v1+json"


def test_parse_booking_jsonapi():
    svc = _svc()
    # JSON:API envelope as returned by the real dashboard API.
    b = svc._parse_booking({"data": {
        "type": "bookings", "id": "555",
        "attributes": {"slots": 4, "status": "approved",
                       "time": "2026-08-15T19:00:00-04:00", "table-number": "12",
                       "note": "window"},
        "relationships": {
            "person": {"data": {"type": "people", "id": "99"}},
            "service": {"data": {"type": "services", "id": "7"}},
        },
    }})
    assert (b.id, b.size, b.status, b.person_id) == ("555", 4, "approved", "99")
    assert b.experience_id == "7"  # the service (shift) id
    assert b.tables == ("12",)
    assert b.time.startswith("2026-08-15")


def test_parse_booking_normalizes_canceled():
    svc = _svc()
    b = svc._parse_booking({"data": {
        "type": "bookings", "id": "7",
        "attributes": {"slots": 2, "status": "canceled",
                       "time": "2026-08-15T18:00:00-04:00"},
    }})
    assert b.status == "cancelled" and b.is_cancelled


def test_parse_person_jsonapi():
    svc = _svc()
    p = svc._parse_person({"data": {
        "type": "people", "id": "3",
        "attributes": {"first-name": "Alex", "last-name": "Kim",
                       "phone": "+15145551234", "email": "a@b.co"},
    }})
    assert p.first_name == "Alex" and p.last_name == "Kim"
    assert p.phone == "+15145551234" and p.id == "3"


async def test_availability_parses_nested_map(monkeypatch):
    """The real /availabilities/{date} returns time -> party-size -> {area: count}."""
    svc = _svc()

    async def fake_request(method, path, *, json=None, params=None, accept=None):
        assert method == "GET" and path == "/availabilities/2026-07-24"
        return {
            "2026-07-24T17:15:00-04:00": {"2": {"": 17}, "4": {"": 5}, "6": {"": 1}},
            "2026-07-24T17:45:00-04:00": {"2": {"": 14}, "4": {"": 4}, "6": {}},
        }

    monkeypatch.setattr(svc, "_request", fake_request)
    avail = await svc.check_availability("2026-07-24", 6)
    # Party of 6 is open at 17:15 (count 1) but full at 17:45 (empty {}).
    times = [s.time for s in avail.slots]
    assert times == ["2026-07-24T17:15:00-04:00"]
    assert avail.slots[0].label == "5:15 PM"
    await svc.aclose()


async def test_service_lookup_is_non_fatal(monkeypatch):
    """If /services fails, booking still proceeds (server infers the shift)."""
    from resto_agent.reservation.errors import BookingNotFoundError

    svc = _svc()

    async def boom(method, path, *, json=None, params=None, accept=None):
        raise BookingNotFoundError()

    monkeypatch.setattr(svc, "_request", boom)
    assert await svc._service_id_for_time("2026-07-24", "2026-07-24T19:30:00-04:00") == ""
    await svc.aclose()


async def test_service_id_requires_exact_slot_match(monkeypatch):
    """A service IS a 15-min slot: match started-at exactly, ignoring status.

    Ground truth from a captured 201 create: the booking referenced the service
    whose started-at equalled the reservation time, and that service was marked
    "closed".
    """
    svc = _svc()

    async def fake(method, path, *, json=None, params=None, accept=None):
        assert path == "/services"
        return {"data": [
            {"id": "S1130", "type": "services",
             "attributes": {"status": "opened", "started-at": "2026-07-24T15:30:00Z"}},
            {"id": "S1545", "type": "services",   # 'closed' but still bookable
             "attributes": {"status": "closed", "started-at": "2026-07-24T19:45:00Z"}},
        ]}

    monkeypatch.setattr(svc, "_request", fake)
    # 11:30 EDT == 15:30Z -> exact match
    assert await svc._service_id_for_time("2026-07-24", "2026-07-24T11:30:00-04:00") == "S1130"
    # 15:45 EDT == 19:45Z -> matches the "closed" service (staff bookings)
    assert await svc._service_id_for_time("2026-07-24", "2026-07-24T15:45:00-04:00") == "S1545"
    # No service at that instant -> no id (caller raises SlotUnavailable)
    assert await svc._service_id_for_time("2026-07-24", "2026-07-24T13:07:00-04:00") == ""
    await svc.aclose()


async def test_create_booking_without_matching_service_raises(monkeypatch):
    from resto_agent.reservation.errors import SlotUnavailableError

    svc = _svc()

    async def fake(method, path, *, json=None, params=None, accept=None):
        if path == "/people/query":
            return {"data": [{"id": "P1", "type": "people"}]}
        return {"data": []}  # no services -> no slot

    monkeypatch.setattr(svc, "_request", fake)
    with pytest.raises(SlotUnavailableError):
        await svc.create_booking(time="2026-09-08T11:30:00-04:00", party_size=2,
                                 first_name="ZZ", phone="+15145550199")
    await svc.aclose()


async def test_create_booking_sends_expected_leave_at(monkeypatch):
    """The create payload conveys the datetime as expected-leave-at (start+turn),
    references person + service, and does NOT send a `time` attribute."""
    svc = _svc()
    captured: dict = {}

    async def fake(method, path, *, json=None, params=None, accept=None):
        if path == "/people/query":
            return {"data": [{"id": "P1", "type": "people"}]}
        if path == "/services":
            # 11:30 EDT == 15:30Z — the slot the booking asks for.
            return {"data": [{"id": "S1", "type": "services",
                              "attributes": {"status": "opened",
                                             "started-at": "2026-09-08T15:30:00Z"}}]}
        if path == "/bookings" and method == "POST":
            captured["json"] = json
            return {"data": {"type": "bookings", "id": "B1",
                             "attributes": {"slots": 2, "status": "approved"},
                             "relationships": {"person": {"data": {"id": "P1"}},
                                               "service": {"data": {"id": "S1"}}}}}
        return {}

    monkeypatch.setattr(svc, "_request", fake)
    await svc.create_booking(time="2026-09-08T11:30:00-04:00", party_size=2,
                             first_name="ZZ", phone="+15145550199")
    data = captured["json"]["data"]
    attrs = data["attributes"]
    assert data["type"] == "bookings"
    # `time` is server-derived from the service and must never be sent.
    assert "time" not in attrs
    # 11:30 EDT + 90min turn -> 13:00 EDT == 17:00Z, dashboard's exact format.
    assert attrs["expected-leave-at"] == "2026-09-08T17:00:00.000Z"
    assert attrs["slots"] == 2 and attrs["status"] == "approved"
    assert attrs["booking-type"] == "reservation"
    assert attrs["quoted-wait-time"] == 900
    rels = data["relationships"]
    assert rels["service"]["data"]["id"] == "S1"
    assert rels["person"]["data"]["id"] == "P1"
    assert rels["restaurant"]["data"] == {"type": "restaurants", "id": "<redacted>"}
    await svc.aclose()


async def test_availability_large_party_escalates():
    from resto_agent.reservation.errors import LargePartyError

    svc = _svc()
    with pytest.raises(LargePartyError):
        await svc.check_availability("2026-07-24", 8)  # >6 = staff
    await svc.aclose()


def test_build_service_selects_private_backend(monkeypatch):
    from resto_agent.config import Settings
    from resto_agent.reservation import build_service

    s = Settings(
        reservation_backend="libro-private",
        libro_private_token="fake", libro_private_email="owner@example.com",
        libro_private_restaurant_id="<redacted>",
    )
    svc = build_service(s)
    assert isinstance(svc, LibroPrivateReservationService)


def test_build_service_private_without_token_errors():
    from resto_agent.config import Settings
    from resto_agent.reservation import build_service

    s = Settings(reservation_backend="libro-private", libro_private_token="",
                 libro_private_email="")
    with pytest.raises(ValueError):
        build_service(s)


# ---------------------------------------------------------------------------
# The live-booking alarm
# ---------------------------------------------------------------------------
# LIVE_BOOKINGS_TO_DELETE.txt is the only thing standing between a test booking
# and a table the venue's staff cannot sell. It used to fire on every `pytest` run,
# which is worse than not having it: three mock rows in that file taught us to
# scroll past exactly the warning we built it to notice.


def test_alarm_is_quiet_for_the_mock_and_for_fake_transports():
    """Only a real dispatch at Libro's host counts as touching the floor."""
    import httpx
    from resto_agent.reservation.libro_private import _reaches_libro

    async def _handler(request):  # pragma: no cover - never dispatched
        return httpx.Response(200, json={})

    # The mock backend: right transport, wrong host.
    assert not _reaches_libro(httpx.AsyncClient(base_url="http://127.0.0.1:8000"))
    # The resilience tests: right host, but nothing leaves the machine.
    assert not _reaches_libro(httpx.AsyncClient(
        base_url="https://api.libroreserve.com",
        transport=httpx.MockTransport(_handler)))
    # The real thing, and only the real thing.
    assert _reaches_libro(httpx.AsyncClient(base_url="https://api.libroreserve.com"))


async def test_a_mocked_booking_never_arms_the_alarm(monkeypatch):
    """A booking made with `_request` patched out did not happen at Libro."""
    svc = _svc()

    async def fake(method, path, *, json=None, params=None, accept=None):
        if path == "/people/query":
            return {"data": [{"id": "P1", "type": "people"}]}
        if path == "/services":
            return {"data": [{"id": "S1", "type": "services",
                              "attributes": {"status": "opened",
                                             "started-at": "2026-09-08T15:30:00Z"}}]}
        if path == "/bookings" and method == "POST":
            return {"data": {"type": "bookings", "id": "B1",
                             "attributes": {"slots": 2, "status": "approved"},
                             "relationships": {"person": {"data": {"id": "P1"}},
                                               "service": {"data": {"id": "S1"}}}}}
        return {}

    monkeypatch.setattr(svc, "_request", fake)
    await svc.create_booking(time="2026-09-08T11:30:00-04:00", party_size=2,
                             first_name="ZZ", phone="+15145550199")
    assert svc._touched_libro is False
    await svc.aclose()
