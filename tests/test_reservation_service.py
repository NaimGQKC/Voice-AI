"""End-to-end tests of the ReservationService against the in-process mock.

These exercise the full JSON:API client -> mock -> SQLite path, the same code
that will run against real Libro (only base URL + auth differ).
"""

from __future__ import annotations

import pytest

from resto_agent.reservation.errors import (
    LargePartyError,
    NotCancelableError,
    PartySizeOutOfRangeError,
    SlotUnavailableError,
)
from resto_agent.reservation.models import Availability, Booking
from tests.conftest import future_date, slot_time


async def test_check_availability_returns_slots(service):
    avail = await service.check_availability(future_date(), 2)
    assert isinstance(avail, Availability)
    assert avail.is_available
    assert all(s.time and s.label and s.experience_name for s in avail.slots)
    # Labels are human-spoken, e.g. "6:00 PM".
    assert any(s.label.endswith("PM") for s in avail.slots)


async def test_zero_party_size_raises_2005(service):
    with pytest.raises(PartySizeOutOfRangeError) as exc:
        await service.check_availability(future_date(), 0)
    assert exc.value.code == "2005"


async def test_large_party_raises_2006(service):
    with pytest.raises(LargePartyError) as exc:
        await service.check_availability(future_date(), 30)
    assert exc.value.code == "2006"


async def test_book_lookup_reschedule_cancel_roundtrip(service):
    date = future_date()
    booking = await service.create_booking(
        time=slot_time(date, "18:30"),
        party_size=2,
        first_name="Jordan",
        phone="+15145551111",
        note="window seat",
    )
    assert isinstance(booking, Booking)
    assert booking.status == "confirmed"
    assert booking.size == 2
    assert booking.experience_name == "Dinner"
    assert len(booking.tables) == 1  # a 2-top fits without merging

    # Lookup by phone finds it.
    found = await service.list_bookings(phone="+15145551111")
    assert [b.id for b in found] == [booking.id]

    # Reschedule to another open time.
    moved = await service.reschedule_booking(booking.id, new_time=slot_time(date, "20:00"))
    assert moved.time == slot_time(date, "20:00")

    # Cancel it.
    cancelled = await service.cancel_booking(booking.id)
    assert cancelled.is_cancelled

    # Re-cancelling is not allowed.
    with pytest.raises(NotCancelableError):
        await service.cancel_booking(booking.id)


async def test_book_unavailable_time_raises(service):
    with pytest.raises(SlotUnavailableError):
        await service.create_booking(
            time=slot_time(future_date(), "02:00"),  # not a seating
            party_size=2,
            first_name="Pat",
            phone="+15145552222",
        )


async def test_large_party_books_a_merged_table(service):
    """A party of 8 has no single table, so the engine combines two 4-tops."""
    date = future_date()
    booking = await service.create_booking(
        time=slot_time(date, "19:00"), party_size=8,
        first_name="Group", phone="+15145558000",
    )
    assert booking.is_merged
    assert len(booking.tables) == 2


async def test_capacity_exhaustion_by_arrangement(service):
    """Only three arrangements can seat 6 at one time; the 4th party must fail.

    With a 2-table merge cap, a party of 6 fits via: the window 6-top, the mid
    group (4+4), or the back group (4+2). The front group (three 2-tops) can't
    reach 6 in two tables, so a 4th simultaneous party of 6 has nowhere to go.
    """
    date = future_date()
    time = slot_time(date, "19:00")
    for i in range(3):
        b = await service.create_booking(
            time=time, party_size=6, first_name=f"Six{i}", phone=f"+1514666000{i}"
        )
        assert b.status == "confirmed"
    with pytest.raises(SlotUnavailableError):
        await service.create_booking(
            time=time, party_size=6, first_name="Overflow", phone="+15146669999"
        )


async def test_turn_time_blocks_overlapping_slot_only(service):
    """A 7 PM booking of the only 8-arrangement blocks overlapping times, not earlier ones."""
    date = future_date()
    await service.create_booking(
        time=slot_time(date, "19:00"), party_size=8,
        first_name="Eight", phone="+15145558888",
    )
    avail8 = await service.check_availability(date, 8)
    times = [s.time for s in avail8.slots]
    # 7:30 overlaps the 7:00-8:45 turn -> gone; 5:00 (ends 6:45) does not -> still open.
    assert slot_time(date, "19:30") not in times
    assert slot_time(date, "17:00") in times


async def test_person_create_and_update(service):
    date = future_date()
    booking = await service.create_booking(
        time=slot_time(date, "19:00"), party_size=2,
        first_name="Robin", phone="+15145553333", email="robin@example.com",
    )
    person = await service.get_person(booking.person_id)
    assert person.phone == "+15145553333"
    updated = await service.update_person(person.id, last_name="Lee")
    assert updated.last_name == "Lee"


async def test_payment_intent(service):
    date = future_date()
    booking = await service.create_booking(
        time=slot_time(date, "18:00"), party_size=2,
        first_name="Max", phone="+15145554444",
    )
    intent = await service.init_payment_intent(booking_id=booking.id, amount=5000)
    assert intent.amount == 5000
    assert intent.currency == "CAD"
    assert intent.payment_url
