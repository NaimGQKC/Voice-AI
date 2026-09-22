"""Table-assignment engine for the LOCAL FAKE restaurant. Not production.

    ⚠️  THE TABLE LAYOUT BELOW IS INVENTED. It is NOT the venue's real floor plan.
        Nobody has ever confirmed the venue's table inventory with the owner.

    ⚠️  THIS ENGINE NEVER RUNS IN PRODUCTION. The live backend is
        `LibroPrivateReservationService`, and **Libro does its own seating** —
        we ask "can you fit 8 at 7 PM?" and Libro answers. We never see tables.
        Nothing in this file is imported by the live path.

So: the "combined table" behaviour you can see in `scripts/demo.py` proves *our
engine* works. It does **not** prove anything about how the venue's dining room is
actually run. Don't cite it as evidence about the real restaurant.

Why keep it at all: the test suite needs a backend that can realistically refuse
a booking, so it can run offline in ~6s with no API keys and no live writes to a
real restaurant. A fake that just says yes to everything would test nothing.

Design note that *does* carry over to production: table math is deterministic
Python, never an LLM. Same input, same output, unit-testable.

Key concepts encoded here:
  * Tables have capacities; some belong to a *combinable group* and can be
    pushed together for larger parties (up to MAX_MERGE_TABLES).
  * Counter seats are for 1-2 guests and are never merged.
  * A reservation occupies its table(s) for a *turn time*, so bookings conflict
    by time-overlap, not by exact start time.
  * Parties larger than any possible arrangement require staff (escalate).
  * Services (lunch/dinner) run on specific weekdays with a last seating time.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from itertools import combinations

# Montreal (America/Toronto). Fixed at EDT (-04:00) for the mock's purposes.
TZ_OFFSET = "-04:00"

# Up to this many tables may be pushed together for one party.
MAX_MERGE_TABLES = 2
MIN_PARTY_SIZE = 1


# -- tables -----------------------------------------------------------------

@dataclass(frozen=True)
class Table:
    id: str
    name: str
    seats: int
    kind: str = "table"  # "table" | "counter"
    group: str = ""       # combinable group; tables in the same group can merge
    min_party: int = 1


# An intimate ~30-seat room: a few 2-tops and 4-tops that can be combined within
# their zone, one 6-top, and a 4-seat counter seating (counter seats never merge).
FLOOR_PLAN: list[Table] = [
    Table("t_front_1", "Table 1", 2, group="front"),
    Table("t_front_2", "Table 2", 2, group="front"),
    Table("t_front_3", "Table 3", 2, group="front"),
    Table("t_mid_1", "Table 4", 4, group="mid"),
    Table("t_mid_2", "Table 5", 4, group="mid"),
    Table("t_back_1", "Table 6", 4, group="back"),
    Table("t_back_2", "Table 7", 2, group="back"),
    Table("t_window", "Table 8", 6, group="window"),  # single, not combinable
    Table("c_1", "Counter seat 1", 2, kind="counter", group="counter"),
    Table("c_2", "Counter seat 2", 2, kind="counter", group="counter"),
]

TABLES_BY_ID: dict[str, Table] = {t.id: t for t in FLOOR_PLAN}


@dataclass(frozen=True)
class Assignment:
    tables: list[Table]
    merged: bool

    @property
    def seats(self) -> int:
        return sum(t.seats for t in self.tables)

    @property
    def table_ids(self) -> list[str]:
        return [t.id for t in self.tables]

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]


def _mergeable_groups(tables: list[Table]) -> dict[str, list[Table]]:
    groups: dict[str, list[Table]] = {}
    for t in tables:
        if t.kind == "counter" or not t.group:
            continue
        groups.setdefault(t.group, []).append(t)
    # A group is only "combinable" if it has more than one table.
    return {g: ts for g, ts in groups.items() if len(ts) > 1}


def assign(party_size: int, free_ids: list[str]) -> Assignment | None:
    """Pick the least-wasteful seating for ``party_size`` from ``free_ids``.

    Prefers a single table that fits; otherwise the smallest valid merge within
    one combinable group. Returns ``None`` if the free tables can't seat them.
    """
    free = [TABLES_BY_ID[i] for i in free_ids if i in TABLES_BY_ID]

    # 1) Single table: smallest sufficient; counter only for 1-2; prefer a real
    #    table over the counter on ties.
    singles = [
        t for t in free
        if t.min_party <= party_size <= t.seats
        and (t.kind != "counter" or party_size <= 2)
    ]
    if singles:
        best = min(singles, key=lambda t: (t.seats, t.kind == "counter", t.name))
        return Assignment(tables=[best], merged=False)

    # 2) Merge within a single combinable group; minimize wasted seats, then count.
    best_merge: tuple[tuple[int, int], list[Table]] | None = None
    for tables in _mergeable_groups(free).values():
        for r in range(2, min(MAX_MERGE_TABLES, len(tables)) + 1):
            for combo in combinations(tables, r):
                seats = sum(t.seats for t in combo)
                if seats >= party_size:
                    key = (seats, len(combo))
                    if best_merge is None or key < best_merge[0]:
                        best_merge = (key, list(combo))
    if best_merge:
        return Assignment(tables=best_merge[1], merged=True)

    return None


def max_arrangement_capacity() -> int:
    """Largest party the room can seat in *any* single arrangement (empty room)."""
    cap = max(t.seats for t in FLOOR_PLAN)
    for tables in _mergeable_groups(FLOOR_PLAN).values():
        top = sorted(tables, key=lambda t: -t.seats)[:MAX_MERGE_TABLES]
        cap = max(cap, sum(t.seats for t in top))
    return cap


#: Online booking cap. Larger parties must be arranged by staff (escalate).
MAX_ONLINE_PARTY = max_arrangement_capacity()


def requires_staff(party_size: int) -> bool:
    return party_size > MAX_ONLINE_PARTY


# -- services / hours -------------------------------------------------------

# Public listings: lunch Mon-Sat ~11:30-14:30, dinner daily ~17:00-21:30.
# We offer seatings on a 30-min grid up to a "last seating" before close.
# Monday=0 ... Sunday=6 (datetime.date.weekday()).

EXP_LUNCH = "exp_lunch_demo"
EXP_DINNER = "exp_dinner_demo"


@dataclass(frozen=True)
class Service:
    key: str
    experience_id: str
    name: str
    weekdays: frozenset[int]
    open_hhmm: str
    last_seating_hhmm: str
    turn_minutes: int
    step_minutes: int = 30


SERVICES: list[Service] = [
    Service(
        key="lunch",
        experience_id=EXP_LUNCH,
        name="Lunch",
        weekdays=frozenset({0, 1, 2, 3, 4, 5}),  # Mon-Sat (closed Sunday lunch)
        open_hhmm="11:30",
        last_seating_hhmm="13:00",  # owner-confirmed
        turn_minutes=90,   # owner: '90 minutes tops, very strict'
    ),
    Service(
        key="dinner",
        experience_id=EXP_DINNER,
        name="Dinner",
        weekdays=frozenset({0, 1, 2, 3, 4, 5, 6}),  # daily
        open_hhmm="17:00",
        last_seating_hhmm="20:00",  # owner-confirmed
        turn_minutes=90,   # owner: '90 minutes tops, very strict'
    ),
]

EXPERIENCES = {s.experience_id: s.name for s in SERVICES}


# -- time helpers -----------------------------------------------------------

def _parse(iso_time: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso_time)


def add_minutes(iso_time: str, minutes: int) -> str:
    return (_parse(iso_time) + dt.timedelta(minutes=minutes)).isoformat()


def overlaps(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    return _parse(start_a) < _parse(end_b) and _parse(start_b) < _parse(end_a)


def is_past(iso_time: str, *, now: dt.datetime | None = None) -> bool:
    try:
        when = _parse(iso_time)
    except ValueError:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when < now


def _hhmm_range(open_hhmm: str, last_hhmm: str, step: int) -> list[str]:
    start = dt.datetime.strptime(open_hhmm, "%H:%M")
    last = dt.datetime.strptime(last_hhmm, "%H:%M")
    out, cur = [], start
    while cur <= last:
        out.append(cur.strftime("%H:%M"))
        cur += dt.timedelta(minutes=step)
    return out


@dataclass(frozen=True)
class Seating:
    time: str
    experience_id: str
    experience_name: str
    turn_minutes: int


def candidate_starts(date: str) -> list[Seating]:
    """All seating start times offered on ``date`` (weekday-aware), pre-occupancy."""
    weekday = dt.date.fromisoformat(date).weekday()
    seatings: list[Seating] = []
    for svc in SERVICES:
        if weekday not in svc.weekdays:
            continue
        for hhmm in _hhmm_range(svc.open_hhmm, svc.last_seating_hhmm, svc.step_minutes):
            seatings.append(
                Seating(
                    time=f"{date}T{hhmm}:00{TZ_OFFSET}",
                    experience_id=svc.experience_id,
                    experience_name=svc.name,
                    turn_minutes=svc.turn_minutes,
                )
            )
    return seatings


def slot_for_time(iso_time: str) -> Seating | None:
    """Return the configured seating matching ``iso_time`` exactly, or None."""
    try:
        date = iso_time[:10]
    except (TypeError, IndexError):
        return None
    return next((s for s in candidate_starts(date) if s.time == iso_time), None)


def spoken_label(iso_time: str) -> str:
    try:
        hour, minute = int(iso_time[11:13]), iso_time[14:16]
    except (ValueError, IndexError):
        return iso_time
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute} {suffix}"
