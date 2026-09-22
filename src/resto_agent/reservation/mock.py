"""MockReservationService — the JSON:API client pointed at the local mock.

Two construction modes:

* ``in_process()`` (default for the POC): wires httpx directly to the FastAPI
  mock app via ASGITransport. No server, no port, no network — ideal for tests
  and ``python agent.py console``.
* ``http()``: talks to a separately running mock server (``python -m
  mock_libro``) over real HTTP, which mirrors the production transport exactly.
"""

from __future__ import annotations

import httpx

from mock_libro.app import create_app
from mock_libro.db import RESTAURANT_ID as MOCK_RESTAURANT_ID

from .jsonapi import JsonApiReservationService


class MockReservationService(JsonApiReservationService):
    @classmethod
    def in_process(
        cls,
        *,
        db_path: str = ":memory:",
        restaurant_id: str = MOCK_RESTAURANT_ID,
        seed: bool = True,
    ) -> "MockReservationService":
        app = create_app(db_path, seed=seed)
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://mock-libro")
        svc = cls(client=client, restaurant_id=restaurant_id)
        svc._app = app  # keep a reference so the app isn't garbage-collected
        return svc

    @classmethod
    def http(
        cls,
        *,
        base_url: str = "http://localhost:8000",
        restaurant_id: str = MOCK_RESTAURANT_ID,
    ) -> "MockReservationService":
        client = httpx.AsyncClient(base_url=base_url)
        return cls(client=client, restaurant_id=restaurant_id)
