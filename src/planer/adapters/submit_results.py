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

from planer.adapters.ingest_api import (
    StuMgmtClient,
    existing_assessments_by_user,
    points_of,
    resolve_assignment,
)
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

    assignment = resolve_assignment(
        client,
        course_id,
        want_id=settings.stumgmt_presentation_assignment_id,
        want_name=settings.stumgmt_presentation_assignment_name,
    )
    assignment_id = str(assignment["id"])
    points = points_of(assignment)
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
    existing = existing_assessments_by_user(client, course_id, assignment_id)

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
