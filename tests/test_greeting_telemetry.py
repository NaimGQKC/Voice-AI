"""Call telemetry: the measuring instrument for the 19% zero-user-turn defect.

The point of these tests is that the numbers are *earned*. An unmeasured field
must stay NULL and be reported as "not measured" — never silently rendered as
zero, which would make a change look successful when it was simply unobserved.
"""

from __future__ import annotations

import io
import sqlite3
from contextlib import redirect_stdout

import pytest

from resto_agent.store import CALLS_TELEMETRY_COLUMNS, TELEMETRY_FIELDS, CallStore


@pytest.fixture
def store():
    s = CallStore(":memory:")
    yield s
    s.close()


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def tele():
    agent_mod = pytest.importorskip("resto_agent.agent")
    clock = _Clock()
    return agent_mod.GreetingTelemetry(now=clock), clock


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_calls_table_has_every_telemetry_column(store):
    have = {r["name"] for r in store._conn.execute("PRAGMA table_info(calls)")}
    assert set(TELEMETRY_FIELDS) <= have


def test_the_five_measurements_the_doc_asked_for_all_exist():
    """docs/GREETING_ABANDONMENT.md, step 1 — "measure, don't guess"."""
    assert {
        "answer_to_first_word_ms",   # time from answer to first audible word
        "greeting_ms",               # greeting duration
        "user_spoke",                # whether the caller spoke
        "first_user_speech_ms",      # ...and at what second
        "greeting_interrupted",      # whether they interrupted the greeting
        "detected_language",         # the language detected
    } <= set(TELEMETRY_FIELDS)


def test_migration_upgrades_a_database_created_before_telemetry(tmp_path):
    """CREATE TABLE IF NOT EXISTS is a no-op on a live DB — ALTER TABLE isn't."""
    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.executescript(
        "CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "call_id TEXT NOT NULL UNIQUE, started_at TEXT NOT NULL, ended_at TEXT, "
        "caller_number TEXT NOT NULL DEFAULT '', locale TEXT NOT NULL DEFAULT 'en', "
        "outcome TEXT NOT NULL DEFAULT 'in_progress', transcript TEXT NOT NULL DEFAULT '');"
    )
    old.execute("INSERT INTO calls (call_id, started_at) VALUES ('legacy-1', '2026-01-01')")
    old.commit()
    old.close()

    s = CallStore(str(db))
    try:
        have = {r["name"] for r in s._conn.execute("PRAGMA table_info(calls)")}
        assert set(TELEMETRY_FIELDS) <= have
        row = s._conn.execute(
            "SELECT * FROM calls WHERE call_id = 'legacy-1'").fetchone()
        assert row["answer_to_first_word_ms"] is None  # not measured, not zero
        assert row["user_spoke"] == 0
    finally:
        s.close()


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "twice.db")
    CallStore(db).close()
    s = CallStore(db)  # must not raise "duplicate column name"
    s.close()


# --------------------------------------------------------------------------
# Timing arithmetic
# --------------------------------------------------------------------------


def test_unmeasured_stays_none(tele):
    t, _ = tele
    assert t.as_row() == {
        "answer_to_first_word_ms": None,
        "greeting_ms": None,
        "greeting_interrupted": None,
        "user_spoke": None,
        "first_user_speech_ms": None,
        "detected_language": None,
        "disclosure_spoken": None,
    }


def test_a_normal_call_measures_all_five_numbers(tele):
    t, clock = tele
    t.mark_answered()
    clock.advance(0.42)          # dead air before the agent speaks
    t.mark_agent_speaking()
    clock.advance(1.1)           # "<name>, bonjour !"
    t.mark_greeting_done(interrupted=False, disclosure_spoken=True)
    clock.advance(0.8)
    t.mark_user_spoke(language="fr")

    assert t.answer_to_first_word_ms == 420
    assert t.greeting_ms == 1100
    assert t.first_user_speech_ms == pytest.approx(2320, abs=1)
    assert t.user_spoke is True
    assert t.detected_language == "fr"


def test_first_speech_wins_over_later_speech(tele):
    """We want when they FIRST spoke, not the most recent turn."""
    t, clock = tele
    t.mark_answered()
    clock.advance(1.0)
    t.mark_user_spoke()
    clock.advance(30.0)
    t.mark_user_spoke(language="fr")
    assert t.first_user_speech_ms == 1000
    assert t.detected_language == "fr"  # language may arrive after VAD onset


def test_first_agent_word_wins(tele):
    t, clock = tele
    t.mark_answered()
    clock.advance(0.5)
    t.mark_agent_speaking()
    clock.advance(5.0)
    t.mark_agent_speaking()  # later replies must not move it
    assert t.answer_to_first_word_ms == 500


def test_silent_caller_reports_user_spoke_false(tele):
    t, clock = tele
    t.mark_answered()
    clock.advance(0.3)
    t.mark_agent_speaking()
    clock.advance(1.0)
    t.mark_greeting_done(interrupted=False, disclosure_spoken=True)
    assert t.user_spoke is False
    assert t.first_user_speech_ms is None
    assert t.as_row()["user_spoke"] is None  # leaves the column at its 0 default


def test_barge_in_is_recorded(tele):
    t, clock = tele
    t.mark_answered()
    t.mark_agent_speaking()
    clock.advance(0.4)
    t.mark_user_spoke(language="fr")
    t.mark_greeting_done(interrupted=True, disclosure_spoken=False)
    row = t.as_row()
    assert row["greeting_interrupted"] is True
    assert row["disclosure_spoken"] is None
    assert row["user_spoke"] is True


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_record_greeting_persists_the_row(store):
    store.start_call("c1", locale="fr")
    store.record_greeting(
        "c1", answer_to_first_word_ms=380, greeting_ms=1050,
        greeting_interrupted=True, user_spoke=True, first_user_speech_ms=1400,
        detected_language="fr", disclosure_spoken=False,
    )
    r = store._conn.execute("SELECT * FROM calls WHERE call_id='c1'").fetchone()
    assert r["answer_to_first_word_ms"] == 380
    assert r["greeting_ms"] == 1050
    assert r["greeting_interrupted"] == 1
    assert r["user_spoke"] == 1
    assert r["first_user_speech_ms"] == 1400
    assert r["detected_language"] == "fr"


def test_none_never_clobbers_an_earlier_measurement(store):
    """Called once after the greeting and again at hang-up — must accumulate."""
    store.start_call("c2")
    store.record_greeting("c2", answer_to_first_word_ms=300, greeting_ms=1000)
    store.record_greeting("c2", answer_to_first_word_ms=None, user_spoke=True,
                          first_user_speech_ms=2200, detected_language="en")
    r = store._conn.execute("SELECT * FROM calls WHERE call_id='c2'").fetchone()
    assert r["answer_to_first_word_ms"] == 300  # survived the second write
    assert r["greeting_ms"] == 1000
    assert r["user_spoke"] == 1
    assert r["detected_language"] == "en"


def test_record_greeting_rejects_unknown_fields(store):
    store.start_call("c3")
    with pytest.raises(ValueError, match="unknown telemetry field"):
        store.record_greeting("c3", nonsense=1)


def test_record_greeting_with_nothing_measured_is_a_noop(store):
    store.start_call("c4")
    store.record_greeting("c4", greeting_ms=None)
    r = store._conn.execute("SELECT * FROM calls WHERE call_id='c4'").fetchone()
    assert r["greeting_ms"] is None


def test_telemetry_columns_declaration_matches_field_names():
    assert TELEMETRY_FIELDS == tuple(n for n, _ in CALLS_TELEMETRY_COLUMNS)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _seed(store, n_silent: int, n_spoke: int) -> None:
    for i in range(n_silent):
        store.start_call(f"silent-{i}", locale="fr")
        store.record_greeting(f"silent-{i}", answer_to_first_word_ms=400,
                              greeting_ms=1000)
    for i in range(n_spoke):
        store.start_call(f"spoke-{i}", locale="fr")
        store.record_greeting(f"spoke-{i}", answer_to_first_word_ms=400,
                              greeting_ms=1000, user_spoke=True,
                              first_user_speech_ms=1800, detected_language="fr",
                              disclosure_spoken=True)


def test_zero_user_turn_rate(store):
    _seed(store, n_silent=2, n_spoke=8)
    s = store.greeting_stats()
    assert s["calls"] == 10
    assert s["zero_user_turn"] == 2
    assert s["zero_user_turn_rate"] == pytest.approx(0.2)


def test_rate_is_none_not_zero_when_there_are_no_calls(store):
    s = store.greeting_stats()
    assert s["calls"] == 0
    assert s["zero_user_turn_rate"] is None
    assert s["avg_greeting_ms"] is None


def test_stats_report_language_breakdown(store):
    store.start_call("fr-1")
    store.record_greeting("fr-1", user_spoke=True, detected_language="fr")
    store.start_call("en-1")
    store.record_greeting("en-1", user_spoke=True, detected_language="en")
    store.start_call("quiet-1")
    langs = store.greeting_stats()["languages"]
    assert langs["fr"] == 1
    assert langs["en"] == 1
    assert langs["unknown"] == 1  # never spoke -> we genuinely don't know


def test_silent_calls_lists_the_offending_calls(store):
    _seed(store, n_silent=3, n_spoke=1)
    rows = store.silent_calls()
    assert {r["call_id"] for r in rows} == {"silent-0", "silent-1", "silent-2"}


def test_disclosure_deferred_is_counted(store):
    store.start_call("barged")
    store.record_greeting("barged", user_spoke=True, greeting_interrupted=True,
                          disclosure_spoken=False)
    s = store.greeting_stats()
    assert s["greeting_interrupted"] == 1
    assert s["disclosure_deferred"] == 1


def test_greeting_report_prints_not_measured_rather_than_zero(store):
    """An unobserved call must not be reported as a 0ms greeting."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import calls as calls_cli

    store.start_call("unmeasured")
    buf = io.StringIO()
    with redirect_stdout(buf):
        calls_cli.print_greeting_report(store, days=7)
    out = buf.getvalue()
    assert "not measured" in out
    assert "0ms" not in out


def test_greeting_report_shows_the_rate_and_the_baseline(store):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import calls as calls_cli

    _seed(store, n_silent=2, n_spoke=8)
    buf = io.StringIO()
    with redirect_stdout(buf):
        calls_cli.print_greeting_report(store, days=7)
    out = buf.getvalue()
    assert "20%" in out          # measured
    assert "19%" in out          # the incumbent baseline to beat
    assert "under 10%" in out    # the target


def test_greeting_report_on_an_empty_log_says_so(store):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import calls as calls_cli

    buf = io.StringIO()
    with redirect_stdout(buf):
        calls_cli.print_greeting_report(store, days=7)
    assert "nothing to report" in buf.getvalue().lower()
