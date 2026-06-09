from collections import defaultdict

from planer.domain.models import Group, Participant, resolve_group_id

_RULE = "INDIVIDUAL_PERCENT_WITH_ALLOWED_FAILURES"
_ASSIGNMENT_TYPE = "HOMEWORK"


def is_due(participant: Participant, threshold: int) -> bool:
    """True if participant is below the threshold in the INDIVIDUAL_PERCENT rule."""
    result = next(
        (
            r
            for r in participant.results
            if r.rule == _RULE and r.assignment_type == _ASSIGNMENT_TYPE
        ),
        None,
    )
    if result is None:
        return False
    if not participant.has_admission:
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
