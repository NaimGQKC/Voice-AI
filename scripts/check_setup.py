"""Preflight check: is this machine ready to run the voice agent?

Run after filling in `.env`:

    python scripts/check_setup.py

Checks, in order:
  1. .env exists and the required values are filled in (not placeholders)
  2. The [agent] extras are installed
  3. Each API key actually works (live calls to Deepgram / Google / LiveKit)
  4. The reservation backend + concierge logic runs end to end locally

Exit code 0 = everything green, you can run `python agent.py console`.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

GREEN, RED, YELLOW, DIM, END = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
_failures: list[str] = []
_warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"  {GREEN}[ok]{END}   {msg}")


def fail(msg: str, hint: str = "") -> None:
    print(f"  {RED}[FAIL]{END} {msg}")
    if hint:
        print(f"         {DIM}{hint}{END}")
    _failures.append(msg)


def warn(msg: str, hint: str = "") -> None:
    print(f"  {YELLOW}[warn]{END} {msg}")
    if hint:
        print(f"         {DIM}{hint}{END}")
    _warnings.append(msg)


def _placeholder(value: str) -> bool:
    v = (value or "").strip()
    return not v or v.startswith("PASTE_") or "YOUR-PROJECT" in v


# ---------------------------------------------------------------- 1. .env ---
def check_env_file() -> None:
    print("\n1) .env file")
    env_path = ROOT / ".env"
    if not env_path.exists():
        fail(".env not found",
             "Run: cp .env.example .env  — then fill in the 2 TIER 1 keys.")
        return
    ok(".env exists")

    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        # Minimal parse so key checks still work without python-dotenv.
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    # TIER 1 — everything you need to TALK to the agent in console mode.
    # LiveKit is NOT here on purpose: `console` runs entirely on your laptop and
    # falls back to VAD turn-taking without it. Demanding it would block the
    # 10-minute path on a third account nobody needs yet.
    required = {
        "DEEPGRAM_API_KEY": "https://console.deepgram.com/signup → API Keys",
    }
    llm_keys = {
        "groq": ("GROQ_API_KEY", "https://console.groq.com/keys"),
        "cerebras": ("CEREBRAS_API_KEY", "https://cloud.cerebras.ai"),
        "xai": ("XAI_API_KEY", "https://console.x.ai"),
        "openai": ("OPENAI_API_KEY", "https://platform.openai.com/api-keys"),
        "google": ("GOOGLE_API_KEY", "https://aistudio.google.com/apikey"),
        "livekit": (None, "(uses your LiveKit credentials)"),
    }
    provider = os.environ.get("AGENT_LLM_PROVIDER", "google").lower()
    key_name, where = llm_keys.get(provider, llm_keys["google"])
    print(f"  (LLM provider: {provider})")
    if key_name:
        required[key_name] = where

    for key, where in required.items():
        if _placeholder(os.environ.get(key, "")):
            fail(f"{key} is not set", f"Get it from: {where}")
        else:
            ok(f"{key} is set")

    # TIER 3 — only needed for a browser or phone call, never for `console`.
    lk = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
          if not _placeholder(os.environ.get(k, ""))]
    if len(lk) == 3:
        ok("LiveKit credentials set (browser + phone calls available)")
        url = os.environ.get("LIVEKIT_URL", "")
        if not url.startswith("wss://"):
            warn("LIVEKIT_URL should start with wss://", f"Current value: {url}")
    elif lk:
        warn(f"LiveKit partially configured ({len(lk)}/3)",
             "All three are needed. See docs/SETUP.md Tier 4.")
    else:
        print("  \033[2m  (LiveKit not set — fine; `console` mode doesn't need it)\033[0m")


# ------------------------------------------------------------ 2. installs ---
def check_installs() -> None:
    print("\n2) Python packages")
    try:
        import livekit.agents as lka

        ok(f"livekit-agents {lka.__version__}")
    except ImportError:
        fail("livekit-agents not installed", 'Run: pip install -e ".[agent]"')
        return
    for plugin in ("deepgram", "google"):
        try:
            __import__(f"livekit.plugins.{plugin}")
            ok(f"plugin: {plugin}")
        except ImportError:
            fail(f"plugin missing: {plugin}", 'Run: pip install -e ".[agent]"')
    try:
        __import__("livekit.plugins.noise_cancellation")
        ok("plugin: noise_cancellation")
    except ImportError:
        warn("noise_cancellation plugin missing (calls still work, phone audio is noisier)")


# ------------------------------------------------------ 3. live key checks ---
async def check_keys_live() -> None:
    print("\n3) API keys (live checks)")
    try:
        import httpx
    except ImportError:
        fail("httpx not installed", 'Run: pip install -e ".[dev]"')
        return

    async with httpx.AsyncClient(timeout=15) as client:
        # Deepgram: auth-scoped endpoint validates the key.
        dg = os.environ.get("DEEPGRAM_API_KEY", "")
        if _placeholder(dg):
            warn("skipping Deepgram check (key not set)")
        else:
            try:
                r = await client.get(
                    "https://api.deepgram.com/v1/projects",
                    headers={"Authorization": f"Token {dg}"},
                )
                if r.status_code == 200:
                    ok("Deepgram key works")
                elif r.status_code in (401, 403):
                    fail("Deepgram key rejected (401/403)",
                         "Re-copy it from https://console.deepgram.com")
                else:
                    warn(f"Deepgram returned HTTP {r.status_code} (key may still be fine)")
            except Exception as exc:
                warn(f"Could not reach Deepgram: {type(exc).__name__}",
                     "Check your internet connection / proxy.")

        # OpenAI-compatible providers: validate the key AND list usable models,
        # so you never have to guess a model name that still exists.
        provider = os.environ.get("AGENT_LLM_PROVIDER", "groq").lower()
        _compatible = {
            "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1/models",
                     "https://console.groq.com/keys"),
            "xai": ("XAI_API_KEY", "https://api.x.ai/v1/models",
                    "https://console.x.ai"),
            "cerebras": ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/models",
                         "https://cloud.cerebras.ai"),
            "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1/models",
                       "https://platform.openai.com/api-keys"),
        }
        if provider in _compatible:
            env_name, url, console = _compatible[provider]
            key = os.environ.get(env_name, "")
            if _placeholder(key):
                warn(f"skipping {provider} check ({env_name} not set)")
            else:
                try:
                    r = await client.get(url, headers={"Authorization": f"Bearer {key}"})
                    if r.status_code == 200:
                        ok(f"{provider} key works")
                        try:
                            ids = [m.get("id") for m in (r.json().get("data") or [])]
                            ids = [i for i in ids if i][:12]
                            if ids:
                                print(f"         {DIM}available models: "
                                      f"{', '.join(ids)}{END}")
                                chosen = os.environ.get("AGENT_LLM_MODEL", "")
                                if chosen and chosen not in ids:
                                    warn(f"AGENT_LLM_MODEL='{chosen}' is not in that list",
                                         "Set it to one of the models above.")
                        except Exception:  # noqa: BLE001
                            pass
                    elif r.status_code in (401, 403):
                        fail(f"{provider} key rejected", f"Re-copy it from {console}")
                    else:
                        warn(f"{provider} returned HTTP {r.status_code}")
                except Exception as exc:
                    warn(f"Could not reach {provider}: {type(exc).__name__}")
        elif provider == "google":
            gk = os.environ.get("GOOGLE_API_KEY", "")
            if _placeholder(gk):
                warn("skipping Gemini check (key not set)")
            else:
                try:
                    r = await client.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        params={"key": gk, "pageSize": 1},
                    )
                    if r.status_code == 200:
                        ok("Google Gemini key works")
                    elif r.status_code in (400, 401, 403):
                        fail("Google Gemini key rejected",
                             "Re-create it at https://aistudio.google.com/apikey")
                    else:
                        warn(f"Gemini returned HTTP {r.status_code}")
                except Exception as exc:
                    warn(f"Could not reach Google: {type(exc).__name__}")
        elif provider == "livekit":
            ok("LLM routed via LiveKit Inference (uses your LiveKit credentials)")
        else:
            warn(f"unknown AGENT_LLM_PROVIDER='{provider}'",
                 "Use one of: groq, xai, cerebras, openai, livekit, google.")

    # LiveKit: mint a local access token (validates key/secret format+pairing).
    lk_key = os.environ.get("LIVEKIT_API_KEY", "")
    lk_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if _placeholder(lk_key) or _placeholder(lk_secret):
        warn("skipping LiveKit token check (credentials not set)")
    else:
        try:
            from livekit import api as lk_api

            token = (
                lk_api.AccessToken(lk_key, lk_secret)
                .with_identity("preflight")
                .with_grants(lk_api.VideoGrants(room_join=True, room="preflight"))
                .to_jwt()
            )
            ok("LiveKit token mints locally (key/secret pair is well-formed)")
            if len(token) < 50:
                warn("Generated token looks suspiciously short")
        except ImportError:
            warn("livekit-api not importable; skipping token check")
        except Exception as exc:
            fail(f"LiveKit token generation failed: {exc}",
                 "Re-copy BOTH the key and secret from the same LiveKit API key entry.")


# ------------------------------------------------- 4. local logic dry run ---
async def check_logic() -> None:
    print("\n4) Reservation logic (offline dry run)")
    try:
        import datetime as dt

        from resto_agent.concierge import Concierge
        from resto_agent.reservation.mock import MockReservationService

        svc = MockReservationService.in_process(db_path=":memory:")
        c = Concierge(svc, today=dt.date.today())
        msg = await c.check_availability(date="tomorrow", party_size=2, part_of_day="dinner")
        await svc.aclose()
        if "PM" in msg:
            ok(f'concierge answered: "{msg[:60]}..."')
        else:
            warn(f"concierge answered unexpectedly: {msg[:80]}")
    except Exception as exc:
        fail(f"logic dry run failed: {type(exc).__name__}: {exc}",
             "Run `pytest -q` for details.")


def main() -> int:
    print("restaurant voice agent — preflight check")
    check_env_file()
    check_installs()
    asyncio.run(check_keys_live())
    asyncio.run(check_logic())

    print()
    if _failures:
        print(f"{RED}✗ {len(_failures)} problem(s) to fix — see [FAIL] lines above.{END}")
        return 1
    if _warnings:
        print(f"{YELLOW}✓ Ready with {len(_warnings)} warning(s).{END}", end=" ")
    else:
        print(f"{GREEN}✓ All green.{END}", end=" ")
    print("Next:  python agent.py console   (talk to it in your terminal)")
    print("Then:  python agent.py dev       (test in the browser via LiveKit's Agent Console)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
