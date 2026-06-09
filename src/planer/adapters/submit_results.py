"""Write presentation outcomes back to stu-mgmt as per-user assessments.

Manual sync step (CLI ``planer sync`` / admin button). For every member of a
group that PRESENTED in the round, create an assessment on the configured
homework assignment with full points as a **draft** (``isDraft=true``) the
assignment's mandatory pass-rule then tracks who presented once the draft is
released in the stu-mgmt UI.

Idempotent per user: a member who already has an assessment is skipped, unless
``force`` is set (then the existing assessment is PATCHed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session

from planer.adapters.ingest_api import StuMgmtClient
from planer.adapters.sparky import StuMgmtAuth
from planer.config import Settings
from planer.domain.results_submission import build_assessments
from planer.logging_setup import get_logger

logger = get_logger("submit_results")


@dataclass
class SubmitReport:
    assignment_id: str = ""
    created: list[str] = field(default_factory=list)  # user_ids newly assessed
    updated: list[str] = field(default_factory=list)  # user_ids PATCHed (force)
    skipped: list[str] = field(default_factory=list)  # already assessed (idempotency)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (user_id, error)
    dry_run: bool = False


def _points_of(assignment: dict) -> int:
    return int(assignment.get("points", 0) or 0)


def _resolve_assignment(client: StuMgmtClient, course_id: str, settings: Settings) -> dict:
    """Find the target assignment by configured id, else by exact name.

    Fails fast (ValueError) when nothing is configured, the id is unknown, or a
    name matches zero / more than one assignment.
    """
    want_id = settings.stumgmt_presentation_assignment_id.strip()
    want_name = settings.stumgmt_presentation_assignment_name.strip()
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


def _existing_by_user(client: StuMgmtClient, course_id: str, assignment_id: str) -> dict[str, str]:
    """Map user_id -> assessment_id for assessments that already exist."""
    existing = client.get(f"/courses/{course_id}/assignments/{assignment_id}/assessments")
    result: dict[str, str] = {}
    if isinstance(existing, list):
        for entry in existing:
            uid = _assessment_user_id(entry)
            if uid and entry.get("id"):
                result[uid] = str(entry["id"])
    return result


def submit_presentations(
    session: Session,
    settings: Settings,
    round_id: int,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> SubmitReport:
    """Write per-user assessments for everyone who PRESENTED in ``round_id``."""
    # Local imports keep adapters/ from depending on each other at module load.
    from planer.adapters.db import get_all_students, get_presentations_for_round

    course_id = settings.stumgmt_course_id
    auth = StuMgmtAuth(settings.sparky_auth_url, settings.sparky_user, settings.sparky_password)
    client = StuMgmtClient(settings.stumgmt_api_url, auth)

    assignment = _resolve_assignment(client, course_id, settings)
    assignment_id = str(assignment["id"])
    points = _points_of(assignment)
    report = SubmitReport(assignment_id=assignment_id, dry_run=dry_run)

    # Members per group, using the same "solo students group under their own id"
    # rule as the rest of the app (group_id or id).
    members_by_group: dict[str, list[str]] = {}
    for s in get_all_students(session):
        members_by_group.setdefault(s.group_id or s.id, []).append(s.id)

    presentations = [
        (p.group_id, str(p.status)) for p in get_presentations_for_round(session, round_id)
    ]
    comment = f"Vorgestellt (Tutorium-Planer, Runde {round_id}, {date.today().isoformat()})"
    payloads = build_assessments(presentations, members_by_group, points, comment)

    # Read-only; safe in dry-run too and makes the preview's skipped/created accurate.
    existing = _existing_by_user(client, course_id, assignment_id)

    to_create: list[dict] = []
    for pl in payloads:
        if pl.user_id in existing:
            if not force:
                report.skipped.append(pl.user_id)
                continue
            if dry_run:
                report.updated.append(pl.user_id)
                continue
            try:
                client.patch(
                    f"/courses/{course_id}/assignments/{assignment_id}"
                    f"/assessments/{existing[pl.user_id]}",
                    {
                        "achievedPoints": pl.achieved_points,
                        "isDraft": pl.is_draft,
                        "comment": pl.comment,
                    },
                )
                report.updated.append(pl.user_id)
            except Exception as exc: 
                report.failed.append((pl.user_id, str(exc)))
            continue
        if dry_run:
            report.created.append(pl.user_id)
            continue
        to_create.append(
            {
                "assignmentId": assignment_id,
                "isDraft": pl.is_draft,
                "achievedPoints": pl.achieved_points,
                "comment": pl.comment,
                "userId": pl.user_id,
            }
        )

    if to_create:
        try:
            client.post(
                f"/courses/{course_id}/assignments/{assignment_id}/assessments/bulk",
                to_create,
            )
            report.created.extend(dto["userId"] for dto in to_create)
        except Exception as exc:  
            report.failed.extend((dto["userId"], str(exc)) for dto in to_create)

    logger.info(
        "presentation sync complete",
        extra={
            "round_id": round_id,
            "assignment_id": assignment_id,
            "n_created": len(report.created),
            "n_updated": len(report.updated),
            "n_skipped": len(report.skipped),
            "n_failed": len(report.failed),
            "dry_run": dry_run,
        },
    )
    return report
