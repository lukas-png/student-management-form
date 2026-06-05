"""Web tests for the public availability flow.

Tests cover:
- Valid / expired / manipulated tokens
- GET renders the form with existing pre-selection
- POST persists availability; re-submit overwrites
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from planer.adapters.db import (
    create_round,
    create_slot,
    create_tables,
    get_availabilities_for_round,
    make_engine,
    upsert_students,
)
from planer.config import Settings, get_settings
from planer.domain.models import Participant
from planer.security import sign_availability_token
from planer.web.app import app
from planer.web.deps import get_db

_SECRET = "test_secret_key_that_is_at_least_32_chars_long"
_T0 = datetime(2024, 3, 18, 10, 0)
_T1 = datetime(2024, 3, 20, 14, 0)


def _participant(uid: str = "u1") -> Participant:
    return Participant(
        user_id=uid,
        display_name="Alice",
        email="alice@uni.de",
        matr_nr="111111",
        group_id="g1",
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


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------


class TestTokenValidation:
    def test_valid_token_returns_200(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.get(f"/availability/{token}")
        assert resp.status_code == 200

    def test_manipulated_token_returns_400(self, client: TestClient) -> None:
        resp = client.get("/availability/this.is.not.a.valid.token")
        assert resp.status_code == 400

    def test_tampered_token_returns_400(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        tampered = token[:-3] + "xxx"
        resp = client.get(f"/availability/{tampered}")
        assert resp.status_code == 400

    def test_wrong_secret_returns_400(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        token = sign_availability_token(
            "u1", round_id, secret_key="wrong_secret_00000000000000000000"
        )
        resp = client.get(f"/availability/{token}")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET: form rendering
# ---------------------------------------------------------------------------


class TestGetForm:
    def test_form_shows_slot(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            create_slot(sess, rnd.id, _T0, room="A101")
            round_id = rnd.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.get(f"/availability/{token}")
        assert resp.status_code == 200
        assert "A101" in resp.text

    def test_form_shows_no_slots_message_when_round_empty(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.get(f"/availability/{token}")
        assert resp.status_code == 200
        assert "noch keine Termine" in resp.text

    def test_existing_availability_preselected(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        from planer.adapters.db import upsert_availability

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            round_id = rnd.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.get(f"/availability/{token}")
        assert resp.status_code == 200
        assert "checked" in resp.text

    def test_multiple_slots_shown(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            create_slot(sess, rnd.id, _T0, room="R1")
            create_slot(sess, rnd.id, _T1, room="R2")
            round_id = rnd.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.get(f"/availability/{token}")
        assert resp.status_code == 200
        assert "R1" in resp.text
        assert "R2" in resp.text


# ---------------------------------------------------------------------------
# POST: submission
# ---------------------------------------------------------------------------


class TestPostSubmit:
    def test_submit_saves_availability(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            round_id, slot_id = rnd.id, slot.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.post(f"/availability/{token}", data={f"slot_{slot_id}": "on"})
        assert resp.status_code == 200

        with Session(test_engine) as sess:
            avails = get_availabilities_for_round(sess, round_id)
        assert len(avails) == 1
        assert avails[0].available is True

    def test_submit_unchecked_saves_false(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            round_id = rnd.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.post(f"/availability/{token}", data={})
        assert resp.status_code == 200

        with Session(test_engine) as sess:
            avails = get_availabilities_for_round(sess, round_id)
        assert len(avails) == 1
        assert avails[0].available is False

    def test_resubmit_overwrites(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            round_id, slot_id = rnd.id, slot.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        client.post(f"/availability/{token}", data={f"slot_{slot_id}": "on"})
        client.post(f"/availability/{token}", data={})

        with Session(test_engine) as sess:
            avails = get_availabilities_for_round(sess, round_id)
        assert len(avails) == 1
        assert avails[0].available is False

    def test_post_invalid_token_returns_400(self, client: TestClient) -> None:
        resp = client.post("/availability/bad.token.here", data={})
        assert resp.status_code == 400

    def test_submit_two_slots_partial_selection(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            rnd = create_round(sess)
            assert rnd.id is not None
            s1 = create_slot(sess, rnd.id, _T0)
            s2 = create_slot(sess, rnd.id, _T1)
            assert s1.id is not None and s2.id is not None
            round_id, sid1, sid2 = rnd.id, s1.id, s2.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        client.post(f"/availability/{token}", data={f"slot_{sid1}": "on"})

        with Session(test_engine) as sess:
            avails = {a.slot_id: a.available for a in get_availabilities_for_round(sess, round_id)}
        assert avails[sid1] is True
        assert avails[sid2] is False


# ---------------------------------------------------------------------------
# Confirm mode (fixed slot, yes/no)
# ---------------------------------------------------------------------------


class TestConfirmMode:
    def _setup(self, test_engine):  # type: ignore[no-untyped-def]
        from planer.adapters.db import set_round_mode, upsert_curation, upsert_groups
        from planer.domain.models import Group as DomainGroup

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1")])  # group_id g1
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0, room="A101")
            assert slot.id is not None
            upsert_curation(sess, "g1", rnd.id, included=True, pinned_slot_id=slot.id)
            set_round_mode(sess, rnd.id, "confirm")
            return rnd.id, slot.id

    def test_form_shows_only_pinned_slot_with_yes_no(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, _ = self._setup(test_engine)
        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.get(f"/availability/{token}")
        assert resp.status_code == 200
        assert "A101" in resp.text
        assert "Ja, ich komme" in resp.text
        assert "Nein, ich kann nicht" in resp.text

    def test_confirm_yes_stores_available_true(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, slot_id = self._setup(test_engine)
        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.post(f"/availability/{token}", data={"confirm": "yes"})
        assert resp.status_code == 200
        with Session(test_engine) as sess:
            avails = {a.slot_id: a.available for a in get_availabilities_for_round(sess, round_id)}
        assert avails[slot_id] is True

    def test_confirm_no_stores_available_false(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, slot_id = self._setup(test_engine)
        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        client.post(f"/availability/{token}", data={"confirm": "no"})
        with Session(test_engine) as sess:
            avails = {a.slot_id: a.available for a in get_availabilities_for_round(sess, round_id)}
        assert avails[slot_id] is False

    def test_no_pinned_slot_shows_message(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        from planer.adapters.db import set_round_mode, upsert_groups
        from planer.domain.models import Group as DomainGroup

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            create_slot(sess, rnd.id, _T0)
            set_round_mode(sess, rnd.id, "confirm")
            round_id = rnd.id

        token = sign_availability_token("u1", round_id, secret_key=_SECRET)
        resp = client.get(f"/availability/{token}")
        assert resp.status_code == 200
        assert "noch kein Termin festgelegt" in resp.text
