"""LiveKit Agents entrypoint and voice-stack wiring for the restaurant agent.

Run modes (after `pip install -e ".[agent]"` and filling in `.env`):

    python -m resto_agent.agent console   # local terminal audio, no server
    python -m resto_agent.agent dev       # connect to LiveKit Cloud + browser test

The reservation backend is chosen by env (`AGENT_RESERVATION_BACKEND`), defaulting
to the in-process mock — so this runs end-to-end with no Libro credentials.

Structure follows LiveKit's recommended patterns: bundled VAD, metrics + usage
collection, hosted semantic turn detection, telephony noise cancellation, and
the current date injected into the prompt so relative dates ("this Friday")
resolve correctly. Compatible with livekit-agents >= 1.6.4.

Model identifiers below are the recommended low-cost stack. Plugin model names
occasionally change between releases; verify them against the installed plugin
version if a model isn't found.
"""

from __future__ import annotations

import datetime as dt
import logging

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from dotenv import load_dotenv
from livekit.agents import (
    AgentSession,
    JobContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    metrics,
)
# Plugins MUST be imported at module top level: LiveKit registers each plugin at
# import time and requires that to happen on the main thread. Importing them
# lazily inside a builder (which runs in the job worker thread) raises
# "Plugins must be registered on the main thread". deepgram/google/openai ship
# with the [agent] extra; noise_cancellation is optional.
from livekit.plugins import deepgram, google, openai

try:
    from livekit.plugins import noise_cancellation
except Exception:  # pragma: no cover - optional plugin
    noise_cancellation = None

from .concierge import Concierge
from .config import Settings
from .notify import build_notifier
from .prompts import disclosure_reminder, greeting_for, system_instructions
from .reservation import build_service
from .store import CallStore
from .tools import ReservationAgent

try:
    from zoneinfo import ZoneInfo

    _MTL = ZoneInfo("America/Toronto")
except Exception:  # pragma: no cover
    _MTL = dt.timezone(dt.timedelta(hours=-4))

logger = logging.getLogger("resto-agent")


def _montreal_today() -> dt.date:
    return dt.datetime.now(_MTL).date()


def _build_stt(settings: Settings):
    # Deepgram Nova-3: monolingual English for the MVP, multilingual for FR/EN.
    if settings.is_multilingual:
        return deepgram.STT(model="nova-3", language="multi")
    return deepgram.STT(model="nova-3", language="en")


#: Prompt-cache key. **Constant, not per-call — that is deliberate.**
#:
#: Our fixed prefix (system prompt + 9 tool schemas, ~3,450 tokens) is *identical
#: for every caller*, so a shared key lets call #2 hit the cache warmed by call
#: #1. A per-call key would throw that away and pay full prefill on every call's
#: first turn. OpenAI only splits traffic off a shared key above ~15 req/min; we
#: run about 3 calls a day, so we are nowhere near it.
#:
#: Bump the suffix whenever the system prompt or tool schemas change, so a stale
#: prefix is never served.
PROMPT_CACHE_KEY = "resto-agent-v1"


@dataclass(frozen=True)
class _OpenAICompatible:
    """An OpenAI-chat-completions-compatible endpoint.

    Every provider here needs **zero code** — a base URL, a key, and a model
    name. That is the whole reason the shortlist isn't only US vendors: Qwen,
    DeepSeek, Moonshot, Z.ai and Mistral all speak this dialect, so trying one is
    a `.env` change, not an integration.
    """

    base_url: str
    env_key: str
    default_model: str
    #: OpenAI-specific; other vendors reject the field.
    supports_cache_key: bool = False


OPENAI_COMPATIBLE: dict[str, _OpenAICompatible] = {
    # -- US ------------------------------------------------------------------
    "openai": _OpenAICompatible("", "OPENAI_API_KEY", "gpt-4o-mini",
                                supports_cache_key=True),
    "groq": _OpenAICompatible("https://api.groq.com/openai/v1", "GROQ_API_KEY",
                              "llama-3.3-70b-versatile"),
    "cerebras": _OpenAICompatible("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
                                  "llama-3.3-70b"),
    "xai": _OpenAICompatible("https://api.x.ai/v1", "XAI_API_KEY",
                             "grok-4-1-fast-non-reasoning"),
    # -- non-US: cheap, strong, and genuinely multilingual --------------------
    # Worth testing rather than assuming. These train on far more non-English
    # text than the US labs, which is the opposite of what our venue's
    # two-thirds-French call mix would suggest ignoring.
    # Model IDs corrected against each vendor's own current model list — three of
    # the names originally guessed here were stale. See docs/MODEL_SHORTLIST.md.
    "qwen": _OpenAICompatible(
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY", "qwen3.7-plus"),   # "qwen-plus" is a legacy alias
    "moonshot": _OpenAICompatible("https://api.moonshot.ai/v1",
                                  "MOONSHOT_API_KEY", "kimi-k2.6"),
    "zhipu": _OpenAICompatible("https://api.z.ai/api/paas/v4",
                               "ZHIPU_API_KEY", "glm-4.6"),
    # deepseek: `deepseek-chat` no longer exists in DeepSeek's model list, and both
    # current models default to THINKING mode — disqualifying on a TTFT budget.
    "deepseek": _OpenAICompatible("https://api.deepseek.com/v1",
                                  "DEEPSEEK_API_KEY", "deepseek-v4-flash"),
    # mistral: `mistral-small-latest` now resolves to Mistral Small 4, which has no
    # BFCL entry and no published latency. Every number circulating describes
    # Mistral Small 3.2, retired 2026-07-31. Kept for testing, not recommended.
    "mistral": _OpenAICompatible("https://api.mistral.ai/v1",
                                 "MISTRAL_API_KEY", "mistral-small-latest"),
}


def _build_llm(settings: Settings):
    """Pick the runtime LLM.

    ⚠️ **DO NOT SHIP ON A FREE TIER.** An earlier version of this docstring said
    Groq's free tier gives "30 req/min, ~14,400/day" with "sub-100ms first token".
    Both numbers were wrong for this model and this workload:

    * **The binding limit is TOKENS, not requests.** Our fixed per-turn prompt is
      ~3,450 tokens (system prompt ~1,995 + 9 tool schemas ~1,457). Against the
      free tier's 12,000 TPM for llama-3.3-70b that is **~3.5 requests/minute**,
      while a live call needs 6-12. The 30 RPM cap binds ~8.6x later — a red
      herring. Groq's prompt caching would help, but it covers `gpt-oss-*` models
      only, so our prefix is re-billed every single turn.
    * The daily token cap works out to roughly **2 calls/day**; this venue takes
      about 3.2. So the free tier is unusable here on volume alone, regardless of
      latency.
    * **Do not "fix" latency by dropping to llama-3.1-8b-instant** — its free-tier
      budget is 6,000 TPM, *half* the 70B's. If throttling is the problem, the
      smaller model makes it worse while also costing tool-calling accuracy.

    See docs/LLM_BENCHMARK.md and run scripts/benchmark_llm.py to settle it with
    measurements: Groq returns `queue_time` / `prompt_time` / `completion_time` on
    every response, which decomposes TTFT directly.

    Provider options:

      google   DEFAULT (gemini-2.5-flash). NOTE: the free tier is thin and has
               already rate-limited a live voice test — enable billing before
               this goes on a real line.
      groq     fast hardware; OpenAI-compatible. Use a PAID plan — see above.
      cerebras high daily token volume.
      xai      xAI's Grok.
      openai   pay-as-you-go, no daily cap.
      livekit  routed through LiveKit Inference on your existing LiveKit key.
    """
    provider = (settings.llm_provider or "google").lower()
    model = settings.llm_model

    if provider in ("anthropic", "claude"):
        from livekit.plugins import anthropic

        # caching=True marks the system prompt + tool schemas as a cache breakpoint.
        return anthropic.LLM(model=model or "claude-haiku-4-5-20251001", caching=True)

    if provider in ("google", "gemini"):
        return google.LLM(model=model or "gemini-2.5-flash")

    if provider == "livekit":
        from livekit.agents import inference

        return inference.LLM(model=model or "google/gemini-2.5-flash")

    spec = OPENAI_COMPATIBLE.get(provider)
    if spec is None:
        raise ValueError(
            f"Unknown AGENT_LLM_PROVIDER={provider!r}. Known: "
            + ", ".join(sorted([*OPENAI_COMPATIBLE, "anthropic", "google", "livekit"]))
        )

    key = os.environ.get(spec.env_key)
    if not key and spec.env_key != "OPENAI_API_KEY":
        logger.warning("%s is not set; %s will fail to authenticate",
                       spec.env_key, provider)

    kwargs = dict(model=model or spec.default_model, api_key=key)
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    if spec.supports_cache_key:
        # See PROMPT_CACHE_KEY: a constant key, deliberately.
        kwargs["prompt_cache_key"] = PROMPT_CACHE_KEY
    return openai.LLM(**kwargs)


#: Deepgram Aura-2 voice used for the English-only build.
DEEPGRAM_VOICE = "aura-2-thalia-en"

#: Multilingual voice used when the greeting is French. Routed through LiveKit
#: Inference, so it costs **no additional API key** — it authenticates with the
#: LiveKit credentials the agent already needs to take a phone call at all.
MULTILINGUAL_VOICE = "cartesia/sonic-3"
#: Language passed alongside the model. Cartesia needs to be TOLD the language —
#: it does not infer it from the text, and an unset language reads French with an
#: English voice, which is the bug this constant exists to prevent.
MULTILINGUAL_LANG = "fr"


def _has_livekit_cloud() -> bool:
    return bool(os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_URL"))


def _build_tts(settings: Settings):
    """Pick a voice that can actually pronounce what we ask it to say.

    Deliberately single-provider by default: every extra vendor is another key
    that can expire and another bill that can fail on a system meant to run
    unattended. Deepgram gives us STT and TTS on one key.

    ⚠️ But **every Deepgram Aura / Aura-2 voice is English-only** (every model
    id ends in ``-en``; see ``livekit.plugins.deepgram.models.TTSModels``). Our
    greeting is now French first, for a venue whose calls are two-thirds French
    — and an English voice reading "<name>, bonjour !" produces exactly the
    mangled, obviously-foreign pronunciation that makes a Québécois caller hang
    up. That defeats the change it is meant to serve.

    So in multilingual mode we use a multilingual voice via LiveKit Inference,
    which needs no new vendor account. Without LiveKit credentials (plain
    ``console`` mode) we fall back to Deepgram and say so loudly, because the
    French will sound wrong and that must not be discovered on a live call.

    ``AGENT_TTS_MODEL`` overrides both: a value with a "/" goes through LiveKit
    Inference, anything else is treated as a Deepgram model name.
    """
    override = (settings.tts_model or "").strip()
    if override:
        if "/" in override:
            from livekit.agents import inference

            return inference.TTS(model=override)
        return deepgram.TTS(model=override)

    # The greeting is "Bonjour, Hi. the restaurant" in EVERY mode, so the
    # voice must speak French regardless of AGENT_LANGUAGE_MODE. Gating this on
    # is_multilingual was a bug: with the flag unset the agent read a French
    # greeting through an English-only Deepgram voice, which is precisely the
    # mangled pronunciation this whole choice exists to avoid.
    if _has_livekit_cloud():
        from livekit.agents import inference

        return inference.TTS(model=MULTILINGUAL_VOICE,
                             language=MULTILINGUAL_LANG)

    logger.warning(
        "No LiveKit credentials: falling back to the English-only Deepgram voice "
        "%s. The greeting is 'Bonjour, Hi. the restaurant' in every mode, "
        "so the French WILL be mispronounced — this was caught on the first live "
        "test. Set LIVEKIT_API_KEY/LIVEKIT_URL, or AGENT_TTS_MODEL, before putting "
        "this on a real line.",
        DEEPGRAM_VOICE,
    )
    return deepgram.TTS(model=DEEPGRAM_VOICE)


def _ms(a: float, b: float) -> int:
    return int(round((b - a) * 1000))


@dataclass
class GreetingTelemetry:
    """What the caller actually experienced in the first seconds of the call.

    19% of real calls at this venue ended with the caller never speaking
    (docs/GREETING_ABANDONMENT.md). We cannot test on real users, so the only
    way any greeting change is falsifiable is to measure the call itself. This
    class is the measuring instrument; :meth:`as_row` feeds
    ``CallStore.record_greeting``.

    Deliberately pure and clock-injectable — no LiveKit types, no I/O — so the
    timing arithmetic is testable without audio, a room, or a network.

    Every ``mark_*`` is idempotent-first-wins: the interesting number is when
    something happened for the FIRST time, and later events must not overwrite
    it. Unmeasured stays ``None`` rather than becoming 0, because "we never
    heard them" and "they answered instantly" are opposite findings.
    """

    now: Callable[[], float] = time.monotonic
    answered_at: float | None = None
    first_word_at: float | None = None
    greeting_done_at: float | None = None
    first_user_speech_at: float | None = None
    greeting_interrupted: bool = False
    disclosure_spoken: bool = False
    detected_language: str = ""
    _langs: list[str] = field(default_factory=list)

    # -- marks -------------------------------------------------------------
    def mark_answered(self) -> None:
        if self.answered_at is None:
            self.answered_at = self.now()

    def mark_agent_speaking(self) -> None:
        """First audible word out of the agent — the end of the dead-air window."""
        if self.first_word_at is None:
            self.first_word_at = self.now()

    def mark_greeting_done(self, *, interrupted: bool, disclosure_spoken: bool) -> None:
        if self.greeting_done_at is None:
            self.greeting_done_at = self.now()
        self.greeting_interrupted = interrupted
        self.disclosure_spoken = disclosure_spoken

    def mark_user_spoke(self, *, language: str = "") -> None:
        if self.first_user_speech_at is None:
            self.first_user_speech_at = self.now()
        # Language arrives with the transcript, which is later than the VAD
        # onset — so it is recorded independently of the first-speech instant.
        if language and language not in self._langs:
            self._langs.append(language)
        if language and not self.detected_language:
            self.detected_language = language

    # -- derived -----------------------------------------------------------
    @property
    def user_spoke(self) -> bool:
        return self.first_user_speech_at is not None

    @property
    def answer_to_first_word_ms(self) -> int | None:
        if self.answered_at is None or self.first_word_at is None:
            return None
        return _ms(self.answered_at, self.first_word_at)

    @property
    def greeting_ms(self) -> int | None:
        if self.first_word_at is None or self.greeting_done_at is None:
            return None
        return _ms(self.first_word_at, self.greeting_done_at)

    @property
    def first_user_speech_ms(self) -> int | None:
        if self.answered_at is None or self.first_user_speech_at is None:
            return None
        return _ms(self.answered_at, self.first_user_speech_at)

    def as_row(self) -> dict:
        """Only what was actually measured. ``None`` = don't write this field."""
        return {
            "answer_to_first_word_ms": self.answer_to_first_word_ms,
            "greeting_ms": self.greeting_ms,
            "greeting_interrupted": True if self.greeting_interrupted else None,
            "user_spoke": True if self.user_spoke else None,
            "first_user_speech_ms": self.first_user_speech_ms,
            "detected_language": self.detected_language or None,
            "disclosure_spoken": True if self.disclosure_spoken else None,
        }


def _attach_greeting_telemetry(session, tele: GreetingTelemetry) -> None:
    """Wire the SDK's own events into the telemetry record.

    ``agent_state_changed -> speaking`` is the moment audio starts flowing, and
    ``user_state_changed -> speaking`` is VAD onset — earlier and more honest
    than waiting for a transcript, because a caller who says "allô" and hangs up
    still counts as having spoken.
    """

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:  # pragma: no cover - needs a live session
        if getattr(ev, "new_state", "") == "speaking":
            tele.mark_agent_speaking()

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:  # pragma: no cover - needs a live session
        if getattr(ev, "new_state", "") == "speaking":
            tele.mark_user_spoke()

    @session.on("user_input_transcribed")
    def _on_transcribed(ev) -> None:  # pragma: no cover - needs a live session
        tele.mark_user_spoke(language=getattr(ev, "language", None) or "")


def _build_turn_handling(settings: Settings) -> TurnHandlingOptions:
    """Semantic turn detection + preemptive generation (agents SDK >= 1.6.6 API).

    The hosted ``inference.TurnDetector`` runs on LiveKit Cloud (included on the
    free Build tier), so it is only enabled when LiveKit credentials are present
    — in plain ``console`` mode without a cloud project we fall back to VAD
    turn-taking rather than fail at runtime.
    """

    # Preemptive generation: start the LLM as soon as the caller is *likely*
    # done, discarding the draft if they keep talking — cuts perceived latency.
    opts = TurnHandlingOptions(preemptive_generation={"enabled": True})
    if os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_URL"):
        try:
            from livekit.agents import inference

            opts["turn_detection"] = inference.TurnDetector()
        except Exception:  # pragma: no cover - keep the call alive regardless
            logger.warning("Hosted turn detector unavailable; using VAD turn-taking.")
    else:
        logger.info("No LiveKit credentials; using VAD turn-taking (console mode).")
    return opts


async def entrypoint(ctx: JobContext) -> None:
    load_dotenv()
    settings = Settings.from_env()

    # The clock starts the moment we pick up: everything below is measured
    # against this instant, because that is what the caller experiences.
    tele = GreetingTelemetry()
    tele.mark_answered()

    service = build_service(settings)
    locale = "fr" if settings.is_multilingual else "en"
    today = _montreal_today()

    # Durable call record. Without this, messages and waitlist captures die when
    # the caller hangs up — and the agent promises they won't.
    store = CallStore(settings.db_path)
    notifier = build_notifier()
    call_id = getattr(ctx.room, "name", "") or uuid.uuid4().hex[:12]
    store.start_call(call_id, locale=locale)

    concierge = Concierge(service, locale=locale, today=today,
                          store=store, notifier=notifier, call_id=call_id)
    base_instructions = system_instructions(
        multilingual=settings.is_multilingual, today=today.isoformat()
    )
    agent = ReservationAgent(concierge, instructions=base_instructions)

    # VAD: AgentSession bundles silero by default, so no explicit vad= needed.
    #
    # aec_warmup_duration: THE BARGE-IN FIX. See docs/GREETING_ABANDONMENT.md
    # cause #2 ("the caller can't interrupt it"). Passing
    # `allow_interruptions=True` to `session.say()` is necessary but was NOT
    # sufficient: livekit-agents defaults `aec_warmup_duration=3.0`, and for the
    # first 3s of the agent's first utterance the SDK
    #   * returns early from `AgentActivity._interrupt_by_audio_activity`, and
    #   * substitutes silence frames into the STT stream (`push_audio`)
    # so a caller talking over the greeting is neither able to interrupt it nor
    # transcribed at all — which also costs us the language detection we rely on
    # to switch to English. Our whole greeting is ~1s, so the SDK default made
    # 100% of it uninterruptible.
    #
    # The tradeoff we are accepting: AEC warmup exists so the agent doesn't
    # self-interrupt on its own echo. We take that risk because (a) this runs
    # over SIP telephony, where echo control is the carrier's job rather than a
    # local AEC that needs to converge, and (b) `resume_false_interruption` is
    # on by default, so a mis-fire resumes the greeting instead of killing it.
    # If truncated greetings ever show up, AGENT_AEC_WARMUP_S raises it back
    # without a code change.
    session = AgentSession(
        stt=_build_stt(settings),
        llm=_build_llm(settings),
        tts=_build_tts(settings),
        turn_handling=_build_turn_handling(settings),
        aec_warmup_duration=settings.aec_warmup_s or None,
    )
    _attach_greeting_telemetry(session, tele)

    # -- observability: log per-turn metrics and a usage summary at end -----
    usage = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        metrics.log_metrics(ev.metrics)
        usage.collect(ev.metrics)

    async def _log_usage() -> None:
        logger.info("call usage summary: %s", usage.get_summary())

    def _collect_transcript() -> str:
        """Render the conversation for the dashboard's per-call view.

        ⚠️ A transcript is materially more sensitive than a name and a number —
        it can contain anything a caller said. It is therefore OPT-IN via
        AGENT_STORE_TRANSCRIPTS, and covered by the same retention purge as
        everything else (docs/DATA_RETENTION.md).
        """
        if os.environ.get("AGENT_STORE_TRANSCRIPTS", "").lower() not in ("1", "true", "yes"):
            return ""
        try:
            lines = []
            for item in session.history.items:
                role = getattr(item, "role", "")
                if role not in ("user", "assistant"):
                    continue
                text = getattr(item, "text_content", None) or ""
                if text:
                    who = "Caller" if role == "user" else "Agent"
                    lines.append(f"{who}: {text}")
            return "\n".join(lines)
        except Exception:
            logger.exception("could not collect transcript")
            return ""

    async def _close_call_record() -> None:
        try:
            # Last write wins for anything measured after the greeting — most
            # importantly whether the caller EVER spoke, which is the number
            # this whole exercise exists to move.
            store.record_greeting(call_id, **tele.as_row())
            outcome = "completed" if tele.user_spoke else "no_user_turn"
            store.end_call(call_id, outcome=outcome,
                           transcript=_collect_transcript())
        finally:
            store.close()

    ctx.add_shutdown_callback(_log_usage)
    ctx.add_shutdown_callback(_close_call_record)
    ctx.add_shutdown_callback(service.aclose)

    # Krisp telephony noise cancellation (BVC) is a LiveKit Cloud feature, so it
    # only applies when connected with credentials — not in local console mode.
    room_input_options = None
    has_cloud = _has_livekit_cloud()
    if noise_cancellation is not None and has_cloud:
        try:
            room_input_options = RoomInputOptions(noise_cancellation=noise_cancellation.BVC())
        except Exception:  # pragma: no cover - keep the call alive regardless
            logger.info("noise cancellation unavailable; continuing without it.")

    await session.start(agent=agent, room=ctx.room, room_input_options=room_input_options)
    await ctx.connect()

    await _speak_greeting(session, agent, settings, tele, base_instructions)
    store.record_greeting(call_id, **tele.as_row())


async def _speak_greeting(session, agent, settings: Settings,
                          tele: GreetingTelemetry, base_instructions: str) -> None:
    """Say the greeting, then the AI disclosure, as two interruptible utterances.

    Two `say()` calls rather than one sentence, and this is the whole point of
    the disclosure design in ``prompts.py``:

    * The caller hears "<name>, bonjour !" (~1s) and can answer immediately.
    * The disclosure follows as its own speech handle. If they have already
      started talking, we simply never start it — we do not talk over a caller
      to read them a compliance line.
    * If the disclosure did not reach them, the model's instructions are updated
      so it discloses in its first substantive reply. Deferred, never dropped.

    ``session.say()`` is used rather than ``generate_reply()`` on purpose: the
    text is verbatim, so the model cannot pad it back into the 13-second
    monologue that caused this bug in the first place.
    """
    greeting, disclosure = greeting_for(settings.is_multilingual)

    handle = session.say(greeting, allow_interruptions=True)
    await handle.wait_for_playout()
    interrupted = bool(handle.interrupted)

    disclosure_spoken = False
    if not interrupted:
        dh = session.say(disclosure, allow_interruptions=True)
        await dh.wait_for_playout()
        disclosure_spoken = not dh.interrupted
        interrupted = bool(dh.interrupted)

    tele.mark_greeting_done(interrupted=interrupted, disclosure_spoken=disclosure_spoken)

    if not disclosure_spoken:
        agent.update_instructions(
            base_instructions + disclosure_reminder(settings.is_multilingual)
        )
        logger.info("greeting barged over; disclosure deferred to the first reply")

    logger.info(
        "greeting telemetry: answer->first word %sms, greeting %sms, interrupted=%s",
        tele.answer_to_first_word_ms, tele.greeting_ms, tele.greeting_interrupted,
    )


def main() -> None:
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
