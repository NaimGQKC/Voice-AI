"""Serve the read-only call dashboard.

    python scripts/dashboard.py                    # http://localhost:8080
    python scripts/dashboard.py --port 9000
    AGENT_DASHBOARD_TOKEN=secret python scripts/dashboard.py   # require ?token=

Reads the same call log the agent writes to (`AGENT_DB_URL`, or `AGENT_DB_PATH` for
a local file). It only ever reads, so running it against a live database while
calls are in progress is safe — SQLite is in WAL mode precisely so a reader
never blocks the agent mid-call.

⚠️ If you expose this beyond localhost, SET `AGENT_DASHBOARD_TOKEN`. Without it
the page is open to anyone who can reach the port, and it shows guest names.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the call dashboard")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default localhost only)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default="", help="override the call log path/URL")
    args = ap.parse_args()

    if args.db:
        os.environ["AGENT_DB_PATH"] = args.db

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed.  pip install uvicorn", file=sys.stderr)
        return 1

    from resto_agent.dashboard import build_app

    if args.host != "127.0.0.1" and not os.environ.get("AGENT_DASHBOARD_TOKEN"):
        print("REFUSING TO START: binding beyond localhost without "
              "AGENT_DASHBOARD_TOKEN would publish guest names and phone "
              "numbers to anyone who can reach this port.\n"
              "Set AGENT_DASHBOARD_TOKEN=<something long> and retry.",
              file=sys.stderr)
        return 2

    print(f"dashboard -> http://{args.host}:{args.port}")
    if not os.environ.get("AGENT_DASHBOARD_TOKEN"):
        print("  (no token set — fine for localhost, not for anything public)")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
