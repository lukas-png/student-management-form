"""Contract test: Excel and API ingestion paths must produce identical Participant objects.

Both adapters parse different encodings of the same logical data:
  - Excel: WAHR/FALSCH string booleans, achievedPoints as int or string
  - API:   native JSON booleans (true/false), achievedPoints as int

Despite the encoding difference, both paths must return structurally
equal Participant objects when given the same source data.
"""

from pathlib import Path

from planer.adapters.ingest_api import to_participants
from planer.adapters.ingest_excel import parse_excel
from planer.domain.models import Participant


def _sorted(participants: list[Participant]) -> list[Participant]:
    return sorted(participants, key=lambda p: p.user_id)


def test_excel_and_api_produce_identical_participants(
    sample_excel: Path,
    sample_admission_status: list[dict],
    sample_groups: list[dict],
) -> None:
    from_excel = _sorted(parse_excel(sample_excel))
    from_api = _sorted(to_participants(sample_admission_status, sample_groups))

    assert len(from_excel) == len(from_api), (
        f"Participant count differs: Excel={len(from_excel)}, API={len(from_api)}"
    )
    for excel_p, api_p in zip(from_excel, from_api, strict=True):
        assert excel_p == api_p, (
            f"Mismatch for user {excel_p.user_id}:\n  Excel: {excel_p}\n  API:   {api_p}"
        )


def test_group_membership_identical(
    sample_excel: Path,
    sample_admission_status: list[dict],
    sample_groups: list[dict],
) -> None:
    """Group IDs and names are preserved identically through both paths."""
    from_excel = {p.user_id: (p.group_id, p.group_name) for p in parse_excel(sample_excel)}
    from_api = {
        p.user_id: (p.group_id, p.group_name)
        for p in to_participants(sample_admission_status, sample_groups)
    }
    assert from_excel == from_api


def test_result_tuples_identical(
    sample_excel: Path,
    sample_admission_status: list[dict],
    sample_groups: list[dict],
) -> None:
    """Result tuples (rule, type, points, passed) are identical through both paths."""
    from_excel = {p.user_id: p.results for p in parse_excel(sample_excel)}
    from_api = {
        p.user_id: p.results for p in to_participants(sample_admission_status, sample_groups)
    }
    assert from_excel == from_api
