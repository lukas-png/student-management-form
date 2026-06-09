from planer.domain.models import Group, Slot


def group_feasible_slots(
    group: Group,
    availabilities: dict[tuple[str, str], bool],
    all_slots: list[Slot],
    *,
    require_all: bool = True,
) -> set[str]:
    """Slot IDs where the group's availability quorum is met.

    A missing entry (no response submitted) counts as unavailable.

    ``require_all=True`` (default): a slot is feasible only when **every** member
    is explicitly available (intersection). ``require_all=False``: a slot is
    feasible as soon as **at least one** member is explicitly available (union) ->
    used when assessments are graded individually and one attendee suffices.

    Returns an empty set if no slot meets the quorum — caller must surface this
    as a conflict, never silently ignore it.
    """
    if require_all:
        feasible: set[str] = {s.id for s in all_slots}
        for member in group.members:
            feasible &= {
                s.id for s in all_slots if availabilities.get((member.user_id, s.id)) is True
            }
        return feasible
    feasible = set()
    for member in group.members:
        feasible |= {s.id for s in all_slots if availabilities.get((member.user_id, s.id)) is True}
    return feasible


def member_declined(
    group: Group,
    slot_id: str,
    availabilities: dict[tuple[str, str], bool],
    *,
    require_all: bool = True,
) -> bool:
    """True if member declines block this pinned slot under the group's quorum.

    Distinguishes an active decline from a mere non-response: only an explicit
    ``False`` counts; silence never does. Used to flag a pinned slot as a conflict.

    ``require_all=True`` (default): a **single** member's decline blocks the slot.
    ``require_all=False``: only a decline by **every** member blocks it (one
    available or silent member is enough to keep the slot viable).
    """
    declines = [m for m in group.members if availabilities.get((m.user_id, slot_id)) is False]
    if require_all:
        return len(declines) > 0
    return len(declines) > 0 and len(declines) == len(group.members)
