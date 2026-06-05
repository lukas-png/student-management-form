import time

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
        url = f"{self._base_url}{path}"
        _auth_retried = False
        for attempt in range(3):
            resp = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self._auth.token()}"},
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
            return resp.json()  # type: ignore[return-value]
        raise RuntimeError("max retries exceeded")  # unreachable


def fetch_participants(
    api_url: str,
    course_id: str,
    auth_url: str,
    sparky_user: str,
    sparky_password: str,
) -> list[Participant]:
    """Fetch course participants from stu-mgmt and merge them into domain objects.

    Two reads (verified against the live stu-mgmt OpenAPI):
      - GET /courses/{id}/admission-status → identity + hasAdmission + results[]
      - GET /courses/{id}/groups           → group membership per user
    """
    auth = StuMgmtAuth(auth_url, sparky_user, sparky_password)
    client = StuMgmtClient(api_url, auth)
    admission = client.get(f"/courses/{course_id}/admission-status")
    groups = client.get(f"/courses/{course_id}/groups")
    return to_participants(admission, groups)  # type: ignore[arg-type]


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
                results=results,
            )
        )
    return participants
