"""SQLite storage and seed data for the mock Libro service.

Table inventory and service hours live in ``floorplan.py``; this module persists
restaurants, experiences, people, and bookings (including each booking's assigned
table ids and turn duration), and answers occupancy queries.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path

from . import floorplan

RESTAURANT_ID = "rest_demo_mtl"

# Re-export party limits (defined by the physical room in floorplan.py).
MIN_PARTY_SIZE = floorplan.MIN_PARTY_SIZE
MAX_ONLINE_PARTY = floorplan.MAX_ONLINE_PARTY


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Database:
    """Thin synchronous SQLite wrapper, guarded by a lock for thread-safety."""

    def __init__(self, path: str = ":memory:", *, seed: bool = True):
        self.path = path
        self._lock = threading.Lock()
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(Path(path).expanduser()) if path != ":memory:" else ":memory:",
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        if seed:
            self.seed()

    # -- schema / lifecycle -------------------------------------------------
    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS restaurants (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    locality TEXT,
                    timezone TEXT
                );
                CREATE TABLE IF NOT EXISTS experiences (
                    id TEXT PRIMARY KEY,
                    restaurant_id TEXT NOT NULL,
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS people (
                    id TEXT PRIMARY KEY,
                    first_name TEXT DEFAULT '',
                    last_name TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    email TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS bookings (
                    id TEXT PRIMARY KEY,
                    restaurant_id TEXT NOT NULL,
                    person_id TEXT NOT NULL,
                    experience_id TEXT DEFAULT '',
                    size INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    time TEXT NOT NULL,
                    duration_min INTEGER NOT NULL DEFAULT 105,
                    table_ids TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    locale TEXT DEFAULT 'en',
                    modification_restricted INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self._conn.commit()

    def seed(self) -> None:
        with self._lock:
            if self._conn.execute("SELECT COUNT(*) AS n FROM restaurants").fetchone()["n"]:
                return
            self._conn.execute(
                "INSERT INTO restaurants (id, name, locality, timezone) VALUES (?,?,?,?)",
                (RESTAURANT_ID, "Demo Bistro", "Montreal", "America/Toronto"),
            )
            for exp_id, name in floorplan.EXPERIENCES.items():
                self._conn.execute(
                    "INSERT INTO experiences (id, restaurant_id, name) VALUES (?,?,?)",
                    (exp_id, RESTAURANT_ID, name),
                )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- generic helpers ----------------------------------------------------
    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    # -- restaurants / experiences -----------------------------------------
    def get_restaurant(self, restaurant_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM restaurants WHERE id = ?", (restaurant_id,))

    def list_restaurants(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM restaurants ORDER BY name")

    def get_experience(self, experience_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM experiences WHERE id = ?", (experience_id,))

    # -- people -------------------------------------------------------------
    def get_person(self, person_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM people WHERE id = ?", (person_id,))

    def find_person_by_phone(self, phone: str) -> sqlite3.Row | None:
        if not phone:
            return None
        return self._one("SELECT * FROM people WHERE phone = ?", (phone,))

    def upsert_person(self, *, first_name="", last_name="", phone="", email="") -> sqlite3.Row:
        existing = self.find_person_by_phone(phone) if phone else None
        if existing:
            return existing
        pid = _gen_id("person")
        self._exec(
            "INSERT INTO people (id, first_name, last_name, phone, email) VALUES (?,?,?,?,?)",
            (pid, first_name, last_name, phone, email),
        )
        return self.get_person(pid)

    def update_person(self, person_id: str, **fields) -> sqlite3.Row | None:
        if not self.get_person(person_id):
            return None
        cols = {k: v for k, v in fields.items() if v is not None}
        if cols:
            assignments = ", ".join(f"{k} = ?" for k in cols)
            self._exec(
                f"UPDATE people SET {assignments} WHERE id = ?",
                (*cols.values(), person_id),
            )
        return self.get_person(person_id)

    # -- bookings -----------------------------------------------------------
    def get_booking(self, booking_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM bookings WHERE id = ?", (booking_id,))

    def list_bookings_for_phone(self, phone: str) -> list[sqlite3.Row]:
        return self._all(
            "SELECT b.* FROM bookings b JOIN people p ON p.id = b.person_id"
            " WHERE p.phone = ? ORDER BY b.time DESC",
            (phone,),
        )

    def confirmed_bookings_on_date(
        self, restaurant_id: str, date: str, *, exclude_id: str = ""
    ) -> list[sqlite3.Row]:
        return self._all(
            "SELECT id, time, duration_min, table_ids FROM bookings"
            " WHERE restaurant_id = ? AND status = 'confirmed'"
            " AND substr(time, 1, 10) = ? AND id != ?",
            (restaurant_id, date, exclude_id),
        )

    def free_table_ids(
        self, restaurant_id: str, start: str, duration_min: int, *, exclude_id: str = ""
    ) -> list[str]:
        """Table ids not occupied by any confirmed booking overlapping the turn."""
        end = floorplan.add_minutes(start, duration_min)
        date = start[:10]
        occupied: set[str] = set()
        for row in self.confirmed_bookings_on_date(restaurant_id, date, exclude_id=exclude_id):
            b_start = row["time"]
            b_end = floorplan.add_minutes(b_start, row["duration_min"])
            if floorplan.overlaps(start, end, b_start, b_end):
                occupied.update(filter(None, (row["table_ids"] or "").split(",")))
        return [t.id for t in floorplan.FLOOR_PLAN if t.id not in occupied]

    def insert_booking(
        self,
        *,
        restaurant_id: str,
        person_id: str,
        experience_id: str,
        size: int,
        time: str,
        duration_min: int,
        table_ids: list[str],
        note: str = "",
        locale: str = "en",
    ) -> sqlite3.Row:
        bid = _gen_id("booking")
        self._exec(
            "INSERT INTO bookings (id, restaurant_id, person_id, experience_id, size,"
            " status, time, duration_min, table_ids, note, locale, modification_restricted)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                bid, restaurant_id, person_id, experience_id, size, "confirmed",
                time, duration_min, ",".join(table_ids), note, locale, 0,
            ),
        )
        return self.get_booking(bid)

    def update_booking(self, booking_id: str, **fields) -> sqlite3.Row | None:
        if not self.get_booking(booking_id):
            return None
        if "table_ids" in fields and isinstance(fields["table_ids"], (list, tuple)):
            fields["table_ids"] = ",".join(fields["table_ids"])
        cols = {k: v for k, v in fields.items() if v is not None}
        if cols:
            assignments = ", ".join(f"{k} = ?" for k in cols)
            self._exec(
                f"UPDATE bookings SET {assignments} WHERE id = ?",
                (*cols.values(), booking_id),
            )
        return self.get_booking(booking_id)
