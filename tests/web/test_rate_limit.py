"""Rate-limit mechanism test.

Uses an isolated app + Limiter so exhausting the budget cannot pollute the
shared application's in-memory limiter state used by the other web tests.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _make_app() -> FastAPI:
    limiter = Limiter(key_func=get_remote_address)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    @app.get("/ping")
    @limiter.limit("2/minute")
    async def ping(request: Request) -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_rate_limit_returns_429_after_budget_exhausted() -> None:
    client = TestClient(_make_app())
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    # third request within the same minute exceeds 2/minute
    assert client.get("/ping").status_code == 429
