"""Turn presentation outcomes into per-user assessment payloads.

 One assessment **per user** (not
per group). Every member of a group that PRESENTED gets the full points.
Only PRESENTED counts; NO_SHOW / SCHEDULED are ignored.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

PRESENTED = "PRESENTED"


@dataclass(frozen=True)
class AssessmentPayload:
    user_id: str
    achieved_points: int
    is_draft: bool
    comment: str


def build_assessments(
    member_statuses: Iterable[tuple[str, str]],
    points: int,
    comment: str,
    *,
    is_draft: bool = True,
) -> tuple[AssessmentPayload, ...]:
    """One AssessmentPayload per user marked PRESENTED.

    ``member_statuses`` is an iterable of ``(user_id, status)``; only ``PRESENTED``
    rows produce output. A member of a group that presented but who was marked
    absent is excluded.
    """
    user_ids: set[str] = set()
    for user_id, status in member_statuses:
        if status == PRESENTED:
            user_ids.add(user_id)
    return tuple(
        AssessmentPayload(
            user_id=uid,
            achieved_points=points,
            is_draft=is_draft,
            comment=comment,
        )
        for uid in sorted(user_ids)
    )
