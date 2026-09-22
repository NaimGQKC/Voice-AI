"""Shared test fixtures and helpers."""

from __future__ import annotations

import datetime as dt

import pytest

from resto_agent.reservation.mock import MockReservationService

# Montreal offset used by the mock's seeded seatings.
TZ = "-04:00"


def future_date(days: int = 30) -> str:
    """A date safely in the future so no slot is filtered out as 'past'."""
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def slot_time(date: str, hhmm: str = "18:00") -> str:
    return f"{date}T{hhmm}:00{TZ}"


@pytest.fixture
async def service():
    svc = MockReservationService.in_process(db_path=":memory:")
    try:
        yield svc
    finally:
        await svc.aclose()
