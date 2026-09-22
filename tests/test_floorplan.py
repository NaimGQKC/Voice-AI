"""Unit tests for the deterministic floor-plan / table-assignment engine."""

from __future__ import annotations

import datetime as dt

from mock_libro import floorplan as fp


def all_ids() -> list[str]:
    return [t.id for t in fp.FLOOR_PLAN]


def test_single_table_prefers_least_waste():
    a = fp.assign(2, all_ids())
    assert a is not None and not a.merged
    assert a.seats == 2  # a 2-top, not a 4- or 6-top


def test_party_of_six_uses_the_single_six_top():
    a = fp.assign(6, all_ids())
    assert a is not None and not a.merged
    assert a.seats == 6


def test_party_of_eight_merges_two_tables():
    a = fp.assign(8, all_ids())
    assert a is not None and a.merged
    assert len(a.tables) == 2 and a.seats >= 8


def test_counter_not_used_for_more_than_two():
    # Only the two counter seats free -> a party of 3 cannot be seated (no merge).
    a = fp.assign(3, ["c_1", "c_2"])
    assert a is None


def test_capacity_and_staff_threshold():
    cap = fp.max_arrangement_capacity()
    assert cap == fp.MAX_ONLINE_PARTY == 8
    assert not fp.requires_staff(8)
    assert fp.requires_staff(9)


def test_no_assignment_when_tables_busy():
    # Remove every table that could seat 8 (the mid group) -> no arrangement.
    free = [i for i in all_ids() if i not in ("t_mid_1", "t_mid_2")]
    assert fp.assign(8, free) is None


def test_lunch_closed_on_sunday_dinner_open():
    # Find the next Sunday from today.
    today = dt.date.today()
    sunday = today + dt.timedelta(days=(6 - today.weekday()) % 7 or 7)
    seatings = fp.candidate_starts(sunday.isoformat())
    names = {s.experience_name for s in seatings}
    assert "Dinner" in names
    assert "Lunch" not in names  # closed Sunday lunch


def test_lunch_available_on_a_weekday():
    today = dt.date.today()
    # Next Monday.
    monday = today + dt.timedelta(days=(0 - today.weekday()) % 7 or 7)
    names = {s.experience_name for s in fp.candidate_starts(monday.isoformat())}
    assert {"Lunch", "Dinner"} <= names


def test_overlap_math():
    base = f"2026-07-20T19:00:00{fp.TZ_OFFSET}"
    end = fp.add_minutes(base, 105)  # 20:45
    assert fp.overlaps(base, end, f"2026-07-20T19:30:00{fp.TZ_OFFSET}",
                       f"2026-07-20T21:15:00{fp.TZ_OFFSET}")
    assert not fp.overlaps(base, end, f"2026-07-20T17:00:00{fp.TZ_OFFSET}",
                           f"2026-07-20T18:15:00{fp.TZ_OFFSET}")
