"""Turn presentation outcomes into per-user assessment payloads.

 One assessment **per user** (not
per group). Every member of a group that PRESENTED gets the full points.
Only PRESENTED counts; NO_SHOW / SCHEDULED are ignored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

PRESENTED = "PRESENTED"


@dataclass(frozen=True)
class AssessmentPayload:
    user_id: str
    achieved_points: int
    is_draft: bool
    comment: str


def build_assessments(
    presentations: Iterable[tuple[str, str]],
    members_by_group: Mapping[str, Sequence[str]],
    points: int,
    comment: str,
    *,
    is_draft: bool = True,
) -> tuple[AssessmentPayload, ...]:
    """One AssessmentPayload per member of every PRESENTED group.

    ``presentations`` is an iterable of ``(group_id, status)``; only ``PRESENTED``
    rows produce output. ``members_by_group`` maps a group id to its members'
    user ids.
    """
    user_ids: set[str] = set()
    for group_id, status in presentations:
        if status != PRESENTED:
            continue
        user_ids.update(members_by_group.get(group_id, ()))
    return tuple(
        AssessmentPayload(
            user_id=uid,
            achieved_points=points,
            is_draft=is_draft,
            comment=comment,
        )
        for uid in sorted(user_ids)
    )
