import base64
import json
import logging
import time

import httpx
import pytest
import respx

from planer.adapters.ingest_api import StuMgmtClient, fetch_participants, to_participants
from planer.adapters.sparky import StuMgmtAuth, _exp_from_jwt

_SPARKY_URL = "http://sparky.test/api/v1/authenticate"
_API_URL = "http://api.test"
_COURSE_ID = "CS101"
_ADMISSION_PATH = f"/courses/{_COURSE_ID}/admission-status"
_GROUPS_PATH = f"/courses/{_COURSE_ID}/groups"


def _make_jwt(exp: float) -> str:
    """Build a minimal fake JWT with the given exp claim."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.sig"


_FRESH_JWT = _make_jwt(time.time() + 3600)


def _mock_api(admission: list[dict], groups: list[dict]) -> None:
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _FRESH_JWT}))
    respx.get(f"{_API_URL}{_ADMISSION_PATH}").mock(return_value=httpx.Response(200, json=admission))
    respx.get(f"{_API_URL}{_GROUPS_PATH}").mock(return_value=httpx.Response(200, json=groups))


# --- to_participants (no HTTP) ---


class TestToParticipants:
    def test_correct_participant_count(
        self, sample_admission_status: list[dict], sample_groups: list[dict]
    ) -> None:
        assert len(to_participants(sample_admission_status, sample_groups)) == 3

    def test_participant_fields(
        self, sample_admission_status: list[dict], sample_groups: list[dict]
    ) -> None:
        participants = to_participants(sample_admission_status, sample_groups)
        alice = next(p for p in participants if p.user_id == "u1")
        assert alice.display_name == "Alice Muster"
        assert alice.email == "alice@uni.de"
        assert alice.matr_nr == "111111"
        assert alice.group_id == "g1"
        assert alice.group_name == "Gruppe 1"

    def test_has_admission_true(
        self, sample_admission_status: list[dict], sample_groups: list[dict]
    ) -> None:
        participants = to_participants(sample_admission_status, sample_groups)
        assert next(p for p in participants if p.user_id == "u1").has_admission is True

    def test_has_admission_false(
        self, sample_admission_status: list[dict], sample_groups: list[dict]
    ) -> None:
        participants = to_participants(sample_admission_status, sample_groups)
        assert next(p for p in participants if p.user_id == "u3").has_admission is False

    def test_result_fields(
        self, sample_admission_status: list[dict], sample_groups: list[dict]
    ) -> None:
        participants = to_participants(sample_admission_status, sample_groups)
        r = next(p for p in participants if p.user_id == "u1").results[0]
        assert r.rule == "INDIVIDUAL_PERCENT_WITH_ALLOWED_FAILURES"
        assert r.assignment_type == "HOMEWORK"
        assert r.achieved_points == 2
        assert r.passed is False

    def test_user_without_group_gets_empty_group(self, sample_admission_status: list[dict]) -> None:
        # no groups at all → every user falls back to empty group (solo)
        participants = to_participants(sample_admission_status, [])
        assert all(p.group_id == "" and p.group_name == "" for p in participants)

    def test_empty_results_when_missing(self, sample_groups: list[dict]) -> None:
        admission = [
            {
                "participant": {
                    "userId": "u9",
                    "displayName": "X",
                    "email": "x@x.com",
                    "matrNr": 999,
                },
                "hasAdmission": True,
                "results": [],
            }
        ]
        assert to_participants(admission, sample_groups)[0].results == ()

    def test_matr_nr_int_normalised_to_string(
        self, sample_admission_status: list[dict], sample_groups: list[dict]
    ) -> None:
        # API delivers matrNr as int; domain model stores it as str
        p = to_participants(sample_admission_status, sample_groups)[0]
        assert isinstance(p.matr_nr, str)

    def test_altzulassung_parsed(self, sample_groups: list[dict]) -> None:
        admission = [
            {
                "participant": {"userId": "u1", "displayName": "X", "email": "x@x", "matrNr": 1},
                "hasAdmission": True,
                "hasAdmissionFromPreviousSemester": True,
                "results": [],
            }
        ]
        p = to_participants(admission, sample_groups)[0]
        assert p.has_admission_from_previous_semester is True

    def test_altzulassung_defaults_false_when_missing(
        self, sample_admission_status: list[dict], sample_groups: list[dict]
    ) -> None:
        p = to_participants(sample_admission_status, sample_groups)[0]
        assert p.has_admission_from_previous_semester is False
        assert p.has_assessment is False


# --- StuMgmtAuth ---


class TestStuMgmtAuth:
    @respx.mock
    def test_token_returned(self) -> None:
        respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _FRESH_JWT}))
        auth = StuMgmtAuth(_SPARKY_URL, "user", "pass")
        assert auth.token() == _FRESH_JWT

    @respx.mock
    def test_token_nested_shape(self) -> None:
        # Real Sparkyservice AuthenticationInfoDto nests the JWT under token.token
        respx.post(_SPARKY_URL).mock(
            return_value=httpx.Response(
                200, json={"token": {"token": _FRESH_JWT, "expiration": "x"}}
            )
        )
        auth = StuMgmtAuth(_SPARKY_URL, "user", "pass")
        assert auth.token() == _FRESH_JWT

    @respx.mock
    def test_token_cached_on_second_call(self) -> None:
        route = respx.post(_SPARKY_URL).mock(
            return_value=httpx.Response(200, json={"token": _FRESH_JWT})
        )
        auth = StuMgmtAuth(_SPARKY_URL, "user", "pass")
        auth.token()
        auth.token()
        assert route.call_count == 1

    @respx.mock
    def test_token_refreshed_after_invalidate(self) -> None:
        route = respx.post(_SPARKY_URL).mock(
            return_value=httpx.Response(200, json={"token": _FRESH_JWT})
        )
        auth = StuMgmtAuth(_SPARKY_URL, "user", "pass")
        auth.token()
        auth.invalidate()
        auth.token()
        assert route.call_count == 2

    @respx.mock
    def test_expired_token_triggers_refresh(self) -> None:
        route = respx.post(_SPARKY_URL).mock(
            return_value=httpx.Response(200, json={"token": _FRESH_JWT})
        )
        auth = StuMgmtAuth(_SPARKY_URL, "user", "pass")
        auth.token()
        auth._expires_at = 0.0  # simulate expiry
        auth.token()
        assert route.call_count == 2

    @respx.mock
    def test_auth_failure_raises(self) -> None:
        respx.post(_SPARKY_URL).mock(return_value=httpx.Response(401))
        auth = StuMgmtAuth(_SPARKY_URL, "bad", "creds")
        with pytest.raises(httpx.HTTPStatusError):
            auth.token()

    def test_exp_from_jwt(self) -> None:
        exp = time.time() + 3600
        assert _exp_from_jwt(_make_jwt(exp)) == pytest.approx(exp)


# --- StuMgmtClient ---


class TestStuMgmtClient:
    @respx.mock
    def test_401_triggers_reauth_and_retry(self) -> None:
        sparky_route = respx.post(_SPARKY_URL).mock(
            return_value=httpx.Response(200, json={"token": _FRESH_JWT})
        )
        api_route = respx.get(f"{_API_URL}{_ADMISSION_PATH}")
        api_route.side_effect = [
            httpx.Response(401),
            httpx.Response(200, json=[]),
        ]
        auth = StuMgmtAuth(_SPARKY_URL, "u", "p")
        client = StuMgmtClient(_API_URL, auth)
        result = client.get(_ADMISSION_PATH)
        assert result == []
        assert sparky_route.call_count == 2  # initial auth + re-auth after 401

    @respx.mock
    def test_401_twice_raises(self) -> None:
        respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _FRESH_JWT}))
        respx.get(f"{_API_URL}{_ADMISSION_PATH}").mock(return_value=httpx.Response(401))
        auth = StuMgmtAuth(_SPARKY_URL, "u", "p")
        client = StuMgmtClient(_API_URL, auth)
        with pytest.raises(httpx.HTTPStatusError):
            client.get(_ADMISSION_PATH)

    @respx.mock
    def test_403_raises_permission_error(self) -> None:
        respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _FRESH_JWT}))
        respx.get(f"{_API_URL}{_ADMISSION_PATH}").mock(return_value=httpx.Response(403))
        auth = StuMgmtAuth(_SPARKY_URL, "u", "p")
        client = StuMgmtClient(_API_URL, auth)
        with pytest.raises(PermissionError, match="Tutor/Lecturer"):
            client.get(_ADMISSION_PATH)


# --- StuMgmtClient 5xx tests ---


@respx.mock
def test_client_5xx_retries_three_times_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("planer.adapters.ingest_api.time.sleep", lambda _: None)
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _FRESH_JWT}))
    api_route = respx.get(f"{_API_URL}{_ADMISSION_PATH}").mock(return_value=httpx.Response(500))
    auth = StuMgmtAuth(_SPARKY_URL, "u", "p")
    client = StuMgmtClient(_API_URL, auth)
    with pytest.raises(httpx.HTTPStatusError):
        client.get(_ADMISSION_PATH)
    assert api_route.call_count == 3


@respx.mock
def test_client_5xx_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("planer.adapters.ingest_api.time.sleep", lambda _: None)
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _FRESH_JWT}))
    api_route = respx.get(f"{_API_URL}{_ADMISSION_PATH}")
    api_route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json=[]),
    ]
    auth = StuMgmtAuth(_SPARKY_URL, "u", "p")
    client = StuMgmtClient(_API_URL, auth)
    assert client.get(_ADMISSION_PATH) == []


# --- fetch_participants (end-to-end with mocked HTTP) ---


@respx.mock
def test_fetch_participants_happy_path(
    sample_admission_status: list[dict], sample_groups: list[dict]
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    participants = fetch_participants(_API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass")
    assert len(participants) == 3
    assert next(p for p in participants if p.user_id == "u1").group_name == "Gruppe 1"


@respx.mock
def test_fetch_participants_sends_bearer_token(
    sample_admission_status: list[dict], sample_groups: list[dict]
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    fetch_participants(_API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass")
    call = respx.get(f"{_API_URL}{_ADMISSION_PATH}").calls[0]
    assert call.request.headers["authorization"] == f"Bearer {_FRESH_JWT}"


@respx.mock
def test_fetch_participants_enriches_has_assessment(
    sample_admission_status: list[dict], sample_groups: list[dict]
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    respx.get(f"{_API_URL}/courses/{_COURSE_ID}/assignments").mock(
        return_value=httpx.Response(200, json=[{"id": "a1", "name": "Vorstellung", "points": 10}])
    )
    respx.get(f"{_API_URL}/courses/{_COURSE_ID}/assignments/a1/assessments").mock(
        return_value=httpx.Response(
            200, json=[{"id": "as1", "userId": "u1", "isDraft": False, "achievedPoints": 10}]
        )
    )
    participants = fetch_participants(
        _API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass", presentation_assignment_id="a1"
    )
    by_id = {p.user_id: p for p in participants}
    assert by_id["u1"].has_assessment is True
    assert by_id["u2"].has_assessment is False


def _mock_enrichment(assessments: list[dict]) -> None:
    """Wire up the assignments + assessments reads for an enrichment test."""
    respx.get(f"{_API_URL}/courses/{_COURSE_ID}/assignments").mock(
        return_value=httpx.Response(200, json=[{"id": "a1", "name": "Vorstellung", "points": 10}])
    )
    respx.get(f"{_API_URL}/courses/{_COURSE_ID}/assignments/a1/assessments").mock(
        return_value=httpx.Response(200, json=assessments)
    )


@respx.mock
def test_draft_assessment_does_not_flag(
    sample_admission_status: list[dict], sample_groups: list[dict]
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    _mock_enrichment([{"id": "as1", "userId": "u1", "isDraft": True, "achievedPoints": 10}])
    participants = fetch_participants(
        _API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass", presentation_assignment_id="a1"
    )
    # draft → not yet released → student stays due
    assert all(p.has_assessment is False for p in participants)


@respx.mock
def test_zero_point_assessment_does_not_flag(
    sample_admission_status: list[dict], sample_groups: list[dict]
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    _mock_enrichment([{"id": "as1", "userId": "u1", "isDraft": False, "achievedPoints": 0}])
    participants = fetch_participants(
        _API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass", presentation_assignment_id="a1"
    )
    # 0 points (< pass threshold) → not passed → student stays due
    assert all(p.has_assessment is False for p in participants)


@respx.mock
def test_string_points_parsed(
    sample_admission_status: list[dict], sample_groups: list[dict]
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    _mock_enrichment([{"id": "as1", "userId": "u1", "isDraft": False, "achievedPoints": "5"}])
    participants = fetch_participants(
        _API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass", presentation_assignment_id="a1"
    )
    by_id = {p.user_id: p for p in participants}
    assert by_id["u1"].has_assessment is True


@respx.mock
def test_pass_points_threshold_respected(
    sample_admission_status: list[dict], sample_groups: list[dict]
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    _mock_enrichment([{"id": "as1", "userId": "u1", "isDraft": False, "achievedPoints": 3}])
    participants = fetch_participants(
        _API_URL,
        _COURSE_ID,
        _SPARKY_URL,
        "user",
        "pass",
        presentation_assignment_id="a1",
        presentation_pass_points=5,
    )
    # 3 points below the configured threshold of 5 → not passed
    assert all(p.has_assessment is False for p in participants)


@respx.mock
def test_fetch_participants_skips_enrichment_without_assignment(
    sample_admission_status: list[dict], sample_groups: list[dict]
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    assignments_route = respx.get(f"{_API_URL}/courses/{_COURSE_ID}/assignments")
    fetch_participants(_API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass")
    assert assignments_route.call_count == 0
    # no assignment configured → nobody flagged
    fetched = fetch_participants(_API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass")
    assert all(p.has_assessment is False for p in fetched)


@respx.mock
def test_fetch_participants_sparky_auth_failure_raises() -> None:
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_participants(_API_URL, _COURSE_ID, _SPARKY_URL, "bad", "creds")


@respx.mock
def test_fetch_participants_api_server_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("planer.adapters.ingest_api.time.sleep", lambda _: None)
    respx.post(_SPARKY_URL).mock(return_value=httpx.Response(200, json={"token": _FRESH_JWT}))
    respx.get(f"{_API_URL}{_ADMISSION_PATH}").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_participants(_API_URL, _COURSE_ID, _SPARKY_URL, "user", "pass")


# --- Log security ---


@respx.mock
def test_no_credentials_or_token_in_logs(
    caplog: pytest.LogCaptureFixture,
    sample_admission_status: list[dict],
    sample_groups: list[dict],
) -> None:
    _mock_api(sample_admission_status, sample_groups)
    with caplog.at_level(logging.DEBUG):
        fetch_participants(_API_URL, _COURSE_ID, _SPARKY_URL, "s3cr3t_user", "s3cr3t_pass")
    all_logs = " ".join(caplog.messages)
    assert "s3cr3t_user" not in all_logs
    assert "s3cr3t_pass" not in all_logs
    assert _FRESH_JWT not in all_logs
