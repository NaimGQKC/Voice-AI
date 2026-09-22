"""Environment-driven configuration for the restaurant voice agent."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    """Read a float from env, falling back to ``default`` on anything unusable.

    A typo in an env var must not stop the phone from being answered.
    """
    raw = os.environ.get(key, "")
    if not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    # Reservation backend
    reservation_backend: str = "mock"  # mock | mock-http | libro
    restaurant_id: str = "rest_demo_mtl"
    mock_base_url: str = "http://localhost:8000"
    mock_db_path: str = ":memory:"

    # Real Libro — private dashboard API (token auth). This is the live path.
    libro_private_base_url: str = "https://api.libroreserve.com"
    libro_private_token: str = ""
    libro_private_email: str = ""
    libro_private_restaurant_id: str = "<redacted>"  # the restaurant

    # Voice stack
    #: Default: gemini-2.5-flash. Of every candidate assessed against primary
    #: sources, Google's Flash family is the ONLY one with both a measured TTFT
    #: inside the 200-700ms budget and a prompt cache whose 2,048-token minimum
    #: our ~3,690-token prefix actually clears. See docs/MODEL_SHORTLIST.md.
    #: Swap with AGENT_LLM_PROVIDER; every alternative is a .env change.
    llm_provider: str = "google"
    llm_model: str = ""  # optional override, e.g. "gpt-4o-mini"
    #: Default `multi`: ~2/3 of this venue's callers speak French, and the
    #: greeting is bilingual in every mode, so English-only was never right.
    language_mode: str = "multi"  # multi | en

    #: TTS voice override. A value containing "/" (e.g. "elevenlabs/eleven_flash_v2_5")
    #: is routed through LiveKit Inference on the existing LiveKit key; anything
    #: else is treated as a Deepgram Aura model name. Empty = pick per language
    #: mode (see ``agent._build_tts``).
    tts_model: str = ""

    #: Seconds the LiveKit SDK ignores caller audio at the start of the agent's
    #: FIRST utterance while acoustic echo cancellation warms up.
    #:
    #: ⚠️ The SDK default is 3.0, and during that window it both blocks
    #: interruption AND feeds silence to the STT — so the caller physically
    #: cannot barge in over a greeting, and their first words are never
    #: transcribed. Our greeting is ~1s, so the SDK default would make the whole
    #: greeting (and ~2s after it) uninterruptible. See
    #: docs/GREETING_ABANDONMENT.md cause #2. Default 0.0 = barge-in from the
    #: first frame. Raise it via AGENT_AEC_WARMUP_S if echo ever self-interrupts
    #: the greeting.
    aec_warmup_s: float = 0.0

    #: Durable call/message log. MUST point at a persistent volume in production
    #: — on ephemeral container disk every restart silently drops messages the
    #: agent promised to pass on.
    db_path: str = "resto_calls.db"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            reservation_backend=_env("AGENT_RESERVATION_BACKEND", "mock"),
            restaurant_id=_env("AGENT_RESTAURANT_ID", "rest_demo_mtl"),
            mock_base_url=_env("AGENT_MOCK_BASE_URL", "http://localhost:8000"),
            mock_db_path=_env("AGENT_MOCK_DB_PATH", ":memory:"),
            libro_private_base_url=_env("LIBRO_PRIVATE_BASE_URL", "https://api.libroreserve.com"),
            libro_private_token=_env("LIBRO_PRIVATE_TOKEN"),
            libro_private_email=_env("LIBRO_PRIVATE_EMAIL"),
            libro_private_restaurant_id=_env("LIBRO_PRIVATE_RESTAURANT_ID", "<redacted>"),
            llm_provider=_env("AGENT_LLM_PROVIDER", "google"),
            llm_model=_env("AGENT_LLM_MODEL", ""),
            language_mode=_env("AGENT_LANGUAGE_MODE", "multi"),
            tts_model=_env("AGENT_TTS_MODEL", ""),
            aec_warmup_s=_env_float("AGENT_AEC_WARMUP_S", 0.0),
            db_path=_env("AGENT_DB_PATH", "resto_calls.db"),
        )

    @property
    def is_multilingual(self) -> bool:
        return self.language_mode.lower() in ("multi", "bilingual", "fr-en", "fr+en")
