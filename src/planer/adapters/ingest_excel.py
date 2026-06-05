from pathlib import Path

import openpyxl

from planer.domain.models import Participant, Result, parse_bool, parse_int

# Expected column headers (case-sensitive).
# One row per participant-result pair; participants with multiple admission
# results appear on multiple rows and are grouped by userId.
_REQUIRED_COLUMNS = {
    "userId",
    "displayName",
    "email",
    "matriculationNumber",
    "groupId",
    "groupName",
    "hasAdmission",
    "rule",
    "assignmentType",
    "achievedPoints",
    "passed",
}


def parse_excel(path: Path) -> list[Participant]:
    """Parse a stu-mgmt Excel export into Participant domain objects.

    Booleans (hasAdmission, passed) may be native bool or WAHR/FALSCH strings.
    achievedPoints may be an integer or a numeric string.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return []

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(h) for h in rows[0]]
    missing = _REQUIRED_COLUMNS - set(headers)
    if missing:
        raise ValueError(f"Excel is missing required columns: {sorted(missing)}")

    by_user: dict[str, dict[str, object]] = {}
    user_results: dict[str, list[Result]] = {}

    for row in rows[1:]:
        raw: dict[str, object] = dict(zip(headers, row, strict=False))
        uid = str(raw["userId"])
        if uid not in by_user:
            by_user[uid] = raw
            user_results[uid] = []
        user_results[uid].append(
            Result(
                rule=str(raw["rule"]),
                assignment_type=str(raw["assignmentType"]),
                achieved_points=parse_int(raw["achievedPoints"]),  # type: ignore[arg-type]
                passed=parse_bool(raw["passed"]),  # type: ignore[arg-type]
            )
        )

    return [
        Participant(
            user_id=str(p["userId"]),
            display_name=str(p["displayName"]),
            email=str(p["email"]),
            matr_nr=str(p["matriculationNumber"]),
            group_id=str(p.get("groupId") or ""),
            group_name=str(p.get("groupName") or ""),
            has_admission=parse_bool(p["hasAdmission"]),  # type: ignore[arg-type]
            results=tuple(user_results[str(p["userId"])]),
        )
        for p in by_user.values()
    ]
