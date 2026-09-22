"""Run the mock Libro server: `python -m mock_libro` or `resto-mock-libro`."""

from __future__ import annotations

import argparse
import os

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the mock Libro JSON:API server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db",
        default=os.environ.get("AGENT_MOCK_DB_PATH", "mock_libro_data/resto.sqlite"),
        help="SQLite path (use ':memory:' for an ephemeral store).",
    )
    args = parser.parse_args()

    app = create_app(args.db, seed=True)
    print(f"Mock Libro listening on http://{args.host}:{args.port} (db={args.db})")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
