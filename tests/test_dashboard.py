"""Tests for the read-only call dashboard.

The properties worth pinning are about what it must NOT do: leak guest phone
numbers by default, and never expose a way to change anything.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from resto_agent.dashboard import build_app  # noqa: E402
from resto_agent.store import CallStore  # noqa: E402


@pytest.fixture
def client():
    store = CallStore(":memory:")
    store.start_call("call-1", locale="fr")
    store.end_call("call-1", outcome="completed")
    store.start_call("call-2", locale="fr")
    store.end_call("call-2", outcome="no_user_turn")
    store.record_message(call_id="call-1", kind="takeout", name="Chris",
                         phone="+15145552020", body="12 portions du plat du jour")
    store.record_booking_attempt(call_id="call-1", ok=False, party_size=7,
                                 wanted_time="2026-08-01T19:00", name="Alex",
                                 phone="+15145551234", error="LargePartyError")
    yield TestClient(build_app(store))
    store.close()


def test_renders_the_things_that_need_a_human(client):
    body = client.get("/").text
    assert "Chris" in body               # the outstanding message
    assert "12 portions du plat du jour" in body  # what they actually wanted
    assert "LargePartyError" in body     # the booking that failed


def test_phone_numbers_are_masked_by_default(client):
    """The owner will open this on a phone, in public."""
    body = client.get("/").text
    assert "+15145552020" not in body
    assert "2020" in body  # enough to recognise the caller


def test_full_numbers_require_an_explicit_act(client):
    body = client.get("/?full=1").text
    assert "+15145552020" in body


def test_token_is_enforced_when_configured(client, monkeypatch):
    monkeypatch.setenv("AGENT_DASHBOARD_TOKEN", "s3cret")
    assert client.get("/").status_code == 401
    assert client.get("/?token=wrong").status_code == 401
    assert client.get("/?token=s3cret").status_code == 200


def test_no_token_configured_means_open(client):
    """Local use must not require ceremony; production sets the token."""
    assert client.get("/").status_code == 200


def test_health_is_unauthenticated_and_cheap(client, monkeypatch):
    """The host's liveness probe cannot carry a secret."""
    monkeypatch.setenv("AGENT_DASHBOARD_TOKEN", "s3cret")
    r = client.get("/health")
    assert r.status_code == 200
    assert "ok" in r.text.lower()


def test_dashboard_exposes_no_mutating_routes():
    """Read-only is a security property, not a description. Libro is the record."""
    store = CallStore(":memory:")
    try:
        app = build_app(store)
        methods = set()
        for route in app.routes:
            methods |= set(getattr(route, "methods", set()) or set())
        assert methods <= {"GET", "HEAD"}, methods
    finally:
        store.close()


def test_empty_store_renders_without_error():
    """A brand-new deployment has no calls; the page must still load."""
    store = CallStore(":memory:")
    try:
        r = TestClient(build_app(store)).get("/")
        assert r.status_code == 200
        assert "No calls recorded yet" in r.text
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Category breakdown and per-call transcripts — the Sadie-parity features.
# ---------------------------------------------------------------------------

def _seed(store, cid, tools, booked=False, spoke=True, tx=""):
    store.start_call(cid, locale="fr")
    store.record_greeting(cid, user_spoke=1 if spoke else 0, detected_language="fr")
    for t in tools:
        store.record_tool_call(call_id=cid, tool=t)
    if booked:
        store.record_booking_attempt(call_id=cid, ok=True, booking_id="b1", party_size=2)
    store.end_call(cid, outcome="completed" if spoke else "no_user_turn", transcript=tx)


@pytest.fixture
def rich():
    s = CallStore(":memory:")
    _seed(s, "c1", ["check_availability", "book_reservation"], booked=True,
          tx="Caller: une table pour deux\nAgent: bien sûr")
    _seed(s, "c2", ["check_availability", "book_reservation"], booked=True)
    _seed(s, "c3", ["check_availability"])
    _seed(s, "c4", ["handle_takeout"])
    _seed(s, "c5", [], spoke=False)
    yield s
    s.close()


def test_category_breakdown_percentages(rich):
    counts = {cat: (n, share) for cat, n, share in rich.category_breakdown(since_days=7)}
    assert counts["BOOKED"][0] == 2
    assert counts["BOOKED"][1] == pytest.approx(0.4)
    assert counts["TAKEOUT"][0] == 1
    assert counts["NO_INTERACTION"][0] == 1
    assert sum(n for n, _ in counts.values()) == 5


def test_breakdown_renders_with_shares(rich):
    body = TestClient(build_app(rich)).get("/").text
    assert "What calls turned into" in body
    assert "BOOKED" in body and "40%" in body


def test_category_is_derived_from_the_tool_trace_not_asserted(rich):
    """The label and its evidence can never disagree — that's the point."""
    assert rich.call_detail("c1")["category"] == "BOOKED"
    assert rich.tool_trace("c1") == ["check_availability", "book_reservation"]
    assert "book_reservation" in rich.call_detail("c1")["tool_sequence"]


def test_transcript_page_shows_the_conversation(rich):
    body = TestClient(build_app(rich)).get("/call/c1").text
    assert "une table pour deux" in body
    assert "book_reservation" in body


def test_transcript_page_says_so_when_none_was_stored(rich):
    body = TestClient(build_app(rich)).get("/call/c2").text
    assert "No transcript stored" in body
    assert "AGENT_STORE_TRANSCRIPTS" in body


def test_transcript_is_html_escaped():
    """A caller can say anything, and it lands in this page."""
    s = CallStore(":memory:")
    try:
        _seed(s, "x", ["answer_faq"], tx="Caller: <script>alert(1)</script>")
        body = TestClient(build_app(s)).get("/call/x").text
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body
    finally:
        s.close()


def test_unknown_call_is_404(rich):
    assert TestClient(build_app(rich)).get("/call/nope").status_code == 404


def test_call_detail_requires_the_token(rich, monkeypatch):
    monkeypatch.setenv("AGENT_DASHBOARD_TOKEN", "s3cret")
    c = TestClient(build_app(rich))
    assert c.get("/call/c1").status_code == 401
    assert c.get("/call/c1?token=s3cret").status_code == 200
