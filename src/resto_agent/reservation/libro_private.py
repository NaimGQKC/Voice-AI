"""LibroPrivateReservationService — the real Libro dashboard API (id <redacted>).

Wire format confirmed from live dashboard traffic (24 Jul 2026), not guesses:

  Base:    https://api.libroreserve.com
  Accept:  application/vnd.libro-private-v1+json  (JSON:API endpoints)
           application/vnd.libro-private-v2+json  (/availabilities only)
  Content-Type on writes: application/vnd.api+json
  Auth:    Authorization: Token token="<TOKEN>", email="<EMAIL>"

  GET  /availabilities/{YYYY-MM-DD}?restaurant-id=<redacted>     bookable slots (nested map)
  GET  /services?restaurant-id=<redacted>&started-on={date}      shifts + capacity (JSON:API)
  GET  /people/query?query={text}                          guest autocomplete
  GET/POST /people                                         guest CRUD (JSON:API)
  POST /bookings                                            create reservation (JSON:API)
  GET/PATCH /bookings/{id}                                 read / update / cancel

Availability response is a bare map, e.g.:
  { "2026-07-24T19:30:00-04:00": { "1": {"": 17}, "4": {"": 5}, "6": {} }, ... }
  time -> party-size(str) -> { seatingArea: count }.  Empty {} = full for that size.

A booking's datetime is the `time` attribute (matches the availability keys),
party size is `slots`, and it references a `person` and a `service` (shift) by
relationship. Keys are dash-cased JSON:API.

The exact required-field set / status enum for POST /bookings is the one piece
still derived rather than observed — kept minimal here (time, slots, source,
person, service) and confirmed by a single controlled test booking.

Security: the token is a master credential (env only, never logged/committed).
This is an undocumented internal API and may change without notice.

Resilience: because this is a reverse-engineered private API behind a long-lived
token, "Libro stopped answering" is the most likely unattended failure, not a
hypothetical one. Two rules encode that (see the timeout block below):

  * every request is bounded by a timeout sized for a live phone call, and
  * only GET is ever retried — never a booking create.

Transport failures and 5xx surface as :class:`BackendUnavailableError` (or
:class:`BookingOutcomeUnknownError` for an in-flight write) so the concierge can
capture the caller instead of inventing a reason to turn them away.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time as _time

import httpx

from .base import ReservationService
from .errors import (
    BackendAuthError,
    BackendUnavailableError,
    BookingNotFoundError,
    BookingOutcomeUnknownError,
    LargePartyError,
    ReservationError,
    SlotUnavailableError,
)
from .models import Availability, Booking, PaymentIntent, Person, TimeSlot

logger = logging.getLogger(__name__)

# The API is versioned per-endpoint via the Accept header (confirmed from the
# dashboard's own request headers): availabilities use v2, every JSON:API
# endpoint (people/services/notes/bookings/...) uses v1, and writes send the
# standard JSON:API content type.
ACCEPT_V1 = "application/vnd.libro-private-v1+json"   # default (JSON:API endpoints)
ACCEPT_V2 = "application/vnd.libro-private-v2+json"   # /availabilities only
WRITE_CONTENT_TYPE = "application/vnd.api+json"
#: **6, and this is a hard API ceiling — not a policy choice.**
#:
#: Direct probe of GET /availabilities/{date} across 28 days (731 slots, 4,386
#: cells): the party-size key set was ALWAYS exactly {1,2,3,4,5,6}. Forcing
#: `&size=7` returns HTTP 200 with no "7" key in the body. Libro cannot express a
#: party of seven, so there is nothing to check availability against and nothing
#: to book.
#:
#: The incumbent's ">7 → transfer" rule is therefore NOT the binding constraint,
#: and the venue's knowledge base line about holding tables 2h for "parties of 7+"
#: describes a MANUAL floor process with no representation in the booking system.
#: Anything above 6 is a human handoff by design, not a limitation to engineer
#: around. Setting this to 7 makes the agent tell a party of seven "we're fully
#: booked" (empty availability) instead of "let me get someone to arrange that".
MAX_ONLINE_PARTY = 6
#: Table turn length, from the venue's stated booking policy: 1h30 for parties
#: under 6, 2h for 7+. The create conveys this via `expected-leave-at` (start +
#: turn) and the server derives the start time from it; 90 min matches a captured
#: live booking exactly (start 19:45Z, leave 21:15Z).
DEFAULT_TURN_MIN = 90
LARGE_PARTY_TURN_MIN = 120
LARGE_PARTY_THRESHOLD = 6

# ---------------------------------------------------------------------------
# Timeouts and retries: this runs while a human is holding a phone.
# ---------------------------------------------------------------------------
# A hung socket is strictly worse than an error. An error gets a spoken
# response in under a second; a hang gets dead air, and dead air on a phone call
# is indistinguishable from a dropped call. The caller hangs up and we lose them
# without even a name — the one outcome this system exists to prevent.
#
# The previous setting was a single blanket 15s. Nobody waits 15 seconds in
# silence for a restaurant to answer "is 7pm free?". These numbers come from what
# a caller will actually tolerate, not from what the server might need:
#
#   ~1s   normal: the agent covers it with "let me check that for you"
#   ~2-3s noticeable pause, still fine
#   ~5s   the caller says "hello? are you there?"
#   ~8s+  the caller assumes the line dropped
#
# So the ceiling on any single conversational turn is ~8s of silence, and every
# number below is derived from spending that budget:

#: TCP+TLS handshake. If we can't get a socket in 3s the network is broken, and
#: waiting longer only converts a fast failure into dead air. Cheap to retry.
CONNECT_TIMEOUT_S = 3.0

#: Response wait for a **read** (availability, services, people lookup). 4s, so
#: that connect+read+one retry still lands inside the ~8s tolerance ceiling.
READ_TIMEOUT_S = 4.0

#: Response wait for a **write** (POST /bookings, POST /people). Deliberately
#: longer — writes are never retried (see below), so this single attempt gets
#: the whole budget, and giving up early on a create that would have succeeded
#: is the expensive mistake. 8s is the caller's outright patience limit, and the
#: agent has just said "let me get that booked for you", which buys a moment.
WRITE_READ_TIMEOUT_S = 8.0

#: Time to push our (tiny) request body out, and to wait for a pooled
#: connection. Both should be instant; these only exist so neither can hang.
SEND_TIMEOUT_S = 3.0
POOL_TIMEOUT_S = 2.0

#: Retries are for **GET only**, and exactly one of them.
#:
#: Why one: a retry pays for itself against a single dropped packet or a
#: recycled connection, which is the common transient failure. A second retry
#: only helps if the backend is genuinely down, in which case we should be
#: degrading to a human callback rather than making the caller listen to us try
#: again. Two attempts also keeps the worst case inside the silence budget.
#:
#: Why GET only — this is the safety-critical half. ``POST /bookings`` has no
#: idempotency key on this API. If it times out we do not know whether Libro
#: seated the guest, so re-sending it can put one caller at two tables on a
#: Saturday night, which is worse for the restaurant than not booking at all.
#: The same reasoning covers ``POST /people`` (duplicate guest records) and
#: PATCH. So: no non-GET request is ever retried, at any level, ever.
GET_MAX_ATTEMPTS = 2
RETRY_BACKOFF_S = 0.25

#: Hard ceiling on a retried GET including backoff. Belt-and-braces: even if
#: every individual timeout somehow stacks, we stop asking at this point rather
#: than let the caller sit in silence. Checked *before* sleeping, so a slow first
#: attempt simply means no retry happens at all.
RETRY_BUDGET_S = 9.0

#: Statuses that mean "the backend is having a moment", not "your request was
#: wrong". Retryable on a GET; surfaced as BackendUnavailableError otherwise.
#: 429 is included because Libro's dashboard API is undocumented and we would
#: rather back off once than hammer it.
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


def turn_minutes(party_size: int) -> int:
    """Table hold time for a party (venue policy: 1h30 under 6, 2h for 7+)."""
    return LARGE_PARTY_TURN_MIN if party_size > LARGE_PARTY_THRESHOLD else DEFAULT_TURN_MIN

#: Attributes the dashboard sends on a booking PATCH (everything else — `time`,
#: `size`, `source`, lifecycle timestamps — is server-derived/read-only).
_WRITABLE_BOOKING_ATTRS = frozenset({
    "slots", "status", "status-tags", "seating-status", "booking-type",
    "table-number", "note", "private-note", "children", "edit-url", "tags",
    "reduced-mobility", "expected-leave-at", "booking-experience-id",
    "group-number", "group-name", "group-size", "classification-counts",
    "answers", "do-not-move", "deposit-amount", "deposit-charged",
    "deposit-token", "no-show-fee-status", "no-show-fee-status-waived",
    "offer-request-status", "payment-data", "quoted-wait-time",
    "quoted-wait-time-overridden-at",
})


def _slot_label(iso_time: str) -> str:
    try:
        hour, minute = int(iso_time[11:13]), iso_time[14:16]
    except (ValueError, IndexError):
        return iso_time
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute} {suffix}"


def _get(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return default


#: Every real booking is written to this file as one line, immediately, in
#: addition to the console banner. A banner scrolls off; a file does not, and if
#: the process dies before anyone reads the terminal the record still exists.
LIVE_BOOKINGS_LOG = "LIVE_BOOKINGS_TO_DELETE.txt"

#: The only host where a booking is real. Anything else is the mock.
LIBRO_HOST = "api.libroreserve.com"

DASHBOARD_URL = "https://dashboard.libroreserve.com/restaurants/{rid}/reservations"


def _reaches_libro(client: httpx.AsyncClient) -> bool:
    """True only if a write on this client would actually land on the venue's floor.

    Two things have to hold. The host must be Libro's — this adapter is also
    pointed at the local mock. And the transport must be a real one: the test
    suite keeps the real base URL and swaps in a ``MockTransport``, so the URL
    on its own is not proof that anything left the machine.
    """
    if LIBRO_HOST not in str(client.base_url):
        return False
    return not isinstance(getattr(client, "_transport", None), httpx.MockTransport)


def _announce_live_booking(booking, restaurant_id: str) -> None:
    """Shout about a booking that now exists on the restaurant's real floor.

    This is a REAL table at a REAL restaurant. A test booking left behind is a
    table their staff cannot sell and a guest who never arrives, so it has to be
    impossible to create one without noticing. Never raises — announcing must not
    be able to fail a booking that already succeeded.
    """
    try:
        url = DASHBOARD_URL.format(rid=restaurant_id)
        banner = (
            "\n" + "!" * 78 +
            "\n!!  A REAL BOOKING NOW EXISTS ON THE VENUE'S FLOOR — DELETE IT WHEN DONE" +
            f"\n!!  booking id : {booking.id}" +
            f"\n!!  time       : {booking.time}" +
            f"\n!!  party      : {booking.size}" +
            f"\n!!  cancel     : python scripts/verify_booking.py --id {booking.id}" +
            f"\n!!  check      : {url}" +
            "\n" + "!" * 78 + "\n"
        )
        print(banner, flush=True)
        logger.warning("LIVE BOOKING CREATED id=%s time=%s size=%s — delete when done",
                       booking.id, booking.time, booking.size)
        with open(LIVE_BOOKINGS_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}"
                     f"\tid={booking.id}\ttime={booking.time}\tsize={booking.size}"
                     f"\tDELETE_AT={url}\n")
    except Exception:
        logger.exception("could not announce live booking (the booking DID succeed)")


class LibroPrivateReservationService(ReservationService):
    def __init__(
        self,
        *,
        token: str,
        email: str,
        restaurant_id: str,
        base_url: str = f"https://{LIBRO_HOST}",
        timeout: float | httpx.Timeout | None = None,
    ):
        if not token or not email:
            raise ValueError(
                "LIBRO_PRIVATE_TOKEN and LIBRO_PRIVATE_EMAIL must be set for the "
                "libro-private backend."
            )
        self._restaurant_id = str(restaurant_id)
        #: Has any request on this adapter actually reached Libro? Gates the
        #: live-booking alarm — see `_reaches_libro`.
        self._touched_libro = False
        # Two timeout profiles, because a read and a write have different costs
        # of failure on a live call. See the constants above for the reasoning.
        if timeout is None:
            self._read_timeout = httpx.Timeout(
                connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S,
                write=SEND_TIMEOUT_S, pool=POOL_TIMEOUT_S,
            )
            self._write_timeout = httpx.Timeout(
                connect=CONNECT_TIMEOUT_S, read=WRITE_READ_TIMEOUT_S,
                write=SEND_TIMEOUT_S, pool=POOL_TIMEOUT_S,
            )
        else:  # explicit override (tests, probes) applies to everything
            self._read_timeout = self._write_timeout = httpx.Timeout(timeout)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=self._read_timeout,
            headers={
                "Accept": ACCEPT_V1,  # default; availabilities override to v2
                "Authorization": f'Token token="{token}", email="{email}"',
            },
        )

    # -- low-level ---------------------------------------------------------
    def _unreachable(self, method: str, path: str, what: str,
                     exc: Exception) -> BackendUnavailableError:
        """Classify a transport failure — and decide whether it is *ambiguous*.

        A failed GET is unambiguous: nothing changed, say so and move on. A
        failed ``POST /bookings`` is not — the request may have landed. That
        distinction is the difference between "sorry, try another time" and
        "I can't confirm that, someone will call you back", so it is typed.
        """
        where = f"{method} {path}"
        # Never include the exception's repr blindly anywhere near headers; the
        # message below carries no credentials (httpx errors quote the URL only).
        detail = f"{type(exc).__name__}: {exc}"
        if method.upper() != "GET":
            return BookingOutcomeUnknownError(f"{where} {what}", detail=detail)
        return BackendUnavailableError(f"{where} {what}", detail=detail)

    def _decode(self, resp: httpx.Response, method: str, path: str):
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400:
            where = f"{method} {path}"
            snippet = str(body)[:180].replace("\n", " ")
            if resp.status_code in (401, 403):
                # The long-lived token died or was rotated. Nothing was created:
                # a rejected request never reached the booking logic.
                raise BackendAuthError(f"HTTP {resp.status_code} at {where}",
                                       detail=snippet)
            if resp.status_code == 404:
                raise BookingNotFoundError(f"404 Not Found at {where}", detail=snippet)
            if resp.status_code in (409, 422):
                # A slot can fill between the availability check and the create.
                raise SlotUnavailableError(f"HTTP {resp.status_code} at {where}",
                                           detail=snippet)
            if resp.status_code >= 500:
                # Libro is broken, we are not. Degrade, don't invent a reason.
                raise BackendUnavailableError(f"HTTP {resp.status_code} at {where}",
                                              detail=snippet)
            raise ReservationError(f"HTTP {resp.status_code} at {where}", detail=snippet)
        return body if body is not None else {}

    async def _request(self, method: str, path: str, *, json: dict | None = None,
                       params: dict | None = None, accept: str | None = None):
        # restaurant-id is NOT auto-injected: the dashboard only sends it on
        # /availabilities, /services, /notes — adding it elsewhere can 404.
        headers: dict = {}
        if accept:
            headers["Accept"] = accept
        if json is not None:
            headers["Content-Type"] = WRITE_CONTENT_TYPE

        # GET is the only method safe to repeat. See GET_MAX_ATTEMPTS.
        is_get = method.upper() == "GET"
        attempts = GET_MAX_ATTEMPTS if is_get else 1
        timeout = self._read_timeout if is_get else self._write_timeout
        started = _time.monotonic()

        # Set here, and nowhere else, because dispatching through a real
        # transport at Libro's host is the only thing that proves we can touch
        # the restaurant's floor. Tests either patch this method out or swap in
        # a MockTransport, so neither ever trips the alarm below.
        if _reaches_libro(self._client):
            self._touched_libro = True

        last: BackendUnavailableError | None = None
        for attempt in range(1, attempts + 1):
            try:
                resp = await self._client.request(
                    method, path, json=json, params=params, headers=headers,
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                last = self._unreachable(method, path, "timed out", exc)
            except httpx.HTTPError as exc:  # connect/DNS/protocol/pool failures
                last = self._unreachable(method, path, "could not be reached", exc)
            else:
                if resp.status_code not in RETRYABLE_STATUS_CODES:
                    return self._decode(resp, method, path)
                # A 502/504 on a write is ambiguous in exactly the way a timeout
                # is: an intermediary gave up, but the origin may still have
                # processed it. Type it as unknown rather than guess "failed" —
                # the recovery (a human callback) is safe either way, and
                # asserting failure invites the caller to rebook a table they
                # may already have.
                cls = (BackendUnavailableError if is_get
                       else BookingOutcomeUnknownError)
                last = cls(f"HTTP {resp.status_code} at {method} {path}",
                           detail=str(resp.text)[:180].replace("\n", " "))

            if attempt >= attempts:
                break
            # Budget check happens *before* the sleep, so a slow first attempt
            # simply means we don't retry rather than blowing past the ceiling.
            if _time.monotonic() - started + RETRY_BACKOFF_S > RETRY_BUDGET_S:
                logger.warning("Libro %s %s failed and the retry budget is spent",
                               method, path)
                break
            logger.warning("Libro %s %s failed (%s); retrying once", method, path, last)
            await asyncio.sleep(RETRY_BACKOFF_S)

        assert last is not None  # the loop only exits here after a failure
        raise last

    # -- parsing -----------------------------------------------------------
    def _parse_booking(self, raw) -> Booking:
        r = raw.get("data", raw) if isinstance(raw, dict) else {}
        attrs = r.get("attributes", {}) or {}
        rel = r.get("relationships", {}) or {}

        def _rel_id(name: str) -> str:
            data = (rel.get(name) or {}).get("data") or {}
            return str(data.get("id", "")) if isinstance(data, dict) else ""

        status = str(attrs.get("status", "") or "").lower()
        table = attrs.get("table-number") or ""
        return Booking(
            id=str(r.get("id", "")),
            size=int(attrs.get("slots", 0) or 0),
            status="cancelled" if status in ("canceled", "cancelled") else (status or "confirmed"),
            time=str(attrs.get("time", "") or ""),
            restaurant_id=self._restaurant_id,
            person_id=_rel_id("person"),
            experience_id=_rel_id("service"),
            note=str(attrs.get("note", "") or ""),
            locale=str(attrs.get("locale", "en") or "en"),
            tables=tuple(t for t in str(table).split(",") if t),
        )

    @staticmethod
    def _parse_person(raw) -> Person:
        r = raw.get("data", raw) if isinstance(raw, dict) else {}
        attrs = r.get("attributes", {}) or {}
        return Person(
            id=str(r.get("id", "")),
            first_name=str(attrs.get("first-name", "") or ""),
            last_name=str(attrs.get("last-name", "") or ""),
            phone=str(attrs.get("phone", "") or ""),
            email=str(attrs.get("email", "") or ""),
        )

    # -- availability ------------------------------------------------------
    async def check_availability(self, date: str, party_size: int) -> Availability:
        if party_size > MAX_ONLINE_PARTY:
            raise LargePartyError()
        body = await self._request("GET", f"/availabilities/{date}",
                                   params={"restaurant-id": self._restaurant_id},
                                   accept=ACCEPT_V2)
        slots: list[TimeSlot] = []
        if isinstance(body, dict):
            for ts, size_map in body.items():
                if not isinstance(size_map, dict):
                    continue
                area = size_map.get(str(party_size))
                count = 0
                if isinstance(area, dict):
                    count = sum(int(v) for v in area.values() if isinstance(v, (int, float)))
                if count > 0:
                    slots.append(TimeSlot(
                        time=ts, label=_slot_label(ts),
                        experience_id="", experience_name="", seats=party_size,
                    ))
        slots.sort(key=lambda s: s.time)
        return Availability(date=date, party_size=party_size, slots=slots)

    async def _service_id_for_time(self, date: str, time: str) -> str:
        """Return the service whose start EXACTLY matches ``time``.

        Ground truth (from a captured 201 create): a "service" is one 15-minute
        seating slot, not a shift — the day returns ~39 of them. The booking's
        datetime is derived from the referenced service's ``started-at`` (a live
        booking at 19:45Z referenced the service started-at 19:45Z, and `time` is
        never sent). Referencing the wrong one yields 422 code 1006
        "You must select a date & time".

        Status is deliberately NOT filtered: that live booking used a service
        marked "closed" (staff may book outside online hours).

        ``date`` is the restaurant-local date; slots may cross into the next UTC
        day, which the API handles.
        """
        want = _parse_dt(time)
        if want is None:
            return ""
        try:
            body = await self._request("GET", "/services", params={
                "restaurant-id": self._restaurant_id, "started-on": date,
                "only-services": "true",
            })
        except BackendUnavailableError:
            # Deliberately NOT swallowed. Returning "" here makes create_booking
            # raise SlotUnavailableError, i.e. the agent tells a caller "that
            # time isn't available" when the truth is "we couldn't look". That
            # sends a paying guest away for a reason we invented.
            raise
        except ReservationError:
            return ""
        for s in (body.get("data", []) if isinstance(body, dict) else []):
            started = _parse_dt((s.get("attributes") or {}).get("started-at", ""))
            if started is not None and started == want:
                return str(s.get("id", ""))
        return ""

    # -- guest -------------------------------------------------------------
    async def _find_or_create_person(self, *, first_name, last_name, phone, email,
                                     locale="en") -> str:
        if phone:
            res = await self._request("GET", "/people/query", params={"query": phone})
            people = res.get("data", []) if isinstance(res, dict) else (res or [])
            for p in people:
                pid = p.get("id") if isinstance(p, dict) else None
                if pid:
                    return str(pid)
        payload = {"data": {"type": "people", "attributes": {
            "first-name": first_name, "last-name": last_name,
            "phone": phone, "phone-country": "CA", "phone-type": "mobile",
            "email": email, "locale": locale,
        }}}
        body = await self._request("POST", "/people", json=payload)
        return self._parse_person(body).id

    # -- bookings ----------------------------------------------------------
    async def create_booking(
        self, *, time: str, party_size: int, first_name: str, last_name: str = "",
        phone: str = "", email: str = "", note: str = "", locale: str = "en",
        experience_id: str = "",
    ) -> Booking:
        if party_size > MAX_ONLINE_PARTY:
            raise LargePartyError()
        person_id = await self._find_or_create_person(
            first_name=first_name, last_name=last_name, phone=phone,
            email=email, locale=locale,
        )
        service_id = experience_id or await self._service_id_for_time(time[:10], time)
        if not service_id:
            # The service *is* the slot; without it the server has no date/time.
            raise SlotUnavailableError(
                f"No seating slot (service) at {time}",
                detail="No service record matches that exact start time.",
            )
        start = _parse_dt(time)
        leave_iso = (
            (start + dt.timedelta(minutes=turn_minutes(party_size)))
            .strftime("%Y-%m-%dT%H:%M:%S.000Z")
            if start else time
        )
        # Mirror the dashboard's captured create payload field-for-field. `time`
        # is server-derived (from the service) and must not be sent.
        attributes = {
            "slots": party_size,
            "status": "approved",
            "status-tags": "",
            "seating-status": "",
            "booking-type": "reservation",
            "table-number": "",
            "note": note or "",
            "private-note": "",
            "children": False,
            "edit-url": None,
            "tags": [],
            "reduced-mobility": False,
            "expected-leave-at": leave_iso,
            "booking-experience-id": None,
            "group-number": None,
            "group-name": None,
            "group-size": None,
            "answers": [],
            "do-not-move": False,
            "deposit-amount": 0,
            "deposit-charged": False,
            "deposit-token": None,
            "no-show-fee-status": "",
            "no-show-fee-status-waived": "false",
            "offer-request-status": None,
            "payment-data": None,
            "quoted-wait-time": 900,
            "quoted-wait-time-overridden-at": None,
        }
        payload = {"data": {
            "type": "bookings",
            "attributes": attributes,
            "relationships": {
                "service": {"data": {"type": "services", "id": service_id}},
                "person": {"data": {"type": "people", "id": person_id}},
                "restaurant": {"data": {"type": "restaurants",
                                        "id": self._restaurant_id}},
            },
        }}
        body = await self._request("POST", "/bookings", json=payload)
        booking = self._parse_booking(body)
        # An alarm that goes off during `pytest` is an alarm everyone learns to
        # scroll past, which defeats the point of having one.
        if self._touched_libro:
            _announce_live_booking(booking, self._restaurant_id)
        return booking

    async def get_booking(self, booking_id: str) -> Booking:
        return self._parse_booking(await self._request("GET", f"/bookings/{booking_id}"))

    async def list_bookings(self, *, phone: str = "", person_id: str = "") -> list[Booking]:
        """Best-effort: the dashboard capture didn't include a list-by-guest call,
        so resolve the person and read their included bookings; empty on failure."""
        if not person_id and phone:
            res = await self._request("GET", "/people/query", params={"query": phone})
            people = res.get("data", []) if isinstance(res, dict) else (res or [])
            person_id = str(people[0].get("id", "")) if people else ""
        if not person_id:
            return []
        try:
            body = await self._request("GET", f"/people/{person_id}",
                                       params={"include": "bookings"})
        except BackendUnavailableError:
            # An empty list here means "you have no reservation" to the caller.
            # During an outage that is false, and it is the kind of false that
            # ends with a guest arriving to no table. Let it surface.
            raise
        except ReservationError:
            return []
        included = body.get("included", []) if isinstance(body, dict) else []
        return [self._parse_booking({"data": r}) for r in included
                if isinstance(r, dict) and r.get("type") == "bookings"]

    async def update_booking(self, booking_id: str, *, party_size: int | None = None,
                             note: str | None = None) -> Booking:
        attrs: dict = {}
        if party_size is not None:
            attrs["slots"] = party_size
        if note is not None:
            attrs["note"] = note
        return await self._patch_booking(booking_id, attrs=attrs)

    async def _patch_booking(self, booking_id: str, *, attrs: dict,
                             relationships: dict | None = None) -> Booking:
        """Echo the booking back with changes applied, as the dashboard does.

        The dashboard PATCHes the record's full attribute set, so we read the
        current booking and resend the writable fields with our changes merged —
        avoiding any chance of blanking server-side state with a partial update.
        """
        current: dict = {}
        try:
            got = await self._request("GET", f"/bookings/{booking_id}")
            current = (got.get("data", {}) or {}) if isinstance(got, dict) else {}
        except BackendUnavailableError:
            # Without the read we would PATCH a near-empty attribute set over a
            # live booking. Refuse rather than risk blanking a real guest's
            # record; the concierge degrades to a human callback.
            raise
        except ReservationError:
            current = {}
        merged = {k: v for k, v in (current.get("attributes") or {}).items()
                  if k in _WRITABLE_BOOKING_ATTRS}
        merged.update(attrs)

        rels = {k: v for k, v in (current.get("relationships") or {}).items()
                if k in ("service", "person", "restaurant")}
        rels.update(relationships or {})
        rels.setdefault("restaurant", {"data": {"type": "restaurants",
                                                "id": self._restaurant_id}})

        payload = {"data": {"type": "bookings", "id": str(booking_id),
                            "attributes": merged, "relationships": rels}}
        return self._parse_booking(
            await self._request("PATCH", f"/bookings/{booking_id}", json=payload))

    async def cancel_booking(self, booking_id: str) -> Booking:
        return await self._patch_booking(booking_id, attrs={"status": "canceled"})

    async def reschedule_booking(self, booking_id: str, *, new_time: str) -> Booking:
        # The service *is* the slot, so moving a booking means swapping services.
        service_id = await self._service_id_for_time(new_time[:10], new_time)
        if not service_id:
            raise SlotUnavailableError(f"No seating slot (service) at {new_time}")
        start = _parse_dt(new_time)
        attrs = {}
        if start:
            # Party size is unchanged on a reschedule; use the stored size if known.
            attrs["expected-leave-at"] = (
                start + dt.timedelta(minutes=DEFAULT_TURN_MIN)
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return await self._patch_booking(
            booking_id, attrs=attrs,
            relationships={"service": {"data": {"type": "services", "id": service_id}}},
        )

    # -- people ------------------------------------------------------------
    async def get_person(self, person_id: str) -> Person:
        return self._parse_person(await self._request("GET", f"/people/{person_id}"))

    async def update_person(self, person_id: str, *, first_name=None, last_name=None,
                            phone=None, email=None) -> Person:
        attrs = {}
        if first_name is not None:
            attrs["first-name"] = first_name
        if last_name is not None:
            attrs["last-name"] = last_name
        if phone is not None:
            attrs["phone"] = phone
        if email is not None:
            attrs["email"] = email
        payload = {"data": {"type": "people", "id": str(person_id), "attributes": attrs}}
        return self._parse_person(
            await self._request("PATCH", f"/people/{person_id}", json=payload))

    async def init_payment_intent(self, *, booking_id: str, amount: int,
                                  currency: str = "CAD") -> PaymentIntent:
        raise ReservationError(
            "Deposits are not supported via the private API adapter yet.",
            spoken_message=(
                "I can't set up a deposit over the phone yet — I'll note it and "
                "the team will follow up if one is needed."
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_dt(value: str) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp (handles the trailing 'Z') into aware UTC."""
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)
