from dataclasses import dataclass

from planer.domain.models import Group


@dataclass
class ScheduleResult:
    assignments: dict[str, str]  # group_id -> slot_id
    unplaced: list[tuple[str, str]]  # (group_id, reason)


def assign(
    groups: list[Group],
    feasible_slots: dict[str, set[str]],
    slot_capacities: dict[str, int],
    pinned: dict[str, str],
) -> ScheduleResult:
    """Greedy most-constrained-first assignment.

    1. Pinned groups are placed first and their slots' capacities are reduced.
    2. Remaining groups are sorted ascending by number of feasible slots
       (most constrained first), with group_id as a stable tiebreaker.
    3. Each group is placed in the feasible slot with the most remaining
       capacity; ties are broken by ascending slot_id (lexicographic).
    4. Groups with no available feasible slot land in `unplaced` with a reason.

    The algorithm is deterministic: identical inputs always produce identical outputs.
    """
    remaining: dict[str, int] = dict(slot_capacities)
    assignments: dict[str, str] = {}
    unplaced: list[tuple[str, str]] = []

    for gid, sid in pinned.items():
        assignments[gid] = sid
        remaining[sid] -= 1

    pinned_ids = set(pinned.keys())
    free_groups = [g for g in groups if g.id not in pinned_ids]
    free_groups.sort(key=lambda g: (len(feasible_slots.get(g.id, set())), g.id))

    for group in free_groups:
        feasible = feasible_slots.get(group.id, set())
        candidates = [sid for sid in feasible if remaining.get(sid, 0) > 0]
        if not candidates:
            reason = "no feasible slots" if not feasible else "all feasible slots at capacity"
            unplaced.append((group.id, reason))
            continue
        candidates.sort(key=lambda sid: (-remaining.get(sid, 0), sid))
        chosen = candidates[0]
        assignments[group.id] = chosen
        remaining[chosen] -= 1

    return ScheduleResult(assignments=assignments, unplaced=unplaced)
