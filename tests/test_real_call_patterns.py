"""Regression tests derived from 84 REAL calls to this venue (Jun 30 - Jul 25 2026).

Source: the incumbent system's own call log for the venue, exported read-only. Each test
below encodes a failure or success that actually happened to a real caller, so the
numbers in the docstrings are measured, not estimated.

The headline: of 84 calls, only 13 (15%) produced a booking or a modification, and
**36 (43%) ended in a transfer to a human**. These tests target the biggest
categories of that 43%.
"""

from __future__ import annotations

import pytest

from resto_agent.concierge import Concierge
from resto_agent.reservation.mock import MockReservationService
from resto_agent.store import CallStore
from tests.conftest import future_date


@pytest.fixture
def store():
    s = CallStore(":memory:")
    yield s
    s.close()


async def _concierge(store):
    svc = MockReservationService.in_process(db_path=":memory:")
    return Concierge(svc, store=store, call_id="regression")


# ---------------------------------------------------------------------------
# Takeout: the single biggest leak in the corpus.
# 8 calls transferred as `takeout`, plus ~6 more who hung up after being told to
# use the website (#4544152, #4730445, #4825246, #4825421, #4825612, #4825891).
# Roughly one call in six. The incumbent had no takeout path at all.
# ---------------------------------------------------------------------------

async def test_takeout_caller_is_captured_not_dead_ended(store):
    c = await _concierge(store)
    try:
        msg = c.handle_takeout(name="Chris", phone="514-555-2020",
                               order="12 portions of the daily special for pickup tonight")
        assert "call you right back" in msg.lower()
        assert "website" not in msg.lower()  # not a brush-off
    finally:
        await c.service.aclose()

    rows = store.pending_messages()
    assert [r.kind for r in rows] == ["takeout"]
    assert "12 portions" in rows[0].body


async def test_takeout_caller_happy_with_website_is_not_forced_into_a_callback(store):
    """Respect the caller who says 'I'll just order online' — don't over-serve."""
    c = await _concierge(store)
    try:
        msg = c.handle_takeout(wants_callback=False)
        assert "online" in msg.lower()
    finally:
        await c.service.aclose()
    assert store.pending_messages() == []


async def test_takeout_without_a_number_asks_for_one_before_promising(store):
    c = await _concierge(store)
    try:
        msg = c.handle_takeout(order="tasting platter")
        assert "number" in msg.lower()
    finally:
        await c.service.aclose()
    assert store.pending_messages() == []  # nothing half-promised


# ---------------------------------------------------------------------------
# Cancellations: a capability gap, not a model failure.
# #4542081, #4569020, #4780168, #4780191 - every attempted cancellation in the
# corpus was transferred, because the incumbent has FindReservations but no
# CancelReservation tool. We have one; this proves it end to end.
# ---------------------------------------------------------------------------

async def test_cancellation_completes_without_a_human(store):
    from tests.conftest import slot_time

    c = await _concierge(store)
    try:
        date = future_date()
        await c.book_reservation(time=slot_time(date, "19:00"), party_size=2,
                                 first_name="Renee", phone="514-555-3030")
        msg = await c.cancel_reservation(phone="514-555-3030")
        assert "cancelled" in msg.lower()
        assert "transfer" not in msg.lower()
    finally:
        await c.service.aclose()


# ---------------------------------------------------------------------------
# No availability: 7 calls transferred under `no-availability`, and 16 of the 33
# availability lookups were flagged unavailable or empty. The incumbent's rule
# literally instructs a transfer once alternatives are declined.
# ---------------------------------------------------------------------------

async def test_no_availability_anywhere_captures_instead_of_transferring(store):
    from resto_agent.reservation.models import Availability

    c = await _concierge(store)
    try:
        async def nothing(date, party_size):
            return Availability(date=date, party_size=party_size, slots=[])

        c.service.check_availability = nothing
        msg = await c.check_availability(date=future_date(), party_size=5,
                                         part_of_day="dinner", preferred_time="8")
        assert "transfer" not in msg.lower()
        assert "call you back" in msg.lower()  # no auto-text exists
    finally:
        await c.service.aclose()


# ---------------------------------------------------------------------------
# Party size: the venue's own rule is "accept <= 7, staff arrange 8+".
# We had 6 in the Libro adapter, which would have wrongly escalated parties of 7.
# ---------------------------------------------------------------------------

def test_party_ceiling_is_six_because_libro_cannot_express_seven():
    """NOT a policy choice: a 28-day probe of /availabilities/{date} (4,386 cells)
    only ever returned party-size keys 1-6, and forcing &size=7 returns 200 with
    no "7" key. Setting this to 7 would make the agent tell a party of seven
    "we're fully booked" instead of offering to have staff arrange it."""
    from resto_agent import faq
    from resto_agent.reservation import libro_private

    assert libro_private.MAX_ONLINE_PARTY == 6
    assert faq.MAX_ONLINE_PARTY == 6


# ---------------------------------------------------------------------------
# The AM/PM rule, confirmed against the venue's live configuration.
# ---------------------------------------------------------------------------

def test_am_pm_inference_matches_the_venue_rule():
    """'No context and hour 1-8 -> always PM', including 24h-style '07h15'."""
    from resto_agent import datetime_resolve as dr

    assert dr.resolve_time("7") == (19, 0)
    assert dr.resolve_time("7h15") == (19, 15)
    assert dr.resolve_time("9") == (9, 0)      # 9+ stays AM without context
    assert dr.resolve_time("9 tonight") == (21, 0)


# ---------------------------------------------------------------------------
# Hours, confirmed against the venue's live configuration:
#   Mon-Sat 11:30-14:30 and 17:00-21:30; Sunday 17:00-21:30 only.
# ---------------------------------------------------------------------------

def test_hours_match_the_venue_configuration():
    from resto_agent import faq

    hours = faq.answer("hours", locale="en")
    assert "11:30" in hours and "2:30" in hours
    assert "5" in hours and "9:30" in hours


def test_no_sunday_lunch_service():
    from mock_libro import floorplan

    sunday = 6
    lunch = [s for s in floorplan.SERVICES
             if s.name.lower().startswith("lunch") and sunday in s.weekdays]
    assert lunch == [], "Sunday is dinner-only at this venue"
    dinner = [s for s in floorplan.SERVICES
              if s.name.lower().startswith("dinner") and sunday in s.weekdays]
    assert dinner, "Sunday dinner service should exist"
