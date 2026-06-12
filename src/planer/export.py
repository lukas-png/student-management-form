"""Attendance export: "who presented when" for the records.

Row gathering touches the DB, so this lives outside ``domain/``. The CSV
serialization is pure and separately testable.
"""

from __future__ import annotations

import csv
import io

from sqlmodel import Session

from planer.adapters.db import (
    get_all_groups,
    get_member_attendance_for_round,
    get_presentations_for_round,
    get_slots_for_round,
    get_students_in_groups,
)

_FIELDS = [
    "round_id",
    "group_id",
    "group_name",
    "member_name",
    "matriculation",
    "slot_starts_at",
    "room",
    "status",
    "marked_at",
]


def build_export_rows(session: Session, round_id: int) -> list[dict[str, str]]:
    """One row per group member per presentation in the round, ordered for stability."""
    presentations = get_presentations_for_round(session, round_id)
    slots = {s.id: s for s in get_slots_for_round(session, round_id)}
    group_names = {g.id: g.name for g in get_all_groups(session)}
    members_by_group: dict[str, list] = {}
    for student in get_students_in_groups(session, [p.group_id for p in presentations]):
        members_by_group.setdefault(student.group_id, []).append(student)
    attendance = {a.student_id: a for a in get_member_attendance_for_round(session, round_id)}

    rows: list[dict[str, str]] = []
    for pres in sorted(presentations, key=lambda p: p.group_id):
        slot = slots.get(pres.slot_id)
        slot_time = slot.starts_at.strftime("%Y-%m-%d %H:%M") if slot else ""
        room = slot.room if slot else ""
        members = sorted(members_by_group.get(pres.group_id, []), key=lambda s: s.id)
        if not members:
            members = [None]  # still emit a row for a member-less group
        group_marked = pres.marked_at.strftime("%Y-%m-%d %H:%M") if pres.marked_at else ""
        for member in members:
            att = attendance.get(member.id) if member else None
            status = str(att.status) if att else str(pres.status)
            marked = (
                att.marked_at.strftime("%Y-%m-%d %H:%M") if att and att.marked_at else group_marked
            )
            rows.append(
                {
                    "round_id": str(round_id),
                    "group_id": pres.group_id,
                    "group_name": group_names.get(pres.group_id, pres.group_id),
                    "member_name": member.name if member else "",
                    "matriculation": member.matr_nr if member else "",
                    "slot_starts_at": slot_time,
                    "room": room,
                    "status": status,
                    "marked_at": marked,
                }
            )
    return rows


def rows_to_csv(rows: list[dict[str, str]]) -> str:
    """Serialize export rows to CSV text (header always present)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
