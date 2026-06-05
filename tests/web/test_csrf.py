"""CSRF protection tests for admin forms (double-submit cookie)."""

from __future__ import annotations

import re
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from planer.adapters.db import create_tables, make_engine
from planer.config import Settings, get_settings
from planer.web.admin import get_admin_user
from planer.web.app import app
from planer.web.deps import get_db

_SECRET = "test_secret_key_that_is_at_least_32_chars_long"


@pytest.fixture
def test_engine(tmp_path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    create_tables(engine)
    return engine


@pytest.fixture
def client(test_engine) -> Generator[TestClient, None, None]:  # type: ignore[no-untyped-def]
    """Admin client WITHOUT a CSRF override — the real require_csrf runs."""

    def override_db() -> Generator[Session, None, None]:
        with Session(test_engine) as sess:
            yield sess

    def override_settings() -> Settings:
        return Settings(
            secret_key=_SECRET,
            database_url=f"sqlite:///{test_engine.url.database}",
            cookie_secure=False,  # TestClient speaks HTTP
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_admin_user] = lambda: "testadmin"

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


def _extract_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf token in rendered form"
    return match.group(1)


class TestCsrf:
    def test_post_without_token_is_rejected(self, client: TestClient) -> None:
        # No prior GET → no cookie, no form token.
        resp = client.post("/admin/rounds", data={"label": "X"}, follow_redirects=False)
        assert resp.status_code == 403

    def test_get_sets_cookie(self, client: TestClient) -> None:
        resp = client.get("/admin/", follow_redirects=True)
        assert resp.status_code == 200
        assert "csrf_token" in resp.cookies

    def test_post_with_matching_token_succeeds(self, client: TestClient) -> None:
        page = client.get("/admin/", follow_redirects=True)
        token = _extract_token(page.text)
        # TestClient persists the cookie set on the GET.
        resp = client.post(
            "/admin/rounds",
            data={"label": "KW12", "csrf_token": token},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_post_with_wrong_token_is_rejected(self, client: TestClient) -> None:
        client.get("/admin/", follow_redirects=True)  # sets cookie
        resp = client.post(
            "/admin/rounds",
            data={"label": "X", "csrf_token": "not-the-cookie-value"},
            follow_redirects=False,
        )
        assert resp.status_code == 403
