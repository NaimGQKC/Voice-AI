"""Tests for the Concierge spoken-response orchestration (no LiveKit needed)."""

from __future__ import annotations

from resto_agent.concierge import Concierge
from resto_agent.reservation.mock import MockReservationService
from tests.conftest import future_date, slot_time


async def make_concierge(locale: str = "en") -> Concierge:
    svc = MockReservationService.in_process(db_path=":memory:")
    return Concierge(svc, locale=locale)


async def test_availability_speech_offers_times():
    c = await make_concierge()
    try:
        msg = await c.check_availability(date=future_date(), party_size=2)
        assert "PM" in msg or "AM" in msg
        assert "Which would you like" in msg
    finally:
        await c.service.aclose()


async def test_booking_flow_speech():
    c = await make_concierge()
    try:
        date = future_date()
        msg = await c.book_reservation(
            time=slot_time(date, "18:30"), party_size=2,
            first_name="Alex", phone="+15145551234",
        )
        assert "all set" in msg.lower()
        assert "Alex" in msg
    finally:
        await c.service.aclose()


async def test_cancel_without_phone_asks_for_it():
    c = await make_concierge()
    try:
        msg = await c.cancel_reservation()
        assert "phone number" in msg.lower()
    finally:
        await c.service.aclose()


async def test_cancel_unknown_phone_is_graceful():
    c = await make_concierge()
    try:
        msg = await c.cancel_reservation(phone="+15145550000")  # valid but unknown
        assert "don't see" in msg.lower()
    finally:
        await c.service.aclose()


async def test_natural_language_date_is_resolved():
    import datetime as dt
    from resto_agent.concierge import Concierge
    from resto_agent.reservation.mock import MockReservationService

    # Use the real today so the mock's past-slot filter agrees with the resolver;
    # "tomorrow" + dinner is always a valid future seating (dinner runs daily).
    today = dt.date.today()
    svc = MockReservationService.in_process(db_path=":memory:")
    c = Concierge(svc, today=today)
    try:
        msg = await c.check_availability(date="tomorrow", party_size=2, part_of_day="dinner")
        assert "PM" in msg  # resolved to a real date with dinner slots
        assert c.state.last_date == (today + dt.timedelta(days=1)).isoformat()
    finally:
        await svc.aclose()


async def test_past_date_is_refused_gracefully():
    import datetime as dt
    from resto_agent.concierge import Concierge
    from resto_agent.reservation.mock import MockReservationService

    svc = MockReservationService.in_process(db_path=":memory:")
    c = Concierge(svc, today=dt.date(2026, 7, 1))
    try:
        msg = await c.check_availability(date="2026-06-01", party_size=2)
        assert "passed" in msg.lower()
    finally:
        await svc.aclose()


async def test_phone_is_normalized_and_remembered():
    c = await make_concierge()
    try:
        date = future_date()
        # Give the phone in messy human format at booking.
        await c.book_reservation(
            time=slot_time(date, "19:00"), party_size=2,
            first_name="Riley", phone="(514) 555-7788",
        )
        assert c.state.phone == "+15145557788"
        # Cancel later WITHOUT repeating the number — state carries it.
        msg = await c.cancel_reservation()
        assert "cancelled" in msg.lower()
    finally:
        await c.service.aclose()


async def test_booking_without_any_phone_asks_for_it():
    c = await make_concierge()
    try:
        date = future_date()
        msg = await c.book_reservation(
            time=slot_time(date, "19:00"), party_size=2, first_name="NoPhone", phone="",
        )
        assert "phone number" in msg.lower()
    finally:
        await c.service.aclose()


async def test_lookup_and_cancel_by_phone():
    c = await make_concierge()
    try:
        date = future_date()
        await c.book_reservation(
            time=slot_time(date, "19:00"), party_size=4,
            first_name="Sam", phone="+15145557777",
        )
        found = await c.lookup_reservations(phone="+15145557777")
        assert "reservation for 4" in found
        cancelled = await c.cancel_reservation(phone="+15145557777")
        assert "cancelled" in cancelled.lower()
    finally:
        await c.service.aclose()


async def test_large_party_is_spoken_as_escalation():
    c = await make_concierge()
    try:
        msg = await c.check_availability(date=future_date(), party_size=40)
        # Should surface the friendly "we'll arrange with the team" message, not raise.
        assert "team" in msg.lower() and "raise" not in msg.lower()
        assert "name" in msg.lower() or "message" in msg.lower()
    finally:
        await c.service.aclose()


async def test_evening_synonym_narrows_to_dinner():
    """The model often passes "evening"/"tonight", not the literal word "dinner"."""
    c = await make_concierge()
    try:
        for phrase in ("evening", "tonight", "night"):
            msg = await c.check_availability(date=future_date(), party_size=2,
                                             part_of_day=phrase)
            assert "11:30 AM" not in msg, f"{phrase!r} should exclude lunch times"
            assert "PM" in msg
        # And "noon" narrows to lunch — on a weekday (no Sunday lunch service).
        import datetime as dt

        weekday = dt.date.today() + dt.timedelta(days=30)
        weekday += dt.timedelta(days=(1 - weekday.weekday()) % 7)  # next Tuesday
        msg = await c.check_availability(date=weekday.isoformat(), party_size=2,
                                         part_of_day="noon")
        assert "11:30 AM" in msg
    finally:
        await c.service.aclose()


async def test_unparseable_date_reuses_the_day_from_this_call():
    c = await make_concierge()
    try:
        await c.check_availability(date=future_date(), party_size=2)
        # A follow-up where the model sends something unparseable should not
        # re-ask for the date — it reuses the day already established.
        msg = await c.check_availability(date="that same day", party_size=4)
        assert "what day were you thinking" not in msg.lower()
    finally:
        await c.service.aclose()


async def test_merge_is_mentioned_for_large_party():
    c = await make_concierge()
    try:
        msg = await c.check_availability(date=future_date(), party_size=8)
        assert "combined table" in msg.lower()
    finally:
        await c.service.aclose()


async def test_booking_merged_table_is_announced():
    c = await make_concierge()
    try:
        date = future_date()
        msg = await c.book_reservation(
            time=slot_time(date, "19:00"), party_size=8,
            first_name="Group", phone="+15145552000",
        )
        assert "combine" in msg.lower()
    finally:
        await c.service.aclose()


async def test_faq_known_and_unknown():
    c = await make_concierge()
    try:
        assert "lunch" in c.answer_faq(topic="hours").lower()
        assert "message" in c.answer_faq(topic="does_not_exist").lower()
    finally:
        await c.service.aclose()


async def test_take_message_records():
    c = await make_concierge()
    try:
        msg = c.take_message(name="Chris", phone="+15145558888", message="party of 20")
        assert "Chris" in msg
        assert len(c.messages) == 1
        assert c.messages[0].body == "party of 20"
    finally:
        await c.service.aclose()


async def test_french_locale_booking_summary():
    c = await make_concierge(locale="fr")
    try:
        date = future_date()
        msg = await c.book_reservation(
            time=slot_time(date, "18:30"), party_size=2,
            first_name="Camille", phone="+15145559090",
        )
        # Concierge stores locale 'fr' on the booking; summary uses FR phrasing.
        assert "réservation pour 2" in msg
    finally:
        await c.service.aclose()


# ---------------------------------------------------------------------------
# Availability cascade: never leave a caller with nothing.
# Mirrors the failure the incumbent's data exposed — exact-time hits convert
# ~90-100%, alternatives ~36%, and zero-availability converted 0% because the
# system transferred instead of offering anything.
# ---------------------------------------------------------------------------

async def test_cascade_exact_time_is_offered_directly():
    c = await make_concierge()
    try:
        msg = await c.check_availability(date=future_date(), party_size=2,
                                         part_of_day="dinner", preferred_time="7")
        # "7" with no context must be read as 7 PM, and offered as a direct yes.
        assert "7:00 PM" in msg
        assert "shall i book" in msg.lower()
    finally:
        await c.service.aclose()


async def test_cascade_offers_near_alternatives_when_exact_is_taken():
    """Fill 7:00 PM, then ask for it: we should be offered nearby times."""
    c = await make_concierge()
    try:
        date = future_date()
        # Saturate the 19:00 slot so it drops out of availability.
        for i in range(4):
            await c.book_reservation(time=slot_time(date, "19:00"), party_size=6,
                                     first_name=f"F{i}", phone=f"+1514700000{i}")
        msg = await c.check_availability(date=date, party_size=6,
                                         part_of_day="dinner", preferred_time="7")
        assert "isn't open" in msg.lower() or "isn't available" in msg.lower()
        assert "PM" in msg          # concrete alternatives, not a brush-off
        assert "transfer" not in msg.lower()
    finally:
        await c.service.aclose()


async def test_cascade_offers_another_day_when_the_day_is_full(monkeypatch):
    """A completely full day must produce an adjacent-day offer, not a dead end."""
    c = await make_concierge()
    try:
        import datetime as dt
        full_day = (dt.date.today() + dt.timedelta(days=20)).isoformat()
        real = c.service.check_availability

        async def patched(date, party_size):
            if date == full_day:
                from resto_agent.reservation.models import Availability
                return Availability(date=date, party_size=party_size, slots=[])
            return await real(date, party_size)

        c.service.check_availability = patched
        msg = await c.check_availability(date=full_day, party_size=2,
                                         part_of_day="dinner")
        assert "fully booked" in msg.lower()
        assert "instead" in msg.lower()   # offered a different day
    finally:
        await c.service.aclose()


async def test_cascade_ends_in_waitlist_capture_not_a_transfer(monkeypatch):
    """When nothing is available anywhere, capture the caller — never dead-end."""
    c = await make_concierge()
    try:
        from resto_agent.reservation.models import Availability

        async def nothing(date, party_size):
            return Availability(date=date, party_size=party_size, slots=[])

        c.service.check_availability = nothing
        msg = await c.check_availability(date=future_date(), party_size=2)
        assert "call you back" in msg.lower()  # no auto-text exists
        assert c.state.pending_waitlist is True
        # And the caller can then be captured.
        out = c.join_waitlist(name="Dana", phone="514-555-0143", party_size=2)
        assert "Dana" in out and "call you back" in out.lower()
        assert len(c.waitlist) == 1
        assert c.waitlist[0].phone == "+15145550143"
        assert c.state.pending_waitlist is False
    finally:
        await c.service.aclose()
