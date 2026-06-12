import time
from dataclasses import replace

import httpx

from planer.adapters.sparky import StuMgmtAuth
from planer.domain.models import Participant, Result, parse_bool, parse_int

_RETRY_STATUSES = frozenset({500, 502, 503, 504})


class StuMgmtClient:
    """Thin httpx wrapper: Bearer auth, 401 re-auth retry, 5xx backoff."""

    def __init__(self, base_url: str, auth: StuMgmtAuth) -> None:
        self._base_url = base_url
        self._auth = auth

    def get(self, path: str) -> list | dict:
        return self._request("GET", path)

    def post(self, path: str, json_body: dict | list) -> list | dict:
        return self._request("POST", path, json_body)

    def patch(self, path: str, json_body: dict) -> list | dict:
        return self._request("PATCH", path, json_body)

    def _request(self, method: str, path: str, json_body: dict | list | None = None) -> list | dict:
        url = f"{self._base_url}{path}"
        _auth_retried = False
        for attempt in range(3):
            resp = httpx.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self._auth.token()}"},
                json=json_body,
                timeout=30.0,
            )
            if resp.status_code == 401 and not _auth_retried:
                _auth_retried = True
                self._auth.invalidate()
                continue
            if resp.status_code == 403:
                raise PermissionError(f"Access denied for {path} — Tutor/Lecturer role required")
            if resp.status_code in _RETRY_STATUSES and attempt < 2:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()  # type: ignore[return-value]
        raise RuntimeError("max retries exceeded")  # unreachable


def fetch_participants(
    api_url: str,
    course_id: str,
    auth_url: str,
    sparky_user: str,
    sparky_password: str,
    presentation_assignment_id: str = "",
    presentation_assignment_name: str = "",
    presentation_pass_points: int = 1,
) -> list[Participant]:
    """Fetch course participants from stu-mgmt and merge them into domain objects.

    Reads (verified against the live stu-mgmt OpenAPI):
      - GET /courses/{id}/admission-status → identity + hasAdmission
        + hasAdmissionFromPreviousSemester + results[]
      - GET /courses/{id}/groups           → group membership per user

    If a presentation assignment is configured, a third read marks everyone who
    already has a **passed, released** assessment there (``has_assessment=True``)
    so the planner can treat them as already examined. Draft assessments and
    those below ``presentation_pass_points`` do not count. Skipped when no assignment is configured.
    """
    auth = StuMgmtAuth(auth_url, sparky_user, sparky_password)
    client = StuMgmtClient(api_url, auth)
    admission = client.get(f"/courses/{course_id}/admission-status")
    groups = client.get(f"/courses/{course_id}/groups")
    participants = to_participants(admission, groups)  # type: ignore[arg-type]

    if presentation_assignment_id.strip() or presentation_assignment_name.strip():
        assignment = resolve_assignment(
            client,
            course_id,
            want_id=presentation_assignment_id,
            want_name=presentation_assignment_name,
        )
        assessed = passed_assessment_user_ids(
            client, course_id, str(assignment["id"]), presentation_pass_points
        )
        if assessed:
            participants = [
                replace(p, has_assessment=True) if p.user_id in assessed else p
                for p in participants
            ]
    return participants


# ---------------------------------------------------------------------------
# Assessment reads (shared with the results-sync adapter)
# ---------------------------------------------------------------------------


def points_of(assignment: dict) -> int:
    return int(assignment.get("points", 0) or 0)


def resolve_assignment(
    client: StuMgmtClient,
    course_id: str,
    *,
    want_id: str = "",
    want_name: str = "",
) -> dict:
    """Find the target assignment by configured id, else by exact name.

    Fails fast (ValueError) when nothing is configured, the id is unknown, or a
    name matches zero / more than one assignment.
    """
    want_id = want_id.strip()
    want_name = want_name.strip()
    if not want_id and not want_name:
        raise ValueError(
            "No presentation assignment configured — set "
            "STUMGMT_PRESENTATION_ASSIGNMENT_ID or STUMGMT_PRESENTATION_ASSIGNMENT_NAME"
        )
    assignments = client.get(f"/courses/{course_id}/assignments")
    if not isinstance(assignments, list):
        raise ValueError("Unexpected /assignments response (expected a list)")

    if want_id:
        match = next((a for a in assignments if str(a.get("id")) == want_id), None)
        if match is None:
            raise ValueError(f"Assignment id {want_id!r} not found in course {course_id}")
        return match

    matches = [a for a in assignments if a.get("name") == want_name]
    if len(matches) != 1:
        raise ValueError(
            f"Assignment name {want_name!r} matched {len(matches)} assignments "
            "(need exactly one) — use STUMGMT_PRESENTATION_ASSIGNMENT_ID instead"
        )
    return matches[0]


def _assessment_user_id(entry: dict) -> str | None:
    """User id an existing assessment belongs to, across known response shapes."""
    uid = entry.get("userId")
    if uid:
        return str(uid)
    participant = entry.get("participant") or entry.get("user")
    if isinstance(participant, dict) and participant.get("userId"):
        return str(participant["userId"])
    if isinstance(participant, dict) and participant.get("id"):
        return str(participant["id"])
    return None


def existing_assessments_by_user(
    client: StuMgmtClient, course_id: str, assignment_id: str
) -> dict[str, str]:
    """Map user_id -> assessment_id for assessments that already exist."""
    existing = client.get(f"/courses/{course_id}/assignments/{assignment_id}/assessments")
    result: dict[str, str] = {}
    if isinstance(existing, list):
        for entry in existing:
            uid = _assessment_user_id(entry)
            if uid and entry.get("id"):
                result[uid] = str(entry["id"])
    return result


def passed_assessment_user_ids(
    client: StuMgmtClient, course_id: str, assignment_id: str, pass_points: int
) -> set[str]:
    """user_ids with a released (non-draft) assessment of at least ``pass_points``.

    Used by the import enrichment to flag students as already examined. Unlike
    ``existing_assessments_by_user`` (which the results-sync needs to find *every*
    assessment, including drafts, for idempotency), this deliberately ignores
    drafts and failing/zero-point assessments.
    """
    existing = client.get(f"/courses/{course_id}/assignments/{assignment_id}/assessments")
    result: set[str] = set()
    if isinstance(existing, list):
        for entry in existing:
            if entry.get("isDraft"):  
                continue
            try:
                points = float(entry.get("achievedPoints") or 0)  # may arrive as a string
            except (TypeError, ValueError):
                points = 0.0
            if points < pass_points:  # not passed
                continue
            uid = _assessment_user_id(entry)
            if uid:
                result.add(uid)
    return result


def to_participants(
    admission_status: list[dict[str, object]],
    groups: list[dict[str, object]],
) -> list[Participant]:
    """Merge admission-status entries with group membership into Participants.

    The admission-status endpoint nests identity under ``participant`` and lists
    every grading rule in ``results`` (each with ``_rule``/``_assignmentType``);
    the domain filter picks the relevant INDIVIDUAL rule by name. Group id/name
    come from the groups endpoint; a user in no group → empty group (solo).
    """
    group_of: dict[str, tuple[str, str]] = {}
    for g in groups:
        members = g.get("members") or []
        for m in members:  # type: ignore[union-attr]
            group_of[str(m["userId"])] = (str(g["id"]), str(g["name"]))

    participants: list[Participant] = []
    for entry in admission_status:
        person = entry["participant"]  # type: ignore[index]
        uid = str(person["userId"])  # type: ignore[index]
        group_id, group_name = group_of.get(uid, ("", ""))
        results = tuple(
            Result(
                rule=str(r["_rule"]),
                assignment_type=str(r["_assignmentType"]),
                achieved_points=parse_int(r["achievedPoints"]),  # type: ignore[arg-type]
                passed=parse_bool(r["passed"]),  # type: ignore[arg-type]
            )
            for r in (entry.get("results") or [])  # type: ignore[union-attr]
        )
        participants.append(
            Participant(
                user_id=uid,
                display_name=str(person["displayName"]),  # type: ignore[index]
                email=str(person["email"]),  # type: ignore[index]
                matr_nr=str(person["matrNr"]),  # type: ignore[index]
                group_id=group_id,
                group_name=group_name,
                has_admission=parse_bool(entry["hasAdmission"]),  # type: ignore[arg-type]
                has_admission_from_previous_semester=parse_bool(
                    entry.get("hasAdmissionFromPreviousSemester", False)  # type: ignore[arg-type]
                ),
                results=results,
            )
        )
    return participants
