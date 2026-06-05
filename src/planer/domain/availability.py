from planer.domain.models import Group, Slot


def group_feasible_slots(
    group: Group,
    availabilities: dict[tuple[str, str], bool],
    all_slots: list[Slot],
) -> set[str]:
    """Slot IDs where every group member is explicitly available.

    A missing entry (no response submitted) counts as unavailable.
    Returns an empty set if no slot satisfies all members — caller must
    surface this as a conflict, never silently ignore it.
    """
    feasible: set[str] = {s.id for s in all_slots}
    for member in group.members:
        feasible &= {s.id for s in all_slots if availabilities.get((member.user_id, s.id)) is True}
    return feasible


def member_declined(
    group: Group,
    slot_id: str,
    availabilities: dict[tuple[str, str], bool],
) -> bool:
    """True if any member explicitly marked this slot as unavailable (said 'no').

    Distinguishes an active decline from a mere non-response: only an explicit
    ``False`` counts. Used to flag a pinned slot as a conflict.
    """
    return any(availabilities.get((m.user_id, slot_id)) is False for m in group.members)
