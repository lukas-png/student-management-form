from collections import defaultdict

from planer.domain.models import Group, Participant, Result, resolve_group_id

_RULE = "INDIVIDUAL_PERCENT_WITH_ALLOWED_FAILURES"
_ASSIGNMENT_TYPE = "HOMEWORK"


def individual_result(participant: Participant) -> Result | None:
    """The INDIVIDUAL_PERCENT homework result, matched by rule name (never index)."""
    return next(
        (
            r
            for r in participant.results
            if r.rule == _RULE and r.assignment_type == _ASSIGNMENT_TYPE
        ),
        None,
    )


def is_due(participant: Participant, threshold: int) -> bool:
    """True if the participant still has to present.

    Criterion is the homework points only: ``achieved_points < threshold``.
    ``has_admission`` is deliberately NOT required — a student without admission
    can still earn it via the homework/presentation, so they remain due.

    Excluded (never due) regardless of points:
      - Altzulassung (``has_admission_from_previous_semester``) — already admitted
        from an earlier semester, need not present.
      - An assessment already exists on the presentation assignment — already examined.
    """
    if participant.has_admission_from_previous_semester:
        return False
    if participant.has_assessment:
        return False
    result = individual_result(participant)
    if result is None:
        return False
    return result.achieved_points < threshold


def build_groups(participants: list[Participant]) -> list[Group]:
    """Build groups from all participants.

    Students with an empty group_id each form a solo group (id = user_id).
    All members are included, not just those that are due.
    """
    by_group: defaultdict[str, list[Participant]] = defaultdict(list)
    group_names: dict[str, str] = {}
    for p in participants:
        gid = resolve_group_id(p)
        gname = p.group_name if p.group_id else p.display_name
        by_group[gid].append(p)
        group_names.setdefault(gid, gname)
    return [
        Group(id=gid, name=group_names[gid], members=tuple(members))
        for gid, members in by_group.items()
    ]


def select_due_groups(groups: list[Group], threshold: int) -> list[Group]:
    """Return groups where at least one member is due."""
    return [g for g in groups if any(is_due(m, threshold) for m in g.members)]
