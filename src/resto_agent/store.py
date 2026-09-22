"""Durable record of what the agent did on every call.

Why this exists
---------------
Before this module, ``take_message`` appended to a Python list on the Concierge,
and a Concierge is created **per call**. So the agent told callers *"I've passed
your message to the team"* and the message was garbage-collected when they hung
up. Same for ``join_waitlist`` — *"we'll text you the moment a table opens"* went
nowhere. Those are the two paths the agent uses for the calls it *cannot* close
(a party of 14, a fully-booked Saturday), which are the highest-value calls in
the system. They were the only ones guaranteed to be lost.

Two rules follow from that, and they are the whole design:

1. **Never speak a promise you haven't kept.** The confirmation line is returned
   only *after* the row is committed. If the write fails, the agent says
   something honest instead.
2. **Storage and delivery are separate.** Writing to disk is what makes the
   promise true; texting/emailing the restaurant is how they find out quickly.
   Delivery is best-effort and may fail — the record must not depend on it.

Why SQLite
----------
One restaurant, one process, a handful of calls a day, and a mandate to deploy
once and not touch it again. SQLite is a file: no service to run, back up, patch,
or upgrade. WAL mode is on so a reader (the owner running ``scripts/calls.py``)
never blocks the agent mid-call.

⚠️  **Deployment constraint this introduces.** The agent was previously stateless
per call and could run on ephemeral disk. It can't now. ``AGENT_DB_PATH`` must
point at a **persistent volume**, or every restart silently loses the messages —
reintroducing exactly the bug this module removes.

⚠️  **This file contains guest PII** (names, phone numbers). Quebec's Law 25
applies. Hence ``purge_older_than`` and a documented retention default; see
``docs/DATA_RETENTION.md``.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Keep guest contact details no longer than this by default (Law 25: keep
#: personal information only as long as the purpose requires).
DEFAULT_RETENTION_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id       TEXT    NOT NULL UNIQUE,
    started_at    TEXT    NOT NULL,
    ended_at      TEXT,
    caller_number TEXT    NOT NULL DEFAULT '',
    locale        TEXT    NOT NULL DEFAULT 'en',
    outcome       TEXT    NOT NULL DEFAULT 'in_progress',
    transcript    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id      TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    kind         TEXT    NOT NULL,           -- 'message' | 'waitlist'
    name         TEXT    NOT NULL DEFAULT '',
    phone        TEXT    NOT NULL DEFAULT '',
    body         TEXT    NOT NULL DEFAULT '',
    party_size   INTEGER NOT NULL DEFAULT 0,
    wanted_date  TEXT    NOT NULL DEFAULT '',
    wanted_time  TEXT    NOT NULL DEFAULT '',
    delivered_at TEXT,                       -- NULL = the team has NOT been told
    delivery_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS booking_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    ok          INTEGER NOT NULL,            -- 1 = Libro confirmed it
    booking_id  TEXT    NOT NULL DEFAULT '',
    party_size  INTEGER NOT NULL DEFAULT 0,
    wanted_time TEXT    NOT NULL DEFAULT '',
    name        TEXT    NOT NULL DEFAULT '',
    phone       TEXT    NOT NULL DEFAULT '',
    error       TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    seq        INTEGER NOT NULL,          -- order within the call
    tool       TEXT    NOT NULL,
    ok         INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_call ON tool_calls (call_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_undelivered
    ON messages (delivered_at) WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_attempts_failed
    ON booking_attempts (ok, created_at);
"""

#: Greeting telemetry on the ``calls`` table, added after 19% of real calls were
#: found to end with the caller never speaking (docs/GREETING_ABANDONMENT.md).
#:
#: Declared as ALTER TABLE migrations rather than baked into SCHEMA above,
#: because ``CREATE TABLE IF NOT EXISTS`` is a no-op against the production
#: database that already exists — the columns would silently never appear.
#:
#: A NULL here means **not measured**, which is not the same as zero. Reporting
#: must say "not measured" rather than invent a number; that is the same rule as
#: the module docstring — never state something the data hasn't earned.
CALLS_TELEMETRY_COLUMNS: tuple[tuple[str, str], ...] = (
    # ms from answering the call to the first audible word of the greeting
    ("answer_to_first_word_ms", "INTEGER"),
    # ms of greeting audio actually played (shorter than scripted if barged over)
    ("greeting_ms", "INTEGER"),
    # 1 = the caller talked over the greeting and it was cut short
    ("greeting_interrupted", "INTEGER NOT NULL DEFAULT 0"),
    # 1 = the caller said something at any point. 0 here IS the 19% defect.
    ("user_spoke", "INTEGER NOT NULL DEFAULT 0"),
    # ms from answering to the caller's first detected speech
    ("first_user_speech_ms", "INTEGER"),
    # language actually detected from the caller ('fr'/'en'/...), '' = unknown
    ("detected_language", "TEXT NOT NULL DEFAULT ''"),
    # 1 = the AI disclosure clause played to completion; 0 = it was talked over
    # and the model was told to disclose in its first reply instead
    ("disclosure_spoken", "INTEGER NOT NULL DEFAULT 0"),
    # Deterministic classification of the finished call (see CATEGORY_RULES).
    ("category", "TEXT NOT NULL DEFAULT ''"),
    # The tools that fired, in order — the evidence behind `category`.
    ("tool_sequence", "TEXT NOT NULL DEFAULT ''"),
)

#: Names only, in declaration order — the write path's allow-list.
TELEMETRY_FIELDS: tuple[str, ...] = tuple(name for name, _ in CALLS_TELEMETRY_COLUMNS)


#: How a finished call is classified, mirroring the categories the venue's
#: previous system reported so the two are comparable.
#:
#: Derived **deterministically from the tool trace**, never by asking a model to
#: summarise. The incumbent's own summaries were sometimes wrong — one claimed a
#: modification the tool trace shows never happened — and a dashboard that
#: quietly invents outcomes is worse than one that shows fewer of them.
#:
#: Order matters: the first matching rule wins, most-completed first.
CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("BOOKED", "a reservation was created"),
    ("MODIFIED", "an existing reservation was moved"),
    ("CANCELLED", "a reservation was cancelled"),
    ("TAKEOUT", "a takeout order was captured for callback"),
    ("WAITLIST", "caller captured because nothing was available"),
    ("MESSAGE", "a message was taken for the team"),
    ("NO_AVAILABILITY", "availability was checked, nothing booked"),
    ("QA", "a question was answered, nothing booked"),
    ("NO_INTERACTION", "the caller never spoke"),
    ("INCOMPLETE", "the caller spoke but nothing was completed"),
)

CATEGORY_REASONS: dict[str, str] = dict(CATEGORY_RULES)


def categorize(*, tools: list[str], booked: bool, user_spoke: bool) -> str:
    """Classify a call from what actually happened. Pure; unit-tested."""
    ts = set(tools)
    if booked:
        return "BOOKED"
    if "reschedule_reservation" in ts:
        return "MODIFIED"
    if "cancel_reservation" in ts:
        return "CANCELLED"
    if "handle_takeout" in ts:
        return "TAKEOUT"
    if "join_waitlist" in ts:
        return "WAITLIST"
    if "take_message" in ts:
        return "MESSAGE"
    if not user_spoke:
        return "NO_INTERACTION"
    if "check_availability" in ts:
        return "NO_AVAILABILITY"
    if "answer_faq" in ts or "lookup_reservation" in ts:
        return "QA"
    return "INCOMPLETE"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class PendingMessage:
    """A row the restaurant has not been told about yet."""

    id: int
    kind: str
    name: str
    phone: str
    body: str
    party_size: int
    wanted_date: str
    wanted_time: str
    created_at: str


class CallStore:
    """SQLite-backed record of calls, messages, and booking attempts.

    Thread-safe: the agent's tools run on the job worker thread while a CLI
    reader may hold its own connection. One lock around writes is ample at this
    volume and avoids reasoning about SQLite threading modes.
    """

    def __init__(self, db_path: str = ""):
        self.db_path = db_path or os.environ.get("AGENT_DB_PATH", "resto_calls.db")
        self._lock = threading.Lock()
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: guarded by self._lock below.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            # WAL lets the owner read the log while a call is in progress.
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns that post-date the original ``calls`` table.

        ``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already
        exists, so a live database would never gain the greeting telemetry
        columns without this. Idempotent, and safe to run on every start.
        """
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(calls)")}
        for name, decl in CALLS_TELEMETRY_COLUMNS:
            if name not in have:
                self._conn.execute(f"ALTER TABLE calls ADD COLUMN {name} {decl}")

    # -- calls -------------------------------------------------------------
    def start_call(self, call_id: str, *, caller_number: str = "",
                   locale: str = "en") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO calls (call_id, started_at, caller_number, locale) "
                "VALUES (?, ?, ?, ?)",
                (call_id, _now(), caller_number, locale),
            )
            self._conn.commit()

    def end_call(self, call_id: str, *, outcome: str = "completed",
                 transcript: str = "") -> None:
        """Close the call, and classify it from what actually happened.

        The category is computed here rather than written by the caller so that
        it always reflects the recorded trace — there is no path where the label
        and the evidence can disagree.
        """
        tools = self.tool_trace(call_id)
        booked = bool(self._conn.execute(
            "SELECT 1 FROM booking_attempts WHERE call_id = ? AND ok = 1 LIMIT 1",
            (call_id,)
        ).fetchone())
        spoke_row = self._conn.execute(
            "SELECT user_spoke FROM calls WHERE call_id = ?", (call_id,)
        ).fetchone()
        user_spoke = bool(spoke_row["user_spoke"]) if spoke_row else False
        category = categorize(tools=tools, booked=booked, user_spoke=user_spoke)

        with self._lock:
            self._conn.execute(
                "UPDATE calls SET ended_at = ?, outcome = ?, transcript = ?, "
                "category = ?, tool_sequence = ? WHERE call_id = ?",
                (_now(), outcome, transcript, category, " -> ".join(tools), call_id),
            )
            self._conn.commit()

    # -- greeting telemetry ------------------------------------------------
    def record_greeting(self, call_id: str, **fields: object) -> None:
        """Record what the caller actually experienced in the first seconds.

        Accepts any subset of :data:`TELEMETRY_FIELDS`. **``None`` means "not
        measured" and is skipped**, so calling this twice (once when the
        greeting finishes, once at hang-up) accumulates rather than clobbers —
        a later call cannot erase an earlier measurement by not having it.

        Never raises into the call: telemetry going missing is a reporting
        problem, and it must not be able to drop the phone line.
        """
        unknown = set(fields) - set(TELEMETRY_FIELDS)
        if unknown:
            raise ValueError(f"unknown telemetry field(s): {sorted(unknown)}")

        pairs = [(k, v) for k, v in fields.items() if v is not None]
        if not pairs:
            return
        sets = ", ".join(f"{k} = ?" for k, _ in pairs)
        values = [int(v) if isinstance(v, bool) else v for _, v in pairs]
        try:
            with self._lock:
                self._conn.execute(
                    f"UPDATE calls SET {sets} WHERE call_id = ?", (*values, call_id)
                )
                self._conn.commit()
        except Exception:  # pragma: no cover - defensive
            logger.exception("failed to record greeting telemetry for %s", call_id)

    def greeting_stats(self, *, since_days: int = 7) -> dict:
        """The numbers behind docs/GREETING_ABANDONMENT.md, for our own line.

        ``zero_user_turn_rate`` is the one to beat: 19% in the incumbent's
        corpus, target under 10%. Averages are ``None`` when nothing was
        measured — not 0 — because "we don't know" and "it was instant" are very
        different answers.
        """
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=since_days)).isoformat(timespec="seconds")
        row = self._conn.execute(
            """
            SELECT COUNT(*)                                     AS calls,
                   SUM(CASE WHEN user_spoke = 0 THEN 1 ELSE 0 END)          AS zero_user_turn,
                   SUM(CASE WHEN greeting_interrupted = 1 THEN 1 ELSE 0 END) AS interrupted,
                   SUM(CASE WHEN user_spoke = 1 AND disclosure_spoken = 0
                            THEN 1 ELSE 0 END)                  AS disclosure_deferred,
                   AVG(answer_to_first_word_ms)                 AS avg_answer_to_first_word_ms,
                   MAX(answer_to_first_word_ms)                 AS max_answer_to_first_word_ms,
                   AVG(greeting_ms)                             AS avg_greeting_ms,
                   MAX(greeting_ms)                             AS max_greeting_ms,
                   AVG(first_user_speech_ms)                    AS avg_first_user_speech_ms
              FROM calls WHERE started_at >= ?
            """,
            (cutoff,),
        ).fetchone()
        calls = int(row["calls"] or 0)
        langs = {
            r["detected_language"] or "unknown": r["c"]
            for r in self._conn.execute(
                "SELECT detected_language, COUNT(*) c FROM calls "
                "WHERE started_at >= ? GROUP BY detected_language ORDER BY c DESC",
                (cutoff,),
            )
        }
        zero = int(row["zero_user_turn"] or 0)
        return {
            "calls": calls,
            "zero_user_turn": zero,
            # None, not 0.0, when there is nothing to divide by.
            "zero_user_turn_rate": (zero / calls) if calls else None,
            "greeting_interrupted": int(row["interrupted"] or 0),
            "disclosure_deferred": int(row["disclosure_deferred"] or 0),
            "avg_answer_to_first_word_ms": row["avg_answer_to_first_word_ms"],
            "max_answer_to_first_word_ms": row["max_answer_to_first_word_ms"],
            "avg_greeting_ms": row["avg_greeting_ms"],
            "max_greeting_ms": row["max_greeting_ms"],
            "avg_first_user_speech_ms": row["avg_first_user_speech_ms"],
            "languages": langs,
        }

    def silent_calls(self, *, since_days: int = 7, limit: int = 20) -> list[sqlite3.Row]:
        """Calls where the caller never said a word — the 19%, one row each."""
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=since_days)).isoformat(timespec="seconds")
        return self._conn.execute(
            "SELECT * FROM calls WHERE started_at >= ? AND user_spoke = 0 "
            "ORDER BY started_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()

    # -- messages & waitlist ----------------------------------------------
    def record_message(self, *, call_id: str, kind: str, name: str, phone: str,
                       body: str = "", party_size: int = 0, wanted_date: str = "",
                       wanted_time: str = "") -> int:
        """Commit a message/waitlist row. Returns its id.

        Raises on failure **on purpose** — the caller must not speak a
        confirmation if this didn't land.
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (call_id, created_at, kind, name, phone, body, "
                "party_size, wanted_date, wanted_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (call_id, _now(), kind, name, phone, body, party_size,
                 wanted_date, wanted_time),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def record_tool_call(self, *, call_id: str, tool: str, ok: bool = True) -> None:
        """Append to the call's tool trace. Never raises — telemetry must not
        break a live call."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM tool_calls "
                    "WHERE call_id = ?", (call_id,)
                ).fetchone()
                self._conn.execute(
                    "INSERT INTO tool_calls (call_id, created_at, seq, tool, ok) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (call_id, _now(), int(row["n"]), tool, 1 if ok else 0),
                )
                self._conn.commit()
        except Exception:
            logger.exception("could not record tool call %s for %s", tool, call_id)

    def tool_trace(self, call_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT tool FROM tool_calls WHERE call_id = ? ORDER BY seq", (call_id,)
        ).fetchall()
        return [r["tool"] for r in rows]

    def category_breakdown(self, *, since_days: int = 7) -> list[tuple[str, int, float]]:
        """(category, count, share) for finished calls, most common first."""
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=since_days)).isoformat(timespec="seconds")
        rows = self._conn.execute(
            "SELECT category, COUNT(*) c FROM calls "
            "WHERE started_at >= ? AND category != '' GROUP BY category "
            "ORDER BY c DESC", (cutoff,)
        ).fetchall()
        total = sum(r["c"] for r in rows) or 1
        return [(r["category"], r["c"], r["c"] / total) for r in rows]

    def call_detail(self, call_id: str):
        row = self._conn.execute(
            "SELECT * FROM calls WHERE call_id = ?", (call_id,)
        ).fetchone()
        return row

    def mark_delivered(self, message_id: int, *, error: str = "") -> None:
        """Record whether the restaurant was actually notified."""
        with self._lock:
            if error:
                self._conn.execute(
                    "UPDATE messages SET delivery_error = ? WHERE id = ?",
                    (error, message_id),
                )
            else:
                self._conn.execute(
                    "UPDATE messages SET delivered_at = ?, delivery_error = '' "
                    "WHERE id = ?",
                    (_now(), message_id),
                )
            self._conn.commit()

    def pending_messages(self) -> list[PendingMessage]:
        """Rows the restaurant has never been told about — the retry queue."""
        rows = self._conn.execute(
            "SELECT id, kind, name, phone, body, party_size, wanted_date, "
            "wanted_time, created_at FROM messages WHERE delivered_at IS NULL "
            "ORDER BY id"
        ).fetchall()
        return [PendingMessage(**dict(r)) for r in rows]

    # -- bookings ----------------------------------------------------------
    def record_booking_attempt(self, *, call_id: str, ok: bool, booking_id: str = "",
                               party_size: int = 0, wanted_time: str = "",
                               name: str = "", phone: str = "",
                               error: str = "") -> None:
        """Log every booking attempt, successful or not.

        The failures are the point: this is how the owner finds out a caller
        wanted a table and didn't get one.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO booking_attempts (call_id, created_at, ok, booking_id, "
                "party_size, wanted_time, name, phone, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (call_id, _now(), 1 if ok else 0, booking_id, party_size,
                 wanted_time, name, phone, error),
            )
            self._conn.commit()

    def failed_bookings(self, *, since_days: int = 7) -> list[sqlite3.Row]:
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=since_days)).isoformat(timespec="seconds")
        return self._conn.execute(
            "SELECT * FROM booking_attempts WHERE ok = 0 AND created_at >= ? "
            "ORDER BY created_at DESC", (cutoff,)
        ).fetchall()

    def recent_calls(self, limit: int = 50) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM calls ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def stats(self, *, since_days: int = 7) -> dict:
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=since_days)).isoformat(timespec="seconds")
        q = self._conn.execute
        return {
            "calls": q("SELECT COUNT(*) c FROM calls WHERE started_at >= ?",
                       (cutoff,)).fetchone()["c"],
            "bookings_ok": q("SELECT COUNT(*) c FROM booking_attempts "
                             "WHERE ok = 1 AND created_at >= ?", (cutoff,)).fetchone()["c"],
            "bookings_failed": q("SELECT COUNT(*) c FROM booking_attempts "
                                 "WHERE ok = 0 AND created_at >= ?", (cutoff,)).fetchone()["c"],
            "messages": q("SELECT COUNT(*) c FROM messages WHERE created_at >= ?",
                          (cutoff,)).fetchone()["c"],
            "undelivered": q("SELECT COUNT(*) c FROM messages "
                             "WHERE delivered_at IS NULL").fetchone()["c"],
        }

    # -- retention (Law 25) ------------------------------------------------
    def purge_older_than(self, days: int = DEFAULT_RETENTION_DAYS) -> int:
        """Delete guest contact details past the retention window."""
        cutoff = (dt.datetime.now(dt.timezone.utc)
                  - dt.timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            n = 0
            for table, col in (("messages", "created_at"),
                               ("booking_attempts", "created_at"),
                               ("calls", "started_at")):
                n += self._conn.execute(
                    f"DELETE FROM {table} WHERE {col} < ?", (cutoff,)
                ).rowcount
            self._conn.commit()
            return n

    def close(self) -> None:
        with self._lock:
            self._conn.close()
