"""Write-back of presentation results to stu-mgmt (per-user assessments)."""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime

import httpx
import pytest
import respx
from sqlmodel import Session

from planer.adapters.db import (
    PresentationStatus,
    create_presentation,
    create_round,
    create_slot,
    create_tables,
    make_engine,
    update_presentation_status,
    upsert_groups,
    upsert_students,
)
from planer.adapters.submit_results import submit_presentations
from planer.config import Settings
from planer.domain.filtering import build_groups
from planer.domain.models import Participant

_SPARKY_URL = "http://sparky.test/api/v1/authenticate"
_API_URL = "http://api.test"
_COURSE = "CS101"
_ASSIGNMENTS = f"{_API_URL}/courses/{_COURSE}/assignments"
_ASSESSMENTS = f"{_API_URL}/courses/{_COURSE}/assignments/a1/assessments"
_BULK = f"{_ASSESSMENTS}/bulk"


def _jwt() -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode())
    return f"header.{payload.rstrip(b'=').decode()}.sig"


def _settings(**over: str) -> Settings:
    base = dict(
        secret_key="test-secret-key-at-least-32-characters-long",
        stumgmt_api_url=_API_URL,
        sparky_auth_url=_SPARKY_URL,
        sparky_user="bot",
        sparky_password="pw",
        stumgmt_course_id=_COURSE,
        stumgmt_presentation_assignment_id="a1",
    )
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def _participant(uid: str, group_id: str = "", group_name: str = "") -> Participant:
    return Participant(
        user_id=uid,
        display_name=f"Student {uid}",
        email=f"{uid}@uni.de",
        matr_nr="111111",
        group_id=group_id,
        group_name=group_name,
        has_admission=True,
        results=(),
    )


@pytest.fixture
def session():  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite:///:memory:")
    create_tables(engine)
    with Session(engine) as sess:
        yield sess


@pytest.fixture
def round_with_presentations(session: Session) -> int:
    """g1 (u1,u2) PRESENTED, g2 (u3) NO_SHOW, solo u4 PRESENTED."""
    participants = [
        _participant("u1", "g1", "Gruppe 1"),
        _participant("u2", "g1", "Gruppe 1"),
        _participant("u3", "g2", "Gruppe 2"),
        _participant("u4"),  # solo → group id == user id
    ]
    upsert_students(session, participants)
    upsert_groups(session, build_groups(participants))
    rnd = create_round(session)
    assert rnd.id is not None
    slot = create_slot(session, rnd.id, datetime(2024, 3, 18, 10, 0))
    assert slot.id is not None
    for gid in ("g1", "g2", "u4"):
        create_presentation(session, gid, slot.id, rnd.id)
    update_presentation_status(session, "g1", slot.id, PresentationStatus.PRESENTED)
    update_presentation_status(session, "g2", slot.id, PresentationStatus.NO_SHOW)
    update_presentation_status(session, "u4", slot.id, PresentationStatus.PRESENTED)
    return rnd.id


def _mock(existing: list[dict] | None = None, assignments: list[dict] | None = None):  # type: ignore[no-untyped-def]
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _jwt()}))
    respx.get(_ASSIGNMENTS).mock(
        return_value=httpx.Response(
            200,
            json=assignments
            if assignments is not None
            else [{"id": "a1", "name": "V", "points": 1}],
        )
    )
    respx.get(_ASSESSMENTS).mock(return_value=httpx.Response(200, json=existing or []))
    bulk = respx.post(_BULK).mock(return_value=httpx.Response(201, json=[]))
    return bulk


@respx.mock
def test_creates_one_assessment_per_presented_member(
    session: Session, round_with_presentations: int
) -> None:
    bulk = _mock()
    report = submit_presentations(session, _settings(), round_with_presentations)

    assert sorted(report.created) == ["u1", "u2", "u4"]
    assert report.skipped == [] and report.updated == [] and report.failed == []
    assert bulk.called
    sent = json.loads(bulk.calls.last.request.content)
    assert {d["userId"] for d in sent} == {"u1", "u2", "u4"}
    assert all(d["achievedPoints"] == 1 and d["isDraft"] is True for d in sent)
    assert all(d["assignmentId"] == "a1" for d in sent)


@respx.mock
def test_no_show_member_is_not_written(session: Session, round_with_presentations: int) -> None:
    bulk = _mock()
    report = submit_presentations(session, _settings(), round_with_presentations)
    sent = json.loads(bulk.calls.last.request.content)
    assert "u3" not in {d["userId"] for d in sent}
    assert "u3" not in report.created


@respx.mock
def test_bulk_empty_body_is_success_not_failure(
    session: Session, round_with_presentations: int
) -> None:
    """Live stu-mgmt returns 201 with an empty body; json() must not blow up."""
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _jwt()}))
    respx.get(_ASSIGNMENTS).mock(
        return_value=httpx.Response(200, json=[{"id": "a1", "name": "V", "points": 1}])
    )
    respx.get(_ASSESSMENTS).mock(return_value=httpx.Response(200, json=[]))
    respx.post(_BULK).mock(return_value=httpx.Response(201))  # no body

    report = submit_presentations(session, _settings(), round_with_presentations)

    assert sorted(report.created) == ["u1", "u2", "u4"]
    assert report.failed == []


@respx.mock
def test_force_patch_empty_body_is_success(session: Session, round_with_presentations: int) -> None:
    """A 204 No Content on PATCH must count as updated, not failed."""
    _mock(existing=[{"id": "as1", "userId": "u1"}])
    respx.patch(f"{_ASSESSMENTS}/as1").mock(return_value=httpx.Response(204))
    report = submit_presentations(session, _settings(), round_with_presentations, force=True)

    assert report.updated == ["u1"]
    assert report.failed == []


@respx.mock
def test_idempotency_skips_already_assessed(
    session: Session, round_with_presentations: int
) -> None:
    bulk = _mock(existing=[{"id": "as1", "userId": "u1"}])
    report = submit_presentations(session, _settings(), round_with_presentations)

    assert report.skipped == ["u1"]
    assert sorted(report.created) == ["u2", "u4"]
    assert {d["userId"] for d in json.loads(bulk.calls.last.request.content)} == {"u2", "u4"}


@respx.mock
def test_force_patches_existing(session: Session, round_with_presentations: int) -> None:
    _mock(existing=[{"id": "as1", "userId": "u1"}])
    patch = respx.patch(f"{_ASSESSMENTS}/as1").mock(return_value=httpx.Response(200, json={}))
    report = submit_presentations(session, _settings(), round_with_presentations, force=True)

    assert report.updated == ["u1"]
    assert patch.called
    body = json.loads(patch.calls.last.request.content)
    assert body["achievedPoints"] == 1 and body["isDraft"] is True


@respx.mock
def test_dry_run_makes_no_writes(session: Session, round_with_presentations: int) -> None:
    bulk = _mock(existing=[{"id": "as1", "userId": "u1"}])
    report = submit_presentations(session, _settings(), round_with_presentations, dry_run=True)

    assert report.dry_run is True
    assert report.skipped == ["u1"]
    assert sorted(report.created) == ["u2", "u4"]
    assert not bulk.called  # no POST in a dry run


@respx.mock
def test_resolves_assignment_by_name(session: Session, round_with_presentations: int) -> None:
    _mock(assignments=[{"id": "a1", "name": "Vorstellung", "points": 1}])
    settings = _settings(
        stumgmt_presentation_assignment_id="",
        stumgmt_presentation_assignment_name="Vorstellung",
    )
    report = submit_presentations(session, settings, round_with_presentations)
    assert report.assignment_id == "a1"
    assert sorted(report.created) == ["u1", "u2", "u4"]


@respx.mock
def test_name_no_match_raises(session: Session, round_with_presentations: int) -> None:
    _mock(assignments=[{"id": "a1", "name": "Other", "points": 1}])
    settings = _settings(
        stumgmt_presentation_assignment_id="", stumgmt_presentation_assignment_name="Missing"
    )
    with pytest.raises(ValueError, match="matched 0"):
        submit_presentations(session, settings, round_with_presentations)


@respx.mock
def test_name_ambiguous_raises(session: Session, round_with_presentations: int) -> None:
    _mock(
        assignments=[
            {"id": "a1", "name": "Dup", "points": 1},
            {"id": "a2", "name": "Dup", "points": 1},
        ]
    )
    settings = _settings(
        stumgmt_presentation_assignment_id="", stumgmt_presentation_assignment_name="Dup"
    )
    with pytest.raises(ValueError, match="matched 2"):
        submit_presentations(session, settings, round_with_presentations)


@respx.mock
def test_nothing_configured_raises(session: Session, round_with_presentations: int) -> None:
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _jwt()}))
    respx.get(_ASSIGNMENTS).mock(return_value=httpx.Response(200, json=[]))
    settings = _settings(
        stumgmt_presentation_assignment_id="", stumgmt_presentation_assignment_name=""
    )
    with pytest.raises(ValueError, match="No presentation assignment configured"):
        submit_presentations(session, settings, round_with_presentations)


@respx.mock
def test_403_surfaces_as_permission_error(session: Session, round_with_presentations: int) -> None:
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _jwt()}))
    respx.get(_ASSIGNMENTS).mock(return_value=httpx.Response(403, json={}))
    with pytest.raises(PermissionError):
        submit_presentations(session, _settings(), round_with_presentations)


@respx.mock
def test_no_secrets_in_logs(
    session: Session, round_with_presentations: int, caplog: pytest.LogCaptureFixture
) -> None:
    _mock()
    with caplog.at_level(logging.DEBUG):
        submit_presentations(session, _settings(), round_with_presentations)
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert "pw" not in blob and "header." not in blob
