"""Web tests for the admin dashboard flow."""

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
    get_curations_for_round,
    get_presentations_for_round,
    make_engine,
    upsert_groups,
    upsert_students,
)
from planer.config import Settings, get_settings
from planer.domain.models import Group as DomainGroup
from planer.domain.models import Participant
from planer.web.admin import get_admin_user, require_csrf
from planer.web.app import app
from planer.web.deps import get_db

_SECRET = "test_secret_key_that_is_at_least_32_chars_long"
_T0 = datetime(2024, 3, 18, 10, 0)
_T1 = datetime(2024, 3, 20, 14, 0)


def _participant(uid: str = "u1", group_id: str = "g1") -> Participant:
    return Participant(
        user_id=uid,
        display_name="Alice",
        email="alice@uni.de",
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
            admin_auth_mode="forward_auth",
            admin_forward_auth_header="X-Admin-User",
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_admin_user] = lambda: "testadmin"
    app.dependency_overrides[require_csrf] = lambda: None

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def client_no_auth(test_engine) -> Generator[TestClient, None, None]:  # type: ignore[no-untyped-def]
    def override_db() -> Generator[Session, None, None]:
        with Session(test_engine) as sess:
            yield sess

    def override_settings() -> Settings:
        return Settings(
            secret_key=_SECRET,
            database_url=f"sqlite:///{test_engine.url.database}",
            admin_auth_mode="forward_auth",
            admin_forward_auth_header="X-Admin-User",
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings

    with TestClient(app, raise_server_exceptions=True, follow_redirects=False) as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAdminAuth:
    def test_default_mode_is_open(self, client_no_auth: TestClient) -> None:
        # Default (forward_auth) mode performs no app-level check; the reverse
        # proxy / network is the boundary, so no header is required.
        resp = client_no_auth.get("/admin/", follow_redirects=True)
        assert resp.status_code == 200

    def test_valid_header_passes(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            create_round(sess)
        resp = client.get("/admin/", follow_redirects=True)
        assert resp.status_code == 200


class TestPasswordMode:
    """The opt-in `password` mode still enforces Argon2 HTTP Basic credentials."""

    def _client(self, test_engine) -> TestClient:  # type: ignore[no-untyped-def]
        from argon2 import PasswordHasher

        pw_hash = PasswordHasher().hash("s3cret")

        def override_db() -> Generator[Session, None, None]:
            with Session(test_engine) as sess:
                yield sess

        def override_settings() -> Settings:
            return Settings(
                secret_key=_SECRET,
                database_url=f"sqlite:///{test_engine.url.database}",
                admin_auth_mode="password",
                admin_password_hash=pw_hash,
            )

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = override_settings
        return TestClient(app, raise_server_exceptions=True, follow_redirects=False)

    def test_no_credentials_rejected(self, test_engine) -> None:  # type: ignore[no-untyped-def]
        client = self._client(test_engine)
        try:
            assert client.get("/admin/").status_code == 401
        finally:
            app.dependency_overrides.clear()

    def test_valid_credentials_accepted(self, test_engine) -> None:  # type: ignore[no-untyped-def]
        client = self._client(test_engine)
        try:
            resp = client.get("/admin/", auth=("admin", "s3cret"), follow_redirects=True)
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_wrong_password_rejected(self, test_engine) -> None:  # type: ignore[no-untyped-def]
        client = self._client(test_engine)
        try:
            assert client.get("/admin/", auth=("admin", "wrong")).status_code == 401
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Round management
# ---------------------------------------------------------------------------


class TestRoundManagement:
    def test_no_rounds_shows_create_form(self, client: TestClient) -> None:
        resp = client.get("/admin/", follow_redirects=True)
        assert resp.status_code == 200
        assert "Runde erstellen" in resp.text

    def test_create_round_redirects_to_dashboard(self, client: TestClient) -> None:
        resp = client.post("/admin/rounds", data={"label": "KW12"}, follow_redirects=True)
        assert resp.status_code == 200
        assert "KW12" in resp.text

    def test_dashboard_lists_rounds_in_nav(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            create_round(sess, label="A")
            create_round(sess, label="B")
        resp = client.get("/admin/", follow_redirects=True)
        assert "A" in resp.text
        assert "B" in resp.text


# ---------------------------------------------------------------------------
# Slot management
# ---------------------------------------------------------------------------


class TestSlotManagement:
    def test_add_slot_appears_in_dashboard(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            rnd = create_round(sess)
            round_id = rnd.id

        resp = client.post(
            f"/admin/rounds/{round_id}/slots",
            data={"starts_at": "2024-03-18T10:00", "room": "R101", "capacity": "3"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "R101" in resp.text

    def test_delete_slot_removes_it(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            rnd = create_round(sess)
            slot = create_slot(sess, rnd.id, _T0, room="TOREMOVE")  # type: ignore[arg-type]
            round_id, slot_id = rnd.id, slot.id

        resp = client.post(
            f"/admin/rounds/{round_id}/slots/{slot_id}/delete",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "TOREMOVE" not in resp.text


# ---------------------------------------------------------------------------
# Curation
# ---------------------------------------------------------------------------


class TestCuration:
    def test_exclude_group(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            round_id = rnd.id

        client.post(
            f"/admin/rounds/{round_id}/groups/g1/curate",
            data={"included": "false", "pinned_slot_id": ""},
        )

        with Session(test_engine) as sess:
            curs = get_curations_for_round(sess, round_id)
        assert len(curs) == 1
        assert curs[0].included is False

    def test_pin_group_to_slot(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant()])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            slot = create_slot(sess, rnd.id, _T0)  # type: ignore[arg-type]
            round_id, slot_id = rnd.id, slot.id

        client.post(
            f"/admin/rounds/{round_id}/groups/g1/curate",
            data={"included": "true", "pinned_slot_id": str(slot_id)},
        )

        with Session(test_engine) as sess:
            curs = get_curations_for_round(sess, round_id)
        assert curs[0].pinned_slot_id == slot_id


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------


class TestSolver:
    def _setup_round_with_availability(self, sess: Session) -> tuple[int, int, int]:
        upsert_students(sess, [_participant("u1", "g1"), _participant("u2", "g1")])
        upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
        rnd = create_round(sess)
        assert rnd.id is not None
        slot = create_slot(sess, rnd.id, _T0)
        assert slot.id is not None
        return rnd.id, slot.id, slot.id

    def test_solve_creates_presentation(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        from planer.adapters.db import upsert_availability

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            round_id, slot_id = rnd.id, slot.id

        resp = client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)
        assert resp.status_code == 200

        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert len(presos) == 1
        assert presos[0].group_id == "g1"
        assert presos[0].slot_id == slot_id

    def test_solve_excludes_not_due_group(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        # A group whose only member already has an assessment is not due and must
        # not be scheduled even though the member is available.
        from dataclasses import replace

        from planer.adapters.db import upsert_availability

        with Session(test_engine) as sess:
            upsert_students(sess, [replace(_participant("u1", "g1"), has_assessment=True)])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            round_id = rnd.id

        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            assert len(get_presentations_for_round(sess, round_id)) == 0

    def test_mixed_group_schedulable_under_all_quorum(
        self, client: TestClient, test_engine
    ) -> None:  # type: ignore[no-untyped-def]
        # g1 has a due member (u1) and an already-assessed member (u2). Under the
        # default "all" quorum the assessed member would normally block the slot,
        # but not-due members are not summoned and so do not count for the quorum →
        # the group is placed on the slot u1 can make.
        from dataclasses import replace

        from planer.adapters.db import upsert_availability

        with Session(test_engine) as sess:
            upsert_students(
                sess,
                [
                    _participant("u1", "g1"),
                    replace(_participant("u2", "g1"), has_assessment=True),
                ],
            )
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            # u2 (assessed) submits nothing —> must not hold the group back.
            round_id, slot_id = rnd.id, slot.id

        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert len(presos) == 1
        assert presos[0].group_id == "g1"
        assert presos[0].slot_id == slot_id

    def test_solve_excludes_unavailable_group(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            create_slot(sess, rnd.id, _T0)
            round_id = rnd.id

        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)

        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert len(presos) == 0

    def test_any_quorum_places_group_with_one_available_member(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        # Two members, only u1 available. Under the default "all" quorum the group
        # is unplaceable; switching the round to "any" places it on the slot.
        from planer.adapters.db import set_round_quorum, upsert_availability

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1"), _participant("u2", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            upsert_availability(sess, "u2", slot.id, available=False)
            round_id, slot_id = rnd.id, slot.id

        # Default "all" quorum: u2 declined → no feasible slot → unplaced.
        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            assert len(get_presentations_for_round(sess, round_id)) == 0

        # Switch to "any": one available member is enough → group is placed.
        with Session(test_engine) as sess:
            set_round_quorum(sess, round_id, "any")
        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert len(presos) == 1
        assert presos[0].group_id == "g1"
        assert presos[0].slot_id == slot_id

    def test_rerun_solver_overwrites(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        from planer.adapters.db import upsert_availability

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            round_id = rnd.id

        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)
        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)

        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert len(presos) == 1

    def test_solver_respects_excluded_group(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        from planer.adapters.db import upsert_availability

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            round_id = rnd.id

        client.post(
            f"/admin/rounds/{round_id}/groups/g1/curate",
            data={"included": "false", "pinned_slot_id": ""},
        )
        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)

        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert len(presos) == 0


# ---------------------------------------------------------------------------
# Carry-over
# ---------------------------------------------------------------------------


class TestCarryOver:
    def test_presented_group_excluded_from_next_round(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        from planer.adapters.db import (
            PresentationStatus,
            update_presentation_status,
            upsert_availability,
        )

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            r1 = create_round(sess)
            assert r1.id is not None
            s1 = create_slot(sess, r1.id, _T0)
            assert s1.id is not None
            upsert_availability(sess, "u1", s1.id, available=True)
            round1 = r1.id

        # Round 1: solve, group presents.
        client.post(f"/admin/rounds/{round1}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            pres = get_presentations_for_round(sess, round1)[0]
            update_presentation_status(sess, "g1", pres.slot_id, PresentationStatus.PRESENTED)

        # Round 2: same group available, but it already presented → excluded.
        with Session(test_engine) as sess:
            r2 = create_round(sess)
            assert r2.id is not None
            s2 = create_slot(sess, r2.id, _T1)
            assert s2.id is not None
            upsert_availability(sess, "u1", s2.id, available=True)
            round2 = r2.id

        client.post(f"/admin/rounds/{round2}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            presos2 = get_presentations_for_round(sess, round2)
        assert len(presos2) == 0

    def test_no_show_group_returns_in_next_round(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        from planer.adapters.db import (
            PresentationStatus,
            update_presentation_status,
            upsert_availability,
        )

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            r1 = create_round(sess)
            assert r1.id is not None
            s1 = create_slot(sess, r1.id, _T0)
            assert s1.id is not None
            upsert_availability(sess, "u1", s1.id, available=True)
            round1 = r1.id

        client.post(f"/admin/rounds/{round1}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            pres = get_presentations_for_round(sess, round1)[0]
            update_presentation_status(sess, "g1", pres.slot_id, PresentationStatus.NO_SHOW)

        with Session(test_engine) as sess:
            r2 = create_round(sess)
            assert r2.id is not None
            s2 = create_slot(sess, r2.id, _T1)
            assert s2.id is not None
            upsert_availability(sess, "u1", s2.id, available=True)
            round2 = r2.id

        client.post(f"/admin/rounds/{round2}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            presos2 = get_presentations_for_round(sess, round2)
        assert len(presos2) == 1  # no-show carried over into the next round


# ---------------------------------------------------------------------------
# Bulk curation + round mode
# ---------------------------------------------------------------------------


class TestBulkCurationAndMode:
    def test_curate_all_excludes_then_solve_is_empty(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        from planer.adapters.db import upsert_availability

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            round_id = rnd.id

        client.post(f"/admin/rounds/{round_id}/curate-all", data={"included": "false"})
        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)

        with Session(test_engine) as sess:
            assert len(get_presentations_for_round(sess, round_id)) == 0
            curs = get_curations_for_round(sess, round_id)
        assert curs and all(c.included is False for c in curs)

    def test_curate_all_include(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        client.post(f"/admin/rounds/{round_id}/curate-all", data={"included": "true"})
        with Session(test_engine) as sess:
            curs = get_curations_for_round(sess, round_id)
        assert curs and all(c.included is True for c in curs)

    def test_curate_selected_includes_subset(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1"), _participant("u2", "g2")])
            upsert_groups(
                sess,
                [
                    DomainGroup(id="g1", name="G1", members=()),
                    DomainGroup(id="g2", name="G2", members=()),
                ],
            )
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        client.post(
            f"/admin/rounds/{round_id}/curate-selected",
            data={"included": "true", "group_ids": ["g1"]},
        )
        with Session(test_engine) as sess:
            curs = {c.group_id: c.included for c in get_curations_for_round(sess, round_id)}
        assert curs.get("g1") is True
        assert curs.get("g2") is not True

    def test_curate_selected_empty_is_noop(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        resp = client.post(
            f"/admin/rounds/{round_id}/curate-selected",
            data={"included": "false"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        with Session(test_engine) as sess:
            curs = get_curations_for_round(sess, round_id)
        assert curs == []

    def test_set_mode_confirm(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        from planer.adapters.db import get_round

        with Session(test_engine) as sess:
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        client.post(f"/admin/rounds/{round_id}/mode", data={"mode": "confirm"})
        with Session(test_engine) as sess:
            assert get_round(sess, round_id).mode == "confirm"

    def test_set_mode_invalid_rejected(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        with Session(test_engine) as sess:
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        resp = client.post(
            f"/admin/rounds/{round_id}/mode", data={"mode": "nonsense"}, follow_redirects=False
        )
        assert resp.status_code == 400

    def test_set_quorum_any(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        from planer.adapters.db import get_round

        with Session(test_engine) as sess:
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        client.post(f"/admin/rounds/{round_id}/quorum", data={"quorum": "any"})
        with Session(test_engine) as sess:
            assert get_round(sess, round_id).quorum == "any"

    def test_set_quorum_invalid_rejected(
        self,
        client: TestClient,
        test_engine,  # type: ignore[no-untyped-def]
    ) -> None:
        with Session(test_engine) as sess:
            rnd = create_round(sess)
            assert rnd.id is not None
            round_id = rnd.id

        resp = client.post(
            f"/admin/rounds/{round_id}/quorum", data={"quorum": "nonsense"}, follow_redirects=False
        )
        assert resp.status_code == 400


class TestPinnedDeclineConflict:
    def _setup_declined_pin(self, test_engine):  # type: ignore[no-untyped-def]
        from planer.adapters.db import upsert_availability, upsert_curation

        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1"), _participant("u2", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            # u1 can come, u2 declined the pinned slot
            upsert_availability(sess, "u1", slot.id, available=True)
            upsert_availability(sess, "u2", slot.id, available=False)
            upsert_curation(sess, "g1", rnd.id, included=True, pinned_slot_id=slot.id)
            return rnd.id, slot.id

    def test_decline_blocks_scheduling(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, _ = self._setup_declined_pin(test_engine)
        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            assert len(get_presentations_for_round(sess, round_id)) == 0

    def test_override_schedules_anyway(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, slot_id = self._setup_declined_pin(test_engine)
        # admin sets override = schedule despite the decline
        client.post(
            f"/admin/rounds/{round_id}/groups/g1/curate",
            data={"included": "true", "pinned_slot_id": str(slot_id), "override": "true"},
        )
        client.post(f"/admin/rounds/{round_id}/solve", follow_redirects=True)
        with Session(test_engine) as sess:
            presos = get_presentations_for_round(sess, round_id)
        assert len(presos) == 1
        assert presos[0].slot_id == slot_id

    def test_dashboard_shows_conflict(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        round_id, _ = self._setup_declined_pin(test_engine)
        resp = client.get(f"/admin/rounds/{round_id}", follow_redirects=True)
        assert "Konflikt (Absage)" in resp.text


class TestResponseTally:
    def test_counts_only_summoned_members(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        # g1: u1 (due, responded) + u2 (assessed, not summoned). Only u1 counts,
        # and u1 has responded → 1 von 1.
        from dataclasses import replace

        from planer.adapters.db import upsert_availability

        with Session(test_engine) as sess:
            upsert_students(
                sess,
                [
                    _participant("u1", "g1"),
                    replace(_participant("u2", "g1"), has_assessment=True),
                ],
            )
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            upsert_availability(sess, "u1", slot.id, available=True)
            round_id = rnd.id

        resp = client.get(f"/admin/rounds/{round_id}", follow_redirects=True)
        assert "1 von 1" in resp.text
        assert "Alle zurückgemeldet" in resp.text

    def test_counts_open_responses(self, client: TestClient, test_engine) -> None:  # type: ignore[no-untyped-def]
        with Session(test_engine) as sess:
            upsert_students(sess, [_participant("u1", "g1"), _participant("u2", "g1")])
            upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
            rnd = create_round(sess)
            assert rnd.id is not None
            slot = create_slot(sess, rnd.id, _T0)
            assert slot.id is not None
            from planer.adapters.db import upsert_availability

            upsert_availability(sess, "u1", slot.id, available=True)  # only u1 responded
            round_id = rnd.id

        resp = client.get(f"/admin/rounds/{round_id}", follow_redirects=True)
        assert "1 von 2" in resp.text
        assert "1 offen" in resp.text
