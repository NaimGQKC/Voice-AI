"""One command for the demo: a real spoken call against the MOCK reservation system.

    python scripts/demo_call.py

What it does, in order:

  1. starts the mock Libro server on :8000 with an empty, in-memory book
  2. opens the live reservation book in your browser  (/demo/book)
  3. runs the voice agent in console mode  -> it uses your microphone and speakers

Put the terminal and the browser side by side and record. Every booking the
caller makes appears in the book as it happens; a cancellation turns it red.
Ctrl+C ends the call and shuts the mock server down.

SAFETY — this cannot touch the real restaurant. The agent is forced onto the
mock backend, and the Libro token and email are blanked in the child process
even if your .env holds the real ones. Nothing here can create a real booking.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BOLD, GREEN, RED, YELLOW, END = "\033[1m", "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def _preflight(allow_english_voice: bool) -> bool:
    """Fail early and loudly: a demo that breaks on camera is the worst outcome."""
    ok = True
    for key, why in (("DEEPGRAM_API_KEY", "speech recognition"),
                     ("GOOGLE_API_KEY", "the language model")):
        if not os.environ.get(key):
            print(f"{RED}missing {key}{END} — needed for {why}. See docs/SETUP.md.")
            ok = False

    if not (os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_URL")):
        msg = ("LiveKit credentials are not set. Without them the agent falls back to "
               "an English-only voice and the French greeting comes out mangled — "
               "exactly what you do not want on camera.")
        if allow_english_voice:
            print(f"{YELLOW}warning:{END} {msg}")
        else:
            print(f"{RED}missing LIVEKIT_URL / LIVEKIT_API_KEY{END} — {msg}")
            print("  (pass --allow-english-voice to run anyway, e.g. to rehearse)")
            ok = False
    return ok


def _wait_for(url: str, seconds: float = 12.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:  # noqa: S310 - localhost
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.25)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the demo call against the mock system.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--name", default="Demo Bistro",
                    help="restaurant name the agent answers as (default: Demo Bistro)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the book page")
    ap.add_argument("--allow-english-voice", action="store_true",
                    help="run even without LiveKit credentials (French will sound wrong)")
    args = ap.parse_args()

    _load_dotenv()
    if not _preflight(args.allow_english_voice):
        return 1

    base = f"http://127.0.0.1:{args.port}"
    demo_db = ROOT / "demo_calls.db"
    for suffix in ("", "-wal", "-shm"):  # our own scratch file: start every run clean
        Path(f"{demo_db}{suffix}").unlink(missing_ok=True)

    env = dict(os.environ)
    env.update({
        "AGENT_RESERVATION_BACKEND": "mock-http",
        "AGENT_MOCK_BASE_URL": base,
        "AGENT_RESTAURANT_NAME": args.name,
        "AGENT_DB_PATH": str(demo_db),
        "AGENT_STORE_TRANSCRIPTS": "1",   # it is your own voice on a mock system
        # Belt and braces: even a stray .env cannot point this at the real floor.
        "LIBRO_PRIVATE_TOKEN": "",
        "LIBRO_PRIVATE_EMAIL": "",
    })

    print(f"{BOLD}Starting the mock reservation system on {base}{END}")
    mock = subprocess.Popen(
        [sys.executable, "-m", "mock_libro", "--port", str(args.port), "--db", ":memory:"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for(f"{base}/demo/bookings.json"):
            print(f"{RED}the mock server did not come up on port {args.port}{END} "
                  "(is something else using it? try --port 8010)")
            return 1

        if not args.no_browser:
            webbrowser.open(f"{base}/demo/book")

        print(f"{GREEN}Mock system is up.{END}  Backend: mock-http — "
              f"{BOLD}no real booking is possible.{END}")
        print(f"Agent answers as {BOLD}{args.name}{END}. Use headphones. Ctrl+C to end.\n")
        return subprocess.call([sys.executable, str(ROOT / "agent.py"), "console"],
                               cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 0
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mock.kill()


if __name__ == "__main__":
    sys.exit(main())
