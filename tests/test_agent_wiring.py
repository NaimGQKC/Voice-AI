"""Wiring tests against the real livekit-agents SDK.

These run only when the `[agent]` extras are installed (pytest.importorskip
otherwise), and use fake keys — constructors validate configuration shape, not
credentials. They catch SDK API drift (renamed kwargs, deprecated options,
missing plugins) without needing audio or cloud access.
"""

from __future__ import annotations

import warnings

import pytest

pytest.importorskip("livekit.agents")


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "fake")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake")
    monkeypatch.setenv("CARTESIA_API_KEY", "fake")
    monkeypatch.setenv("GROQ_API_KEY", "fake")  # default LLM provider


def _build_session(settings, with_livekit_creds: bool, monkeypatch):
    if with_livekit_creds:
        monkeypatch.setenv("LIVEKIT_API_KEY", "fake")
        monkeypatch.setenv("LIVEKIT_API_SECRET", "fake")
        monkeypatch.setenv("LIVEKIT_URL", "wss://fake.livekit.cloud")
    else:
        monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
        monkeypatch.delenv("LIVEKIT_URL", raising=False)

    from livekit.agents import AgentSession

    from resto_agent.agent import _build_llm, _build_stt, _build_tts, _build_turn_handling

    return AgentSession(
        stt=_build_stt(settings),
        llm=_build_llm(settings),
        tts=_build_tts(settings),
        turn_handling=_build_turn_handling(settings),
    )


def test_english_session_constructs_without_deprecations(monkeypatch):
    from resto_agent.config import Settings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        _build_session(Settings(), with_livekit_creds=True, monkeypatch=monkeypatch)


def test_console_mode_without_livekit_creds_falls_back_to_vad(monkeypatch):
    from resto_agent.agent import _build_turn_handling
    from resto_agent.config import Settings

    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    opts = _build_turn_handling(Settings())
    assert "turn_detection" not in opts  # VAD fallback, no crash at call time


def test_multilingual_session_constructs(monkeypatch):
    from resto_agent.config import Settings

    _build_session(
        Settings(language_mode="multi"),
        with_livekit_creds=True,
        monkeypatch=monkeypatch,
    )


def test_agent_registers_all_tools():
    from resto_agent.concierge import Concierge
    from resto_agent.prompts import system_instructions
    from resto_agent.reservation.mock import MockReservationService
    from resto_agent.tools import ReservationAgent

    svc = MockReservationService.in_process(db_path=":memory:")
    agent = ReservationAgent(
        Concierge(svc),
        instructions=system_instructions(multilingual=False, today="2026-07-24"),
    )
    assert len(agent.tools) == 9  # + join_waitlist, handle_takeout


async def test_tool_exception_does_not_crash_the_call():
    """A failing tool must speak a graceful line, never raise into the session."""
    from resto_agent.concierge import Concierge
    from resto_agent.prompts import system_instructions
    from resto_agent.reservation.mock import MockReservationService
    from resto_agent.tools import ReservationAgent

    svc = MockReservationService.in_process(db_path=":memory:")
    agent = ReservationAgent(
        Concierge(svc),
        instructions=system_instructions(multilingual=False, today="2026-07-24"),
    )

    async def boom(**kwargs):
        raise RuntimeError("backend exploded")

    agent.concierge.check_availability = boom
    out = await agent.check_availability(date="tomorrow", party_size=2)
    assert isinstance(out, str) and "trouble" in out.lower()
    await svc.aclose()


def test_tool_schema_survives_the_safety_wrapper():
    """@_safe must not hide the tool signature/docstring from the LLM."""
    import inspect

    from resto_agent.concierge import Concierge
    from resto_agent.prompts import system_instructions
    from resto_agent.reservation.mock import MockReservationService
    from resto_agent.tools import ReservationAgent

    svc = MockReservationService.in_process(db_path=":memory:")
    agent = ReservationAgent(
        Concierge(svc),
        instructions=system_instructions(multilingual=False, today="2026-07-24"),
    )
    params = inspect.signature(agent.check_availability).parameters
    assert {"date", "party_size", "part_of_day"} <= set(params)
    assert (agent.check_availability.__doc__ or "").strip()


def test_noise_cancellation_plugin_installed():
    from livekit.plugins import noise_cancellation

    assert hasattr(noise_cancellation, "BVC")


def test_builders_work_off_main_thread(monkeypatch):
    """Regression: LiveKit registers plugins at import and requires the main
    thread. Building STT/LLM/TTS from a worker thread (as the job runner does)
    must not raise "Plugins must be registered on the main thread" — i.e. all
    plugin imports must live at module top level, not inside the builders.
    """
    import concurrent.futures as cf

    from resto_agent import agent as A
    from resto_agent.config import Settings

    def build():
        s = Settings()
        return (
            type(A._build_stt(s)).__name__,
            type(A._build_llm(s)).__name__,
            type(A._build_tts(s)).__name__,
        )

    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        stt, llm, tts = ex.submit(build).result()
    assert (stt, llm, tts) == ("STT", "LLM", "TTS")
