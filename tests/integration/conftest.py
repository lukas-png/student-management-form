"""Session-scoped fixtures shared by all integration tests.

SAMPLE_DATA is the canonical source of truth: the same logical data is encoded
three ways — as an Excel workbook (WAHR/FALSCH booleans) and as the two real
stu-mgmt API responses (admission-status + groups, native JSON booleans). The
contract test verifies the Excel and API parsers produce identical Participants.
"""

import json
from pathlib import Path

import openpyxl
import pytest

# Canonical logical records. Each maps to one Excel row, one admission-status
# entry, and (via group_id) one groups-endpoint membership.
SAMPLE_DATA = [
    {
        "user_id": "u1",
        "display_name": "Alice Muster",
        "email": "alice@uni.de",
        "matr_nr": "111111",
        "group_id": "g1",
        "group_name": "Gruppe 1",
        "has_admission": True,
        "rule": "INDIVIDUAL_PERCENT_WITH_ALLOWED_FAILURES",
        "assignment_type": "HOMEWORK",
        "achieved_points": 2,
        "passed": False,
    },
    {
        "user_id": "u2",
        "display_name": "Bob Muster",
        "email": "bob@uni.de",
        "matr_nr": "222222",
        "group_id": "g1",
        "group_name": "Gruppe 1",
        "has_admission": True,
        "rule": "INDIVIDUAL_PERCENT_WITH_ALLOWED_FAILURES",
        "assignment_type": "HOMEWORK",
        "achieved_points": 5,
        "passed": True,
    },
    {
        "user_id": "u3",
        "display_name": "Carol Muster",
        "email": "carol@uni.de",
        "matr_nr": "333333",
        "group_id": "g2",
        "group_name": "Gruppe 2",
        "has_admission": False,
        "rule": "INDIVIDUAL_PERCENT_WITH_ALLOWED_FAILURES",
        "assignment_type": "HOMEWORK",
        "achieved_points": 1,
        "passed": False,
    },
]

_EXCEL_HEADERS = [
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
]


def _bool_to_german(value: bool) -> str:
    return "WAHR" if value else "FALSCH"


def _admission_status() -> list[dict]:
    """SAMPLE_DATA in the real /admission-status shape (nested participant + results)."""
    return [
        {
            "participant": {
                "userId": rec["user_id"],
                "role": "STUDENT",
                "username": rec["user_id"],
                "displayName": rec["display_name"],
                "matrNr": int(rec["matr_nr"]),
                "email": rec["email"],
            },
            "hasAdmission": rec["has_admission"],
            "hasAdmissionFromPreviousSemester": False,
            "fulfillsAdmissionCriteria": rec["has_admission"],
            "results": [
                {
                    "achievedPoints": rec["achieved_points"],
                    "achievedPercent": 0,
                    "passed": rec["passed"],
                    "_rule": rec["rule"],
                    "_assignmentType": rec["assignment_type"],
                }
            ],
        }
        for rec in SAMPLE_DATA
    ]


def _groups() -> list[dict]:
    """SAMPLE_DATA grouped into the real /groups shape (members nested)."""
    by_group: dict[str, dict] = {}
    for rec in SAMPLE_DATA:
        gid = rec["group_id"]
        grp = by_group.setdefault(
            gid,
            {
                "id": gid,
                "name": rec["group_name"],
                "isClosed": False,
                "hasPassword": False,
                "size": 0,
                "members": [],
            },
        )
        grp["members"].append(
            {
                "userId": rec["user_id"],
                "role": "STUDENT",
                "username": rec["user_id"],
                "displayName": rec["display_name"],
                "matrNr": int(rec["matr_nr"]),
                "email": rec["email"],
            }
        )
        grp["size"] = len(grp["members"])
    return list(by_group.values())


@pytest.fixture(scope="session")
def sample_excel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a sample Excel workbook using WAHR/FALSCH string booleans."""
    path = tmp_path_factory.mktemp("fixtures") / "sample.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(_EXCEL_HEADERS)
    for rec in SAMPLE_DATA:
        ws.append(
            [
                rec["user_id"],
                rec["display_name"],
                rec["email"],
                rec["matr_nr"],
                rec["group_id"],
                rec["group_name"],
                _bool_to_german(rec["has_admission"]),
                rec["rule"],
                rec["assignment_type"],
                rec["achieved_points"],
                _bool_to_german(rec["passed"]),
            ]
        )
    wb.save(path)
    return path


@pytest.fixture(scope="session")
def sample_admission_status() -> list[dict]:
    return _admission_status()


@pytest.fixture(scope="session")
def sample_groups() -> list[dict]:
    return _groups()


@pytest.fixture(scope="session")
def sample_api_json(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write the admission-status payload to disk for tests that read from a file."""
    path = tmp_path_factory.mktemp("fixtures") / "admission_status.json"
    path.write_text(json.dumps(_admission_status()), encoding="utf-8")
    return path
