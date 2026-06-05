"""Access-log middleware test: tokens must be redacted from logs."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from planer.logging_setup import LOGGER_NAME
from planer.web.app import app, docs_enabled


def test_docs_enabled_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    assert docs_enabled() is True


def test_docs_disabled_via_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for val in ("false", "False", "0", "no"):
        monkeypatch.setenv("ENABLE_DOCS", val)
        assert docs_enabled() is False


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_access_log_redacts_magic_link_token() -> None:
    # Enter the context first so lifespan startup (which resets handlers) runs,
    # then attach our capturing handler to the planer logger.
    with TestClient(app) as client:
        cap = _Capture()
        logging.getLogger(LOGGER_NAME).addHandler(cap)
        try:
            secret = "this-token-must-never-appear-in-logs"
            resp = client.get(f"/availability/{secret}")
            assert resp.status_code == 400  # bad token, but still logged
        finally:
            logging.getLogger(LOGGER_NAME).removeHandler(cap)

    access = [r for r in cap.records if r.name == "planer.access" and r.getMessage() == "request"]
    assert access, "no access-log record captured"
    rec = access[-1]
    assert rec.path == "/availability/<token>"  # type: ignore[attr-defined]
    # the raw token must not leak through any field of any captured record
    assert all(secret not in str(r.__dict__) for r in cap.records)


def test_access_log_records_health() -> None:
    with TestClient(app) as client:
        cap = _Capture()
        logging.getLogger(LOGGER_NAME).addHandler(cap)
        try:
            assert client.get("/health").status_code == 200
        finally:
            logging.getLogger(LOGGER_NAME).removeHandler(cap)

    rec = [r for r in cap.records if r.name == "planer.access" and r.getMessage() == "request"][-1]
    assert rec.path == "/health"  # type: ignore[attr-defined]
    assert rec.status == 200  # type: ignore[attr-defined]
