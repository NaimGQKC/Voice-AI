"""Concierge: provider-agnostic reservation orchestration.

This is the brain behind the agent's tools, kept free of any LiveKit imports so
it is fully unit-testable. Each method returns a short string suitable for the
voice agent to speak, and converts backend errors into graceful spoken
responses. ``tools.py`` is a thin LiveKit wrapper over these methods.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

from . import datetime_resolve, faq
from .notify import Notifier
from .phone import normalize_phone, spoken_phone
from .store import CallStore
from .reservation import (
    Booking,
    ModificationRestrictedError,
    ReservationError,
    ReservationService,
)
from .reservation.errors import BackendUnavailableError

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """A message taken for the restaurant team (escalation / overflow)."""

    name: str
    phone: str
    body: str


@dataclass
class WaitlistEntry:
    """A caller we couldn't seat — captured instead of being dumped on a human.

    This is the whole point of the availability cascade: a "we're fully booked"
    call is a lead, not a dead end.
    """

    name: str
    phone: str
    date: str
    party_size: int
    preferred_time: str = ""


#: How far from the requested time we'll offer alternatives before widening.
NEAR_MISS_MINUTES = 30
#: How many alternative times to speak. More than two is unusable on a phone.
MAX_ALTERNATIVES = 2
#: How many days either side to search when a whole day is unavailable.
ADJACENT_DAYS = (1, 2)

#: Spoken when we can't even read the guest's existing reservation. Says nothing
#: about whether it exists — because we don't know.
_DEGRADED_LOOKUP = (
    "I can't reach our reservation system right now, so I can't pull up your "
    "booking. I've taken your details and the team will call you right back to "
    "sort it out."
)


@dataclass
class CallState:
    """What we've learned during this call, so the agent doesn't re-ask.

    Mirrors LiveKit's UserData pattern: collected fields persist across tool
    calls within a single conversation.
    """

    name: str = ""
    phone: str = ""  # normalized E.164
    party_size: int = 0
    last_date: str = ""  # YYYY-MM-DD
    pending_waitlist: bool = False
    #: Set once Libro has failed to answer during this call. It changes what the
    #: agent is allowed to promise: "we'll text you when a table opens" is a
    #: waitlist promise and needs a working booking system to be true. When the
    #: backend is down the only true promise is "a human will call you back".
    backend_degraded: bool = False


def _booking_summary(b: Booking, *, locale: str = "en") -> str:
    when = _spoken_time(b.time)
    exp = f" ({b.experience_name})" if b.experience_name else ""
    if locale == "fr":
        return f"une réservation pour {b.size} le {when}{exp}"
    return f"a reservation for {b.size} on {when}{exp}"


_MONTHS = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]


def _spoken_date(iso_date: str) -> str:
    """Render '2026-06-28' as 'June 28'."""
    try:
        month, day = int(iso_date[5:7]), int(iso_date[8:10])
    except (ValueError, IndexError):
        return iso_date
    return f"{_MONTHS[month - 1]} {day}"


def _spoken_time(iso_time: str) -> str:
    """Render '2026-06-28T18:30:00-04:00' as 'June 28 at 6:30 PM'."""
    try:
        hour, minute = int(iso_time[11:13]), iso_time[14:16]
    except (ValueError, IndexError):
        return iso_time
    suffix = "AM" if hour < 12 else "PM"
    return f"{_spoken_date(iso_time)} at {hour % 12 or 12}:{minute} {suffix}"


def _spoken_weekday(iso_date: str) -> str:
    """Render '2026-06-28' as 'Sunday the 28th' — clearer than a bare date on a call."""
    try:
        d = dt.date.fromisoformat(iso_date)
    except ValueError:
        return iso_date
    return f"{d.strftime('%A')} the {d.day}"


def _hhmm(iso_time: str) -> tuple[int, int] | None:
    try:
        return int(iso_time[11:13]), int(iso_time[14:16])
    except (ValueError, IndexError):
        return None


def _clock(hm: tuple[int, int]) -> str:
    hour, minute = hm
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def _speak_list(items: list[str]) -> str:
    """Join for speech: 'a', 'a and b', 'a, b and c'."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _nearest(slots, wanted: tuple[int, int], *, within: int, prefer_later: bool):
    """The closest open slots to `wanted`, nearest first, capped at MAX_ALTERNATIVES.

    Ties break toward *later* at dinner: a caller who asked for 7 is generally
    happier at 7:30 than 6:30 (they have plans before, not after).
    """
    target = wanted[0] * 60 + wanted[1]
    scored = []
    for s in slots:
        hm = _hhmm(s.time)
        if hm is None:
            continue
        delta = (hm[0] * 60 + hm[1]) - target
        if abs(delta) <= within:
            # Nudge earlier options behind later ones when preferring later.
            bias = 1 if (prefer_later and delta < 0) else 0
            scored.append(((abs(delta), bias), s))
    scored.sort(key=lambda x: x[0])
    return [s for _, s in scored[:MAX_ALTERNATIVES]]


class Concierge:
    def __init__(
        self,
        service: ReservationService,
        *,
        locale: str = "en",
        today: dt.date | None = None,
        store: CallStore | None = None,
        notifier: Notifier | None = None,
        call_id: str = "",
    ):
        self.service = service
        self.locale = locale
        self.today = today or dt.date.today()
        self.state = CallState()
        self.call_id = call_id or uuid.uuid4().hex[:12]

        # Durable record. Optional so unit tests stay in-memory and fast, but in
        # production this is what makes "I've passed your message on" true.
        self.store = store
        self.notifier = notifier

        # Kept for tests and the demo. NOT the source of truth: a Concierge is
        # created per call, so anything only in here dies when the caller hangs
        # up. That was the bug; `self.store` is the fix.
        self.messages: list[Message] = []
        self.waitlist: list[WaitlistEntry] = []

    def _persist(self, *, kind: str, name: str, phone: str, body: str = "",
                 party_size: int = 0, wanted_date: str = "",
                 wanted_time: str = "") -> bool:
        """Commit a message/waitlist capture, then try to alert the restaurant.

        Returns True only if the record is durable. The caller MUST NOT speak a
        confirmation when this returns False — that is the whole point of the
        module. Delivery failure does *not* make this False: the row is saved and
        will show up in `pending_messages()`.
        """
        if self.store is None:
            return True  # no store configured (tests/demo): nothing promised

        try:
            row_id = self.store.record_message(
                call_id=self.call_id, kind=kind, name=name, phone=phone, body=body,
                party_size=party_size, wanted_date=wanted_date,
                wanted_time=wanted_time,
            )
        except Exception:
            logger.exception("Could not persist %s for call %s", kind, self.call_id)
            return False

        if self.notifier is not None:
            subject = ("Waitlist request" if kind == "waitlist"
                       else "Message from a caller")
            detail = body or f"party of {party_size} {wanted_date} {wanted_time}".strip()
            err = self.notifier.send(subject, f"{name} ({phone}) - {detail}")
            try:
                self.store.mark_delivered(row_id, error=err)
            except Exception:
                logger.exception("Could not update delivery state for row %s", row_id)
        return True

    # -- graceful degradation ----------------------------------------------
    def _degrade(self, *, spoken: str, what: str, name: str = "", phone: str = "",
                 party_size: int = 0, wanted_date: str = "", wanted_time: str = "",
                 spoken_no_phone: str = "") -> str:
        """Libro is unreachable. Capture the caller instead of losing them.

        This is the single most important path in the file, because it is the
        one that runs when the thing most likely to break, breaks: a
        reverse-engineered private API behind a long-lived token, on a system
        nobody is watching.

        Three rules, in order:

        1. **Never claim an outcome we don't have.** We do not know whether the
           table is free, booked, or cancelled — so we don't say.
        2. **Get the caller into durable storage.** A name and a number in
           SQLite is a callback the restaurant can make. A caller who hung up
           after dead air is gone, and nobody will ever know they rang.
        3. **If we can't even store it, say so and give them the number.** An
           honest dead end beats a promise we can't keep — same rule as
           ``take_message``.

        ``spoken`` is the caller-facing line for the success case; it belongs to
        the calling method because "I couldn't book that" and "I couldn't cancel
        that" have very different consequences for the guest. ``spoken_no_phone``
        is for the same reason: when we have no number to capture we still have
        to tell a caller their cancellation did *not* happen.
        """
        self.state.backend_degraded = True
        name = name or self.state.name
        phone = phone or self.state.phone
        if not phone:
            # Nothing to capture yet — ask for the one thing that makes the
            # caller recoverable, and let the LLM route the answer to a capture
            # tool (join_waitlist / take_message).
            self.state.pending_waitlist = True
            return spoken_no_phone or (
                "I'm having trouble reaching our reservation system right now, and "
                "I don't want to tell you something I can't confirm. Can I take your "
                "name and number so the team can call you straight back?"
            )
        if not self._persist(kind="callback", name=name, phone=phone, body=what,
                             party_size=party_size, wanted_date=wanted_date,
                             wanted_time=wanted_time):
            return (
                "I'm sorry — our reservation system isn't responding and I can't "
                "save your details on my end either. I don't want to promise a "
                f"callback I can't deliver, so please call us directly at {faq.PHONE}."
            )
        self.state.name = name or self.state.name
        self.state.phone = phone
        return spoken

    def _log_booking(self, *, ok: bool, booking_id: str = "", party_size: int = 0,
                     wanted_time: str = "", name: str = "", phone: str = "",
                     error: str = "") -> None:
        """Record a booking attempt. Never raises — logging must not break a call."""
        if self.store is None:
            return
        try:
            self.store.record_booking_attempt(
                call_id=self.call_id, ok=ok, booking_id=booking_id,
                party_size=party_size, wanted_time=wanted_time, name=name,
                phone=phone, error=error,
            )
        except Exception:
            logger.exception("Could not log booking attempt for call %s", self.call_id)

    # -- availability ------------------------------------------------------
    async def check_availability(
        self, *, date: str, party_size: int, part_of_day: str = "",
        preferred_time: str = "",
    ) -> str:
        resolved = datetime_resolve.resolve_date(date, today=self.today)
        if resolved is None and self.state.last_date:
            # Reuse the day already established in this call rather than re-asking.
            resolved = datetime_resolve.resolve_date(self.state.last_date,
                                                     today=self.today)
        if resolved is None:
            return (
                "I want to get the date right — what day were you thinking? "
                "You can say something like 'this Friday' or a date."
            )
        horizon = datetime_resolve.validate_horizon(resolved, today=self.today)
        if horizon == "past":
            return "That date has already passed — what upcoming day works for you?"
        if horizon == "too_far":
            return "That's further out than we take reservations. Could you pick a nearer date?"

        iso_date = resolved.isoformat()
        # Normalize whatever the model passed ("evening", "tonight", "7pm", ...)
        # into lunch/dinner; fall back to inferring it from the date phrase.
        want = (part_of_day or "").strip().lower()
        if want not in ("lunch", "dinner"):
            want = (datetime_resolve.infer_part_of_day(want)
                    or datetime_resolve.infer_part_of_day(date))
        part_of_day = want
        self.state.party_size = party_size or self.state.party_size
        self.state.last_date = iso_date

        try:
            availability = await self.service.check_availability(iso_date, party_size)
        except BackendUnavailableError as exc:
            # We cannot see the book. Saying "we're fully booked" would send away
            # a guest the restaurant may well have room for, and the cascade
            # below would just time out four more times doing it. Stop here and
            # capture instead.
            logger.warning("Availability unavailable for call %s: %s", self.call_id, exc)
            return self._degrade(
                what=(f"Availability check failed — party of {party_size} on {iso_date}"
                      f"{' around ' + preferred_time if preferred_time else ''}. "
                      "Reservation system unreachable; call the guest back."),
                party_size=party_size, wanted_date=iso_date,
                wanted_time=preferred_time,
                spoken=(
                    "I'm having trouble reaching our reservation system this second, "
                    "so I can't see what's open. I've got your details and the team "
                    "will call you right back about that table. Sorry about that."
                ),
            )
        except ReservationError as exc:
            return exc.spoken_message

        slots = list(availability.slots)
        if part_of_day in ("lunch", "dinner"):
            filtered = [s for s in slots if s.experience_name.lower() == part_of_day]
            if filtered:
                slots = filtered

        # ---- the cascade: never leave a caller with nothing ----------------
        wanted = datetime_resolve.resolve_time(preferred_time) if preferred_time else None

        if slots and wanted is not None:
            exact = [s for s in slots if _hhmm(s.time) == wanted]
            if exact:
                self.state.last_date = iso_date
                return (f"Yes — {exact[0].label} on {_spoken_date(iso_date)} is available "
                        f"for {party_size}. Shall I book that?")
            near = _nearest(slots, wanted, within=NEAR_MISS_MINUTES,
                            prefer_later=(part_of_day == "dinner"))
            if near:
                return (f"{_clock(wanted)} isn't open, but I have "
                        f"{_speak_list([s.label for s in near])} on "
                        f"{_spoken_date(iso_date)}. Would either of those work?")
            # Nothing close — widen to the rest of that day before giving up.
            spread = _nearest(slots, wanted, within=24 * 60,
                              prefer_later=(part_of_day == "dinner"))
            if spread:
                return (f"{_clock(wanted)} isn't available. The closest I have that day "
                        f"is {_speak_list([s.label for s in spread])}. Would one of those work?")

        if slots:
            labels = [s.label for s in slots]
            shown = labels[:MAX_ALTERNATIVES + 1]
            merged = any(s.is_merged for s in slots)
            merge_note = (f" For a party of {party_size} we'd set up a combined table."
                          if merged else "")
            return (f"For {party_size} on {_spoken_date(iso_date)} I have "
                    f"{_speak_list(shown)}.{merge_note} Which would you like?")

        # ---- nothing that day: try adjacent days ---------------------------
        alt = await self._adjacent_day(resolved, party_size, part_of_day)
        if alt:
            alt_date, alt_slots = alt
            return (f"We're fully booked on {_spoken_date(iso_date)}. I do have "
                    f"{_speak_list([s.label for s in alt_slots[:MAX_ALTERNATIVES]])} on "
                    f"{_spoken_weekday(alt_date)} instead — would that work?")

        # ---- nothing at all: capture the caller, never dead-end ------------
        self.state.pending_waitlist = True
        # NOT "we'll text you the moment something opens up." Nothing texts them.
        # Libro has no future-dated waitlist at all (its waitlist is a same-day
        # walk-in queue with no date or time field, and the button is disabled on
        # every day except today), and we have not built an opening-watcher. The
        # only true promise is that a person will see this and call back.
        return ("I'm sorry, we're fully booked then, and the days around it are too. "
                "I can take your name and number and have someone call you back if "
                "something opens up — would you like me to do that?")

    async def _adjacent_day(self, day: dt.date, party_size: int, part_of_day: str):
        """Look ±1 then ±2 days for an open table. Returns (date, slots) or None."""
        for offset in ADJACENT_DAYS:
            for delta in (offset, -offset):
                candidate = day + dt.timedelta(days=delta)
                if datetime_resolve.validate_horizon(candidate, today=self.today):
                    continue
                try:
                    avail = await self.service.check_availability(
                        candidate.isoformat(), party_size)
                except BackendUnavailableError:
                    # The backend is down, not this date. Trying four more dates
                    # just buys the caller four more timeouts of silence.
                    return None
                except ReservationError:
                    continue
                found = list(avail.slots)
                if part_of_day in ("lunch", "dinner"):
                    same = [s for s in found if s.experience_name.lower() == part_of_day]
                    found = same or found
                if found:
                    return candidate.isoformat(), found
        return None

    # -- takeout -----------------------------------------------------------
    def handle_takeout(self, *, name: str = "", phone: str = "",
                       order: str = "", wants_callback: bool = True) -> str:
        """Capture a takeout request instead of dead-ending the caller.

        Roughly **one call in six** at this venue is someone trying to order food
        by phone (8 transfers tagged `takeout` plus ~6 more who hung up after
        being told to use the website, out of 84 calls). The incumbent had no
        takeout path at all, so its only moves were "go to the website" or "let
        me transfer you" — and the transcripts show callers hanging up on both.

        We can't take payment or fire an order to the kitchen. But we *can* take
        the order details and the caller's number durably, and put them in front
        of staff immediately — which beats both of the incumbent's options,
        because the caller gets a callback instead of a dial tone.
        """
        if not wants_callback:
            return (
                "No problem — you can order online at any time, and it goes "
                "straight to the kitchen. Anything else I can help with?"
            )

        normalized = normalize_phone(phone) or self.state.phone
        if not normalized:
            return ("I can have someone call you right back to take that order — "
                    "what's the best number for you?")

        if not self._persist(kind="takeout", name=name or self.state.name,
                             phone=normalized, body=order or "takeout order"):
            return (
                "I'm sorry — I can't save that on my end right now. You can order "
                f"online, or reach the restaurant directly at {faq.PHONE}."
            )

        self.state.phone = normalized
        if name:
            self.state.name = name
        return (
            f"Got it{', ' + name if name else ''} — I've sent your order details "
            "to the restaurant and someone will call you right back to confirm it "
            "and take payment. Anything else in the meantime?"
        )

    # -- waitlist ----------------------------------------------------------
    def join_waitlist(self, *, name: str, phone: str, date: str = "",
                      party_size: int = 0, preferred_time: str = "") -> str:
        """Capture a caller we couldn't seat, instead of transferring them."""
        normalized = normalize_phone(phone) or self.state.phone
        if not normalized:
            return "What's the best number to reach you on?"
        resolved = datetime_resolve.resolve_date(date, today=self.today) if date else None
        entry = WaitlistEntry(
            name=name or self.state.name,
            phone=normalized,
            date=resolved.isoformat() if resolved else self.state.last_date,
            party_size=party_size or self.state.party_size,
            preferred_time=preferred_time,
        )
        # "We'll text you the moment a table opens" is a *waitlist* promise, and
        # it needs a working booking system to ever come true. If Libro went dark
        # earlier in this call we don't have a waitlist — we have a caller owed a
        # phone call. Record it as such so the owner sees the right task.
        degraded = self.state.backend_degraded
        if not self._persist(kind="callback" if degraded else "waitlist",
                             name=entry.name, phone=normalized,
                             body=("Reservation system was unreachable during the call"
                                   if degraded else ""),
                             party_size=entry.party_size, wanted_date=entry.date,
                             wanted_time=entry.preferred_time):
            return (
                "I'm sorry — I'm having trouble saving that on my end, and I'd "
                "rather not promise a callback I can't guarantee. Could you try us "
                f"again shortly, or call {faq.PHONE}?"
            )

        self.waitlist.append(entry)
        self.state.name = entry.name
        self.state.phone = normalized
        self.state.pending_waitlist = False
        if degraded:
            return (f"Thanks {entry.name} — I've got your details, and the team will "
                    f"call you back at {spoken_phone(normalized)} to sort the table "
                    "out as soon as our system is back. Anything else I can help with?")
        # See the note in check_availability: no automated text exists, so this
        # says what actually happens — the request is recorded and a human calls.
        return (f"Perfect, thanks {entry.name} — I've put your name down, and the "
                f"team will call you back at {spoken_phone(normalized)} if something "
                "opens up. Anything else I can help with?")

    # -- booking -----------------------------------------------------------
    async def book_reservation(
        self,
        *,
        time: str,
        party_size: int,
        first_name: str,
        phone: str,
        last_name: str = "",
        email: str = "",
        note: str = "",
    ) -> str:
        normalized = normalize_phone(phone) or self.state.phone
        if not normalized:
            return (
                "I'll just need a phone number for the reservation — what's the "
                "best number to reach you?"
            )
        try:
            booking = await self.service.create_booking(
                time=time,
                party_size=party_size,
                first_name=first_name,
                last_name=last_name,
                phone=normalized,
                email=email,
                note=note,
                locale=self.locale,
            )
        except BackendUnavailableError as exc:
            # Libro didn't answer. We do NOT know whether a table was taken, so
            # we say nothing about the reservation and capture the caller.
            self._log_booking(ok=False, party_size=party_size, wanted_time=time,
                              name=first_name, phone=normalized,
                              error=f"{type(exc).__name__}: {exc}")
            return self._degraded_booking(exc, time=time, party_size=party_size,
                                          first_name=first_name, phone=normalized)
        except ReservationError as exc:
            # A real answer from a working backend ("that slot is gone"). Log it
            # so the owner can see that a caller wanted a table and didn't get
            # one — silent failures are the ones that cost money — and speak the
            # honest reason.
            self._log_booking(ok=False, party_size=party_size, wanted_time=time,
                              name=first_name, phone=normalized,
                              error=f"{type(exc).__name__}: {exc}")
            return exc.spoken_message
        except Exception as exc:
            # Anything unforeseen — a parse error, a plugin blowing up, a bug we
            # haven't found. It must NOT propagate: an exception out of a tool
            # ends the caller's turn in silence, which is the worst outcome
            # available. Treat an unknown failure exactly like an outage.
            logger.exception("Unexpected failure booking for call %s", self.call_id)
            self._log_booking(ok=False, party_size=party_size, wanted_time=time,
                              name=first_name, phone=normalized,
                              error=f"{type(exc).__name__}: {exc}")
            return self._degraded_booking(exc, time=time, party_size=party_size,
                                          first_name=first_name, phone=normalized)

        self._log_booking(ok=True, booking_id=booking.id, party_size=party_size,
                          wanted_time=time, name=first_name, phone=normalized)

        # Remember for the rest of the call.
        self.state.name = first_name or self.state.name
        self.state.phone = normalized
        self.state.party_size = party_size
        self.state.last_date = booking.time[:10] or self.state.last_date

        combined = (
            " We'll combine a couple of tables for your group." if booking.is_merged else ""
        )
        return (
            f"You're all set — {_booking_summary(booking, locale=self.locale)}, "
            f"under {first_name}.{combined} Is there anything else I can help with?"
        )

    def _degraded_booking(self, exc: Exception, *, time: str, party_size: int,
                          first_name: str, phone: str) -> str:
        """Spoken fallback when a booking could not be completed or confirmed.

        Note what this deliberately does *not* say: not "you're booked", and not
        "we're full". Either would be a guess. The caller leaves the call with a
        real commitment (a human will ring them) and the restaurant gets a row
        in ``booking_attempts`` plus a ``callback`` message it can action.
        """
        when = _spoken_time(time)
        return self._degrade(
            what=(f"COULD NOT CONFIRM BOOKING — party of {party_size} for {when}. "
                  f"Reservation system unreachable ({type(exc).__name__}). "
                  "Call the guest back and confirm or make the booking."),
            name=first_name,
            phone=phone,
            party_size=party_size,
            wanted_date=time[:10],
            wanted_time=time,
            spoken=(
                "I'm having trouble reaching our reservation system right now, so I "
                "can't confirm that table — and I don't want to tell you it's done "
                f"when I can't see it. I do have your details, a table for "
                f"{party_size} on {when}, and the team will call you right back to "
                "confirm it. Sorry about that."
            ),
        )

    # -- lookup ------------------------------------------------------------
    async def lookup_reservations(self, *, phone: str) -> str:
        normalized = normalize_phone(phone) or self.state.phone
        if not normalized:
            return "What's the phone number the reservation is under?"
        self.state.phone = normalized
        try:
            bookings = await self.service.list_bookings(phone=normalized)
        except BackendUnavailableError:
            # "I don't see any reservations" during an outage is how a guest
            # with a confirmed table gets told they don't have one.
            return self._degrade(
                what="Reservation lookup failed (system unreachable); guest asked "
                     "about an existing booking. Call them back.",
                phone=normalized,
                spoken=(
                    "I can't reach our reservation system at the moment, so I can't "
                    "pull that up — and I don't want to tell you it isn't there when "
                    "I simply can't see it. I've noted your number and the team will "
                    "call you right back."
                ),
            )
        except ReservationError as exc:
            return exc.spoken_message

        active = [b for b in bookings if not b.is_cancelled]
        if not active:
            return (
                "I don't see any active reservations under that number. "
                "Would you like to make one?"
            )
        if len(active) == 1:
            return f"I found {_booking_summary(active[0])}. How can I help with it?"
        summaries = "; ".join(_booking_summary(b) for b in active[:3])
        return f"I found a few: {summaries}. Which one did you mean?"

    # -- cancel ------------------------------------------------------------
    async def cancel_reservation(self, *, booking_id: str = "", phone: str = "") -> str:
        booking = await self._resolve_single(booking_id=booking_id, phone=phone)
        if isinstance(booking, str):  # an error/clarification message
            return booking
        if booking.modification_restricted:
            return ModificationRestrictedError().spoken_message
        try:
            cancelled = await self.service.cancel_booking(booking.id)
        except BackendUnavailableError:
            # A cancel we couldn't perform must never sound like a cancel we
            # did. If the guest believes it's cancelled and it isn't, the
            # restaurant holds an empty table on a full night.
            return self._degrade(
                what=(f"CANCELLATION NOT COMPLETED — booking {booking.id} "
                      f"({_booking_summary(booking)}). System unreachable; the "
                      "reservation is still live. Cancel it and confirm with the guest."),
                phone=self.state.phone,
                spoken=(
                    "I couldn't reach our reservation system to cancel that, so please "
                    "assume it's still booked for now. I've flagged it and the team "
                    "will take care of it and confirm with you."
                ),
                spoken_no_phone=(
                    "I couldn't reach our reservation system to cancel that, so please "
                    "assume it's still booked for now. What's the best number for the "
                    "team to reach you on once it's done?"
                ),
            )
        except ReservationError as exc:
            return exc.spoken_message
        return (
            f"Done — I've cancelled {_booking_summary(cancelled)}. "
            "Is there anything else?"
        )

    # -- reschedule --------------------------------------------------------
    async def reschedule_reservation(
        self, *, new_time: str, booking_id: str = "", phone: str = ""
    ) -> str:
        booking = await self._resolve_single(booking_id=booking_id, phone=phone)
        if isinstance(booking, str):
            return booking
        if booking.modification_restricted:
            return ModificationRestrictedError().spoken_message
        try:
            updated = await self.service.reschedule_booking(
                booking.id, new_time=new_time
            )
        except BackendUnavailableError:
            return self._degrade(
                what=(f"RESCHEDULE NOT COMPLETED — booking {booking.id} to "
                      f"{new_time}. System unreachable; the original time still "
                      "stands. Move it and confirm with the guest."),
                phone=self.state.phone, wanted_time=new_time,
                wanted_date=new_time[:10],
                spoken=(
                    "I couldn't reach our reservation system to move that, so your "
                    "original time still stands for now. I've flagged it and the team "
                    "will change it and confirm with you."
                ),
                spoken_no_phone=(
                    "I couldn't reach our reservation system to move that, so your "
                    "original time still stands for now. What's the best number for "
                    "the team to reach you on once it's changed?"
                ),
            )
        except ReservationError as exc:
            return exc.spoken_message
        return (
            f"All changed — your reservation is now {_spoken_time(updated.time)}. "
            "Anything else?"
        )

    # -- FAQ ---------------------------------------------------------------
    def answer_faq(self, *, topic: str) -> str:
        ans = faq.answer(topic, locale=self.locale)
        if ans:
            return ans
        return (
            "I'm not sure about that one, but I'd be happy to take a message so "
            "the team can get back to you."
        )

    # -- message taking ----------------------------------------------------
    def take_message(self, *, name: str, phone: str, message: str) -> str:
        normalized = normalize_phone(phone) or self.state.phone
        if not normalized:
            return "What's the best number for the team to reach you on?"

        if not self._persist(kind="message", name=name, phone=normalized,
                             body=message):
            # Never claim we passed it on when we couldn't save it. Say something
            # true and give them a way to reach a human themselves.
            return (
                "I'm sorry — I'm having trouble saving that on my end, and I don't "
                "want to promise something I can't deliver. Could you call us back "
                f"in a few minutes, or reach us at {faq.PHONE}?"
            )

        self.messages.append(Message(name=name, phone=normalized, body=message))
        self.state.name = name or self.state.name
        self.state.phone = normalized
        return (
            f"Got it, {name} — I've passed your message to the team and they'll "
            "get back to you. Is there anything else?"
        )

    # -- helpers -----------------------------------------------------------
    async def _resolve_single(self, *, booking_id: str = "", phone: str = ""):
        """Return a single Booking, or a spoken clarification/error string."""
        if booking_id:
            try:
                return await self.service.get_booking(booking_id)
            except BackendUnavailableError:
                return self._degrade(
                    what=f"Could not read booking {booking_id} (system unreachable). "
                         "Guest wanted to change or cancel it; call them back.",
                    spoken=_DEGRADED_LOOKUP,
                )
            except ReservationError as exc:
                return exc.spoken_message
        normalized = normalize_phone(phone) or self.state.phone
        if normalized:
            self.state.phone = normalized
            try:
                bookings = [
                    b for b in await self.service.list_bookings(phone=normalized)
                    if not b.is_cancelled
                ]
            except BackendUnavailableError:
                return self._degrade(
                    what="Could not look up the guest's booking (system unreachable). "
                         "They wanted to change or cancel it; call them back.",
                    phone=normalized, spoken=_DEGRADED_LOOKUP,
                )
            except ReservationError as exc:
                return exc.spoken_message
            if not bookings:
                return "I don't see an active reservation under that number."
            if len(bookings) > 1:
                summaries = "; ".join(_booking_summary(b) for b in bookings[:3])
                return (
                    f"You have more than one: {summaries}. "
                    "Which one would you like to change?"
                )
            return bookings[0]
        return "Could you give me the phone number the reservation is under?"
