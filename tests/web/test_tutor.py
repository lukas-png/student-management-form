"""Web tests for the tutor attendance-marking flow."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from planer.adapters.db import (
    PresentationStatus,
    create_presentation,
    create_round,
    create_slot,
    create_tables,
    get_presentations_for_round,
    make_engine,
    upsert_groups,
    upsert_students,
)
from planer.config import Settings, get_settings
from planer.domain.models import Group as DomainGroup
from planer.domain.models import Participant
from planer.security import sign_tutor_token
from planer.web.app import app
from planer.web.deps import get_db

_SECRET = "test_secret_key_that_is_at_least_32_chars_long"
_T0 = datetime(2024, 3, 18, 10, 0)


def _participant(uid: str, group_id: str = "g1") -> Participant:
    return Participant(
        user_id=uid,
        display_name=f"Student {uid}",
        email=f"{uid}@uni.de",
        matr_nr="111111",
        group_id=group_id,
        group_name="G1",
        has_admission=True,
        results=(),
    )


@pytest.fixture
def test_engine(tmp_path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    create_tables(engine)
    return engine


@pytest.fixture
def client(test_engine) -> Generator[TestClient, None, None]:  # type: ignore[no-untyped-def]
    def override_db() -> Generator[Session, None, None]:
        with Session(test_engine) as sess:
            yield sess

    def override_settings() -> Settings:
        return Settings(
            secret_key=_SECRET,
            database_url=f"sqlite:///{test_engine.url.database}",
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


def _seed(test_engine) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    with Session(test_engine) as sess:
        upsert_students(sess, [_participant("u1"), _participant("u2")])
        upsert_groups(sess, [DomainGroup(id="g1", name="Gruppe Eins", members=())])
        rnd = create_round(sess)
        assert rnd.id is not None
        slot = create_slot(sess, rnd.id, _T0, room="A101")
        assert slot.id is not None
        create_presentation(sess, "g1", slot.id, rnd.id)
        return rnd.id, slot.id


class TestTutorToken:
    def test_bad_token_returns_400(self, client: TestClient) -> None:
        assert client.get("/tutor/not.a.token").status_code == 400

    def test_wrong_secret_returns_400(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, slot_id = _seed(test_engine)
        token = sign_tutor_token(slot_id, round_id, secret_key="another_secret_000000000000000000")
        assert client.get(f"/tutor/{token}").status_code == 400


class TestTutorForm:
    def test_form_shows_group_and_members(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, slot_id = _seed(test_engine)
        token = sign_tutor_token(slot_id, round_id, secret_key=_SECRET)
        resp = client.get(f"/tutor/{token}")
        assert resp.status_code == 200
        assert "Gruppe Eins" in resp.text
        assert "Student u1" in resp.text
        assert "A101" in resp.text

    def test_empty_slot_message(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            round_id, slot_id = rnd.id, slot.id
        token = sign_tutor_token(slot_id, round_id, secret_key=_SECRET)
        resp = client.get(f"/tutor/{token}")
        assert resp.status_code == 200
        assert "keine Gruppen" in resp.text


class TestTutorSubmit:
    def test_mark_presented(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, slot_id = _seed(test_engine)
        token = sign_tutor_token(slot_id, round_id, secret_key=_SECRET)

        resp = client.post(f"/tutor/{token}", data={"status_g1": "PRESENTED"})
        assert resp.status_code == 200

        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert presos[0].status == PresentationStatus.PRESENTED
        assert presos[0].marked_at is not None

    def test_mark_no_show(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, slot_id = _seed(test_engine)
        token = sign_tutor_token(slot_id, round_id, secret_key=_SECRET)

        client.post(f"/tutor/{token}", data={"status_g1": "NO_SHOW"})

        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert presos[0].status == PresentationStatus.NO_SHOW

    def test_invalid_status_ignored(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, slot_id = _seed(test_engine)
        token = sign_tutor_token(slot_id, round_id, secret_key=_SECRET)

        client.post(f"/tutor/{token}", data={"status_g1": "GARBAGE"})

        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert presos[0].status == PresentationStatus.SCHEDULED

    def test_post_bad_token_400(self, client: TestClient) -> None:
        assert client.post("/tutor/bad.token", data={}).status_code == 400
