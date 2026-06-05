"""Round purge tests: cli purge --round wipes a round completely."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import Session, select

from planer.adapters.db import (
    Availability,
    Curation,
    EmailLog,
    PlanningRound,
    Presentation,
    Slot,
    create_presentation,
    create_round,
    create_slot,
    create_tables,
    delete_round,
    log_email,
    make_engine,
    upsert_availability,
    upsert_curation,
    upsert_groups,
    upsert_students,
)
from planer.domain.models import Group as DomainGroup
from planer.domain.models import Participant

_T0 = datetime(2024, 3, 18, 10, 0)


def _participant(uid: str = "u1", group_id: str = "g1") -> Participant:
    return Participant(
        user_id=uid,
        display_name="Alice",
        email="alice@uni.de",
        matr_nr="111111",
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


def _seed_full_round(session: Session) -> int:
    upsert_students(session, [_participant()])
    upsert_groups(session, [DomainGroup(id="g1", name="G1", members=())])
    rnd = create_round(session)
    assert rnd.id is not None
    slot = create_slot(session, rnd.id, _T0)
    assert slot.id is not None
    upsert_availability(session, "u1", slot.id, available=True)
    upsert_curation(session, "g1", rnd.id, included=True)
    create_presentation(session, "g1", slot.id, rnd.id)
    log_email(session, "u1", "availability_request", rnd.id)
    return rnd.id


class TestDeleteRound:
    def test_deletes_all_scoped_rows(self, session: Session) -> None:
        round_id = _seed_full_round(session)

        assert delete_round(session, round_id) is True

        assert session.exec(select(PlanningRound)).all() == []
        assert session.exec(select(Slot)).all() == []
        assert session.exec(select(Availability)).all() == []
        assert session.exec(select(Curation)).all() == []
        assert session.exec(select(Presentation)).all() == []
        assert session.exec(select(EmailLog)).all() == []

    def test_keeps_shared_students_and_groups(self, session: Session) -> None:
        from planer.adapters.db import Group, Student

        round_id = _seed_full_round(session)
        delete_round(session, round_id)

        assert len(session.exec(select(Student)).all()) == 1
        assert len(session.exec(select(Group)).all()) == 1

    def test_only_target_round_affected(self, session: Session) -> None:
        r1 = _seed_full_round(session)
        # second round with its own slot
        r2 = create_round(session)
        assert r2.id is not None
        create_slot(session, r2.id, _T0)

        delete_round(session, r1)

        remaining = session.exec(select(PlanningRound)).all()
        assert [r.id for r in remaining] == [r2.id]
        assert len(session.exec(select(Slot)).all()) == 1

    def test_missing_round_returns_false(self, session: Session) -> None:
        assert delete_round(session, 999) is False
