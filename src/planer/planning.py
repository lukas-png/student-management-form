"""Round-solving orchestration shared by the admin UI and the CLI.

Gathers slots, availabilities, curations, groups and prior-round outcomes from
the DB, runs the pure domain solver, and persists the resulting Presentations.
Pure scheduling stays in ``domain/scheduling.py``; this is just the I/O wiring.
"""

from __future__ import annotations

from sqlmodel import Session

from planer.adapters.db import (
    PresentationStatus,
    build_domain_groups,
    create_presentation,
    delete_presentations_for_round,
    get_all_groups,
    get_availabilities_for_round,
    get_curations_for_round,
    get_presentations_before_round,
    get_round,
    get_slots_for_round,
    get_students_in_groups,
)
from planer.domain.availability import group_feasible_slots, member_declined
from planer.domain.filtering import due_members, is_due
from planer.domain.models import Slot as DomainSlot
from planer.domain.scheduling import ScheduleResult, assign
from planer.domain.tracking import compute_carry_over
from planer.logging_setup import get_logger

logger = get_logger("planning")


def solve_round(session: Session, round_id: int, threshold: int) -> ScheduleResult:
    """Run the solver for a round and persist its Presentations (replacing any prior run).

    Only *due* groups are scheduled: a group is kept when at least one member is
    still due (``is_due`` with ``threshold``). Members with an Altzulassung or an
    existing assessment are not due, so a fully-covered group drops out.
    """
    rnd = get_round(session, round_id)
    require_all = rnd is None or rnd.quorum != "any"
    slots = get_slots_for_round(session, round_id)
    domain_slots = [DomainSlot(id=str(s.id), capacity=s.capacity) for s in slots]
    slot_capacities = {str(s.id): s.capacity for s in slots}

    db_avails = get_availabilities_for_round(session, round_id)
    avail_dict: dict[tuple[str, str], bool] = {
        (a.student_id, str(a.slot_id)): a.available for a in db_avails
    }

    curations = {c.group_id: c for c in get_curations_for_round(session, round_id)}

    db_groups = get_all_groups(session)
    db_students = get_students_in_groups(session, [g.id for g in db_groups])
    domain_groups = build_domain_groups(db_groups, db_students)

    # Carry-over: groups that already PRESENTED in an earlier round are done.
    prior = get_presentations_before_round(session, round_id)
    carry = compute_carry_over(
        [p.group_id for p in prior if p.status == PresentationStatus.PRESENTED],
        [p.group_id for p in prior if p.status == PresentationStatus.NO_SHOW],
    )

    included_groups = [
        dg
        for dg in domain_groups
        if (curations.get(dg.id) is None or curations[dg.id].included)
        and dg.id not in carry.excluded
        and any(is_due(m, threshold) for m in dg.members)
    ]
    excluded_not_due = sum(
        1
        for dg in domain_groups
        if (curations.get(dg.id) is None or curations[dg.id].included)
        and dg.id not in carry.excluded
        and not any(is_due(m, threshold) for m in dg.members)
    )

    due_by_id = {dg.id: due_members(dg, threshold) for dg in domain_groups}
    feasible: dict[str, set[str]] = {
        dg.id: group_feasible_slots(
            due_by_id[dg.id], avail_dict, domain_slots, require_all=require_all
        )
        for dg in included_groups
    }

    # Pinned groups are placed on their fixed slot. But if a (due) member declined
    # that slot, hold the group back (it surfaces as a conflict in the dashboard) —
    # unless the admin set the override to schedule it anyway.
    pinned: dict[str, str] = {}
    for gid, c in curations.items():
        if c.pinned_slot_id is None or not c.included:
            continue
        pin_slot = str(c.pinned_slot_id)
        group = due_by_id.get(gid)
        declined = group is not None and member_declined(
            group, pin_slot, avail_dict, require_all=require_all
        )
        if declined and not c.override:
            continue  # conflict — leave unscheduled for the admin to resolve
        pinned[gid] = pin_slot

    result = assign(included_groups, feasible, slot_capacities, pinned)

    delete_presentations_for_round(session, round_id)
    for gid, sid in result.assignments.items():
        create_presentation(session, gid, int(sid), round_id)

    logger.info(
        "round solved",
        extra={
            "round_id": round_id,
            "groups": len(included_groups),
            "slots": len(slots),
            "assigned": len(result.assignments),
            "unplaced": len(result.unplaced),
            "excluded_carry_over": len(carry.excluded),
            "excluded_not_due": excluded_not_due,
        },
    )
    if result.unplaced:
        logger.warning(
            "groups could not be placed",
            extra={"round_id": round_id, "unplaced_groups": [g for g, _ in result.unplaced]},
        )
    return result
