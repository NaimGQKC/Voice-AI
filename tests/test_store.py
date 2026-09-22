"""Tests for the durable call log — the fix for messages dying on hang-up."""

from __future__ import annotations

import pytest

from resto_agent import faq
from resto_agent.concierge import Concierge
from resto_agent.reservation.mock import MockReservationService
from resto_agent.store import CallStore


@pytest.fixture
def store():
    s = CallStore(":memory:")
    yield s
    s.close()


class _CountingNotifier:
    def __init__(self, error: str = ""):
        self.sent: list[tuple[str, str]] = []
        self.error = error

    def send(self, subject: str, body: str) -> str:
        self.sent.append((subject, body))
        return self.error


async def _concierge(store, notifier=None):
    svc = MockReservationService.in_process(db_path=":memory:")
    return Concierge(svc, store=store, notifier=notifier, call_id="call-1")


async def test_message_survives_the_end_of_the_call(store):
    """The original bug: the Concierge dies per call, so the list died with it."""
    c = await _concierge(store)
    try:
        c.take_message(name="Jordan", phone="514-555-9000", message="party of 14")
    finally:
        await c.service.aclose()

    del c  # caller hangs up, Concierge is garbage-collected

    pending = store.pending_messages()
    assert len(pending) == 1
    assert pending[0].name == "Jordan"
    assert pending[0].body == "party of 14"
    assert pending[0].phone == "+15145559000"  # normalized on the way in


async def test_waitlist_capture_is_durable(store):
    c = await _concierge(store)
    try:
        c.join_waitlist(name="Dana", phone="514-555-0143", party_size=2)
    finally:
        await c.service.aclose()

    rows = store.pending_messages()
    assert [r.kind for r in rows] == ["waitlist"]
    assert rows[0].party_size == 2


async def test_confirmation_is_NOT_spoken_when_the_write_fails(store):
    """The core rule: never promise something we failed to record."""
    c = await _concierge(store)
    try:
        def boom(**kwargs):
            raise RuntimeError("disk full")

        c.store.record_message = boom
        msg = c.take_message(name="Jordan", phone="514-555-9000", message="party of 14")
        assert "passed your message" not in msg.lower()
        assert "trouble" in msg.lower()
        assert faq.PHONE in msg  # gives them a real way to reach a human
    finally:
        await c.service.aclose()


async def test_delivery_failure_still_keeps_the_record(store):
    """An SMS that fails must not lose the message — it stays in the queue."""
    notifier = _CountingNotifier(error="twilio 500")
    c = await _concierge(store, notifier)
    try:
        msg = c.take_message(name="Sam", phone="514-555-1111", message="allergy question")
        assert "passed your message" in msg.lower()  # the record IS durable
    finally:
        await c.service.aclose()

    assert len(notifier.sent) == 1
    pending = store.pending_messages()
    assert len(pending) == 1  # undelivered -> still actionable by a human


async def test_successful_delivery_clears_the_pending_queue(store):
    notifier = _CountingNotifier()
    c = await _concierge(store, notifier)
    try:
        c.take_message(name="Sam", phone="514-555-1111", message="allergy question")
    finally:
        await c.service.aclose()

    assert store.pending_messages() == []
    assert store.stats()["undelivered"] == 0


async def test_failed_booking_is_recorded_for_the_owner(store):
    """'Check if a reservation was not properly made' — this is that record."""
    from resto_agent.reservation.errors import SlotUnavailableError

    c = await _concierge(store)
    try:
        async def unavailable(**kwargs):
            raise SlotUnavailableError()

        c.service.create_booking = unavailable
        await c.book_reservation(time="2026-09-08T19:00:00-04:00", party_size=2,
                                 first_name="Alex", phone="+15145551234")
    finally:
        await c.service.aclose()

    failed = store.failed_bookings()
    assert len(failed) == 1
    assert failed[0]["name"] == "Alex"
    assert failed[0]["ok"] == 0
    assert "SlotUnavailable" in failed[0]["error"]


async def test_successful_booking_is_recorded(store):
    from tests.conftest import future_date, slot_time

    c = await _concierge(store)
    try:
        date = future_date()
        await c.book_reservation(time=slot_time(date, "19:00"), party_size=2,
                                 first_name="Alex", phone="+15145551234")
    finally:
        await c.service.aclose()

    assert store.stats()["bookings_ok"] == 1
    assert store.stats()["bookings_failed"] == 0


async def test_take_message_without_a_phone_asks_for_one(store):
    c = await _concierge(store)
    try:
        msg = c.take_message(name="Jordan", phone="", message="party of 14")
        assert "number" in msg.lower()
    finally:
        await c.service.aclose()
    assert store.pending_messages() == []  # nothing half-recorded


def test_purge_removes_old_records_but_keeps_recent_ones(store):
    """Law 25 retention: old guest contact details go, current ones stay."""
    import datetime as dt

    store.record_message(call_id="c1", kind="message", name="Recent",
                         phone="+15145550001", body="today")
    old_id = store.record_message(call_id="c0", kind="message", name="Old",
                                  phone="+15145550002", body="last year")
    stale = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=400)).isoformat(timespec="seconds")
    store._conn.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                        (stale, old_id))
    store._conn.commit()

    assert store.purge_older_than(days=90) == 1
    remaining = store.pending_messages()
    assert [r.name for r in remaining] == ["Recent"]


def test_stats_shape(store):
    s = store.stats()
    assert set(s) == {"calls", "bookings_ok", "bookings_failed", "messages",
                      "undelivered"}
