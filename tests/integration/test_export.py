"""Export tests: row gathering + CSV serialization."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import Session

from planer.adapters.db import (
    PresentationStatus,
    create_presentation,
    create_round,
    create_slot,
    create_tables,
    make_engine,
    update_presentation_status,
    upsert_groups,
    upsert_students,
)
from planer.domain.models import Group as DomainGroup
from planer.domain.models import Participant
from planer.export import build_export_rows, rows_to_csv

_T0 = datetime(2024, 3, 18, 10, 0)


def _participant(uid: str, group_id: str = "g1") -> Participant:
    return Participant(
        user_id=uid,
        display_name=f"Student {uid}",
        email=f"{uid}@uni.de",
        matr_nr=f"mat-{uid}",
        group_id=group_id,
        group_name="G1",
        has_admission=True,
        results=(),
    )


@pytest.fixture
def session():  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite:///:memory:")
    create_tables(engine)
    with Session(engine) as sess:
        yield sess


class TestBuildExportRows:
    def test_one_row_per_member(self, session: Session) -> None:
        upsert_students(session, [_participant("u1"), _participant("u2")])
        upsert_groups(session, [DomainGroup(id="g1", name="Gruppe Eins", members=())])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0, room="A101")
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)

        rows = build_export_rows(session, rnd.id)

        assert len(rows) == 2
        assert {r["member_name"] for r in rows} == {"Student u1", "Student u2"}
        assert all(r["group_name"] == "Gruppe Eins" for r in rows)
        assert all(r["room"] == "A101" for r in rows)
        assert all(r["status"] == "SCHEDULED" for r in rows)

    def test_status_reflected(self, session: Session) -> None:
        upsert_students(session, [_participant("u1")])
        upsert_groups(session, [DomainGroup(id="g1", name="G1", members=())])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)
        update_presentation_status(session, "g1", slot.id, PresentationStatus.PRESENTED)

        rows = build_export_rows(session, rnd.id)
        assert rows[0]["status"] == "PRESENTED"
        assert rows[0]["marked_at"] != ""

    def test_empty_round_yields_no_rows(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.id is not None
        assert build_export_rows(session, rnd.id) == []


class TestRowsToCsv:
    def test_header_present_when_empty(self) -> None:
        csv_text = rows_to_csv([])
        assert csv_text.splitlines()[0].startswith("round_id,group_id,group_name")

    def test_roundtrip(self, session: Session) -> None:
        upsert_students(session, [_participant("u1")])
        upsert_groups(session, [DomainGroup(id="g1", name="G1", members=())])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0, room="R9")
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)

        csv_text = rows_to_csv(build_export_rows(session, rnd.id))
        lines = csv_text.splitlines()
        assert len(lines) == 2  # header + 1 row
        assert "Student u1" in lines[1]
        assert "R9" in lines[1]
