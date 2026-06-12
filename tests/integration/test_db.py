"""CRUD tests for the SQLModel repository layer.

All tests use a fresh in-memory SQLite database via the `session` fixture,
so they are fully isolated from each other and require no setup beyond import.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from sqlmodel import Session

from planer.adapters.db import (
    Availability,
    Curation,
    Group,
    Presentation,
    PresentationStatus,
    Student,
    build_domain_groups,
    create_presentation,
    create_round,
    create_slot,
    create_tables,
    email_already_sent,
    get_all_groups,
    get_availabilities_for_round,
    get_curations_for_round,
    get_member_attendance_for_round,
    get_member_attendance_for_slot,
    get_presentations_for_round,
    get_round,
    get_slots_for_round,
    get_students_in_groups,
    list_rounds,
    log_email,
    make_engine,
    rollup_status,
    set_round_quorum,
    update_presentation_status,
    upsert_availability,
    upsert_curation,
    upsert_groups,
    upsert_member_attendance,
    upsert_students,
)
from planer.domain.filtering import build_groups
from planer.domain.models import Group as DomainGroup
from planer.domain.models import Participant

_T0 = datetime(2024, 3, 18, 10, 0)


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = make_engine("sqlite:///:memory:")
    create_tables(engine)
    with Session(engine) as sess:
        yield sess


def _participant(
    uid: str = "u1",
    name: str = "Alice",
    email: str = "alice@uni.de",
    matr: str = "111111",
    group_id: str = "g1",
) -> Participant:
    return Participant(
        user_id=uid,
        display_name=name,
        email=email,
        matr_nr=matr,
        group_id=group_id,
        group_name="G1",
        has_admission=True,
        results=(),
    )


def _domain_group(gid: str = "g1", name: str = "Team A") -> DomainGroup:
    return DomainGroup(id=gid, name=name, members=())


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------


class TestStudents:
    def test_upsert_creates_student(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        s = session.get(Student, "u1")
        assert s is not None
        assert s.name == "Alice"
        assert s.email == "alice@uni.de"
        assert s.matr_nr == "111111"

    def test_upsert_updates_existing_student(self, session: Session) -> None:
        upsert_students(session, [_participant(name="Alice")])
        upsert_students(session, [_participant(name="Alice B.")])
        s = session.get(Student, "u1")
        assert s is not None
        assert s.name == "Alice B."

    def test_upsert_multiple_students(self, session: Session) -> None:
        upsert_students(session, [_participant("u1"), _participant("u2")])
        assert session.get(Student, "u1") is not None
        assert session.get(Student, "u2") is not None

    def test_group_id_stored(self, session: Session) -> None:
        upsert_students(session, [_participant(group_id="g42")])
        s = session.get(Student, "u1")
        assert s is not None
        assert s.group_id == "g42"

    def test_empty_group_id_resolved_to_user_id(self, session: Session) -> None:
        # A solo student (empty group_id) is stored under its own user_id so the
        # row matches the synthesised solo Group.id built by build_groups.
        upsert_students(session, [_participant(group_id="")])
        s = session.get(Student, "u1")
        assert s is not None
        assert s.group_id == "u1"


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


class TestGroups:
    def test_upsert_creates_group(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        g = session.get(Group, "g1")
        assert g is not None
        assert g.name == "Team A"

    def test_upsert_updates_group_name(self, session: Session) -> None:
        upsert_groups(session, [_domain_group(name="Old")])
        upsert_groups(session, [_domain_group(name="New")])
        g = session.get(Group, "g1")
        assert g is not None
        assert g.name == "New"

    def test_upsert_multiple_groups(self, session: Session) -> None:
        upsert_groups(session, [_domain_group("g1"), _domain_group("g2", "Team B")])
        assert session.get(Group, "g1") is not None
        assert session.get(Group, "g2") is not None

    def test_solo_student_round_trips_with_its_member(self, session: Session) -> None:
        # Regression: a solo student (empty group_id) must reappear as a member
        # of its own group after persistence, not as an empty group. build_groups
        # synthesises Group.id = user_id; upsert_students must store the same id.
        solo = _participant(uid="solo1", group_id="")
        groups = build_groups([solo])
        upsert_students(session, [solo])
        upsert_groups(session, groups)

        db_groups = get_all_groups(session)
        db_students = get_students_in_groups(session, [g.id for g in db_groups])
        domain_groups = build_domain_groups(db_groups, db_students)

        assert len(domain_groups) == 1
        assert domain_groups[0].id == "solo1"
        assert [m.user_id for m in domain_groups[0].members] == ["solo1"]

    def test_admission_assessment_fields_round_trip(self, session: Session) -> None:
        # Persisted admission/assessment flags + points must reconstruct so that
        # is_due() behaves the same after a reload as it did at import.
        from planer.domain.filtering import _ASSIGNMENT_TYPE, _RULE, is_due
        from planer.domain.models import Result

        def _hw(points: int) -> tuple[Result, ...]:
            return (Result(_RULE, _ASSIGNMENT_TYPE, achieved_points=points, passed=False),)

        due = Participant(
            user_id="due1",
            display_name="Due",
            email="due@uni.de",
            matr_nr="1",
            group_id="g1",
            group_name="G1",
            has_admission=False,
            results=_hw(1),
        )
        alt = Participant(
            user_id="alt1",
            display_name="Alt",
            email="alt@uni.de",
            matr_nr="2",
            group_id="g2",
            group_name="G2",
            has_admission=True,
            has_admission_from_previous_semester=True,
            results=_hw(0),
        )
        assessed = Participant(
            user_id="ass1",
            display_name="Assessed",
            email="ass@uni.de",
            matr_nr="3",
            group_id="g3",
            group_name="G3",
            has_admission=True,
            has_assessment=True,
            results=_hw(0),
        )
        upsert_students(session, [due, alt, assessed])
        upsert_groups(session, build_groups([due, alt, assessed]))

        db_groups = get_all_groups(session)
        db_students = get_students_in_groups(session, [g.id for g in db_groups])
        by_uid = {
            m.user_id: m for g in build_domain_groups(db_groups, db_students) for m in g.members
        }

        assert is_due(by_uid["due1"], threshold=3) is True
        assert is_due(by_uid["alt1"], threshold=3) is False  # Altzulassung
        assert is_due(by_uid["ass1"], threshold=3) is False  # already assessed
        assert by_uid["due1"].results[0].achieved_points == 1
        assert by_uid["alt1"].has_admission_from_previous_semester is True
        assert by_uid["ass1"].has_assessment is True


# ---------------------------------------------------------------------------
# Planning Rounds
# ---------------------------------------------------------------------------


class TestPlanningRounds:
    def test_create_round_assigns_id(self, session: Session) -> None:
        rnd = create_round(session, label="Week 1")
        assert rnd.id is not None
        assert rnd.label == "Week 1"

    def test_create_round_default_label(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.label == ""

    def test_created_at_is_set(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.created_at is not None

    def test_get_round(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.id is not None
        fetched = get_round(session, rnd.id)
        assert fetched is not None
        assert fetched.id == rnd.id

    def test_get_nonexistent_round_returns_none(self, session: Session) -> None:
        assert get_round(session, 9999) is None

    def test_list_rounds(self, session: Session) -> None:
        create_round(session, "R1")
        create_round(session, "R2")
        assert len(list_rounds(session)) == 2

    def test_list_rounds_empty(self, session: Session) -> None:
        assert list_rounds(session) == []

    def test_quorum_defaults_to_all(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.quorum == "all"

    def test_set_round_quorum(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.id is not None
        set_round_quorum(session, rnd.id, "any")
        updated = get_round(session, rnd.id)
        assert updated is not None
        assert updated.quorum == "any"


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


class TestSlots:
    def test_create_slot_assigns_id(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0, room="A101")
        assert slot.id is not None
        assert slot.room == "A101"
        assert slot.capacity == 5

    def test_create_slot_custom_capacity(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0, capacity=3)
        assert slot.capacity == 3

    def test_get_slots_for_round(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.id is not None
        create_slot(session, rnd.id, _T0)
        create_slot(session, rnd.id, _T0)
        assert len(get_slots_for_round(session, rnd.id)) == 2

    def test_slots_from_other_round_not_returned(self, session: Session) -> None:
        r1 = create_round(session)
        r2 = create_round(session)
        assert r1.id is not None
        assert r2.id is not None
        create_slot(session, r1.id, _T0)
        assert get_slots_for_round(session, r2.id) == []

    def test_get_slots_empty_round(self, session: Session) -> None:
        rnd = create_round(session)
        assert rnd.id is not None
        assert get_slots_for_round(session, rnd.id) == []


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestAvailability:
    def test_upsert_creates_availability(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        upsert_availability(session, "u1", slot.id, available=True)
        avail = session.get(Availability, {"student_id": "u1", "slot_id": slot.id})
        assert avail is not None
        assert avail.available is True

    def test_upsert_overwrites_on_resubmit(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        upsert_availability(session, "u1", slot.id, available=True)
        upsert_availability(session, "u1", slot.id, available=False)
        avail = session.get(Availability, {"student_id": "u1", "slot_id": slot.id})
        assert avail is not None
        assert avail.available is False

    def test_submitted_at_updated_on_resubmit(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        upsert_availability(session, "u1", slot.id, available=True)
        first_ts = session.get(Availability, {"student_id": "u1", "slot_id": slot.id})
        assert first_ts is not None
        first_submitted = first_ts.submitted_at
        upsert_availability(session, "u1", slot.id, available=False)
        second_ts = session.get(Availability, {"student_id": "u1", "slot_id": slot.id})
        assert second_ts is not None
        assert second_ts.submitted_at >= first_submitted

    def test_get_availabilities_for_round(self, session: Session) -> None:
        upsert_students(session, [_participant("u1"), _participant("u2")])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        upsert_availability(session, "u1", slot.id, available=True)
        upsert_availability(session, "u2", slot.id, available=False)
        avails = get_availabilities_for_round(session, rnd.id)
        assert len(avails) == 2

    def test_availability_from_other_round_not_returned(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        r1 = create_round(session)
        r2 = create_round(session)
        assert r1.id is not None
        assert r2.id is not None
        slot = create_slot(session, r1.id, _T0)
        assert slot.id is not None
        upsert_availability(session, "u1", slot.id, available=True)
        assert get_availabilities_for_round(session, r2.id) == []


# ---------------------------------------------------------------------------
# Curation
# ---------------------------------------------------------------------------


class TestCuration:
    def test_default_included_true(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        rnd = create_round(session)
        assert rnd.id is not None
        upsert_curation(session, "g1", rnd.id)
        c = session.get(Curation, {"group_id": "g1", "round_id": rnd.id})
        assert c is not None
        assert c.included is True

    def test_exclude_group(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        rnd = create_round(session)
        assert rnd.id is not None
        upsert_curation(session, "g1", rnd.id, included=False)
        c = session.get(Curation, {"group_id": "g1", "round_id": rnd.id})
        assert c is not None
        assert c.included is False

    def test_pin_to_slot(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        upsert_curation(session, "g1", rnd.id, pinned_slot_id=slot.id)
        c = session.get(Curation, {"group_id": "g1", "round_id": rnd.id})
        assert c is not None
        assert c.pinned_slot_id == slot.id

    def test_upsert_curation_updates_existing(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        rnd = create_round(session)
        assert rnd.id is not None
        upsert_curation(session, "g1", rnd.id, included=True)
        upsert_curation(session, "g1", rnd.id, included=False)
        c = session.get(Curation, {"group_id": "g1", "round_id": rnd.id})
        assert c is not None
        assert c.included is False

    def test_get_curations_for_round(self, session: Session) -> None:
        upsert_groups(session, [_domain_group("g1"), _domain_group("g2", "Team B")])
        rnd = create_round(session)
        assert rnd.id is not None
        upsert_curation(session, "g1", rnd.id)
        upsert_curation(session, "g2", rnd.id)
        assert len(get_curations_for_round(session, rnd.id)) == 2

    def test_curations_from_other_round_not_returned(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        r1 = create_round(session)
        r2 = create_round(session)
        assert r1.id is not None
        assert r2.id is not None
        upsert_curation(session, "g1", r1.id)
        assert get_curations_for_round(session, r2.id) == []


# ---------------------------------------------------------------------------
# Presentations
# ---------------------------------------------------------------------------


class TestPresentations:
    def test_create_presentation_scheduled(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        p = create_presentation(session, "g1", slot.id, rnd.id)
        assert p.status == PresentationStatus.SCHEDULED
        assert p.marked_at is None

    def test_update_to_presented(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)
        update_presentation_status(session, "g1", slot.id, PresentationStatus.PRESENTED)
        p = session.get(Presentation, {"group_id": "g1", "slot_id": slot.id})
        assert p is not None
        assert p.status == PresentationStatus.PRESENTED
        assert p.marked_at is not None

    def test_update_to_no_show(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)
        update_presentation_status(session, "g1", slot.id, PresentationStatus.NO_SHOW)
        p = session.get(Presentation, {"group_id": "g1", "slot_id": slot.id})
        assert p is not None
        assert p.status == PresentationStatus.NO_SHOW

    def test_update_nonexistent_raises_value_error(self, session: Session) -> None:
        with pytest.raises(ValueError, match="No presentation"):
            update_presentation_status(session, "gX", 999, PresentationStatus.PRESENTED)

    def test_get_presentations_for_round(self, session: Session) -> None:
        upsert_groups(session, [_domain_group("g1"), _domain_group("g2", "B")])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)
        create_presentation(session, "g2", slot.id, rnd.id)
        assert len(get_presentations_for_round(session, rnd.id)) == 2

    def test_presentations_from_other_round_not_returned(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        r1 = create_round(session)
        r2 = create_round(session)
        assert r1.id is not None
        assert r2.id is not None
        slot = create_slot(session, r1.id, _T0)
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, r1.id)
        assert get_presentations_for_round(session, r2.id) == []


class TestRollupStatus:
    def test_any_presented_wins(self) -> None:
        assert (
            rollup_status([PresentationStatus.PRESENTED, PresentationStatus.NO_SHOW])
            == PresentationStatus.PRESENTED
        )

    def test_all_no_show(self) -> None:
        assert (
            rollup_status([PresentationStatus.NO_SHOW, PresentationStatus.NO_SHOW])
            == PresentationStatus.NO_SHOW
        )

    def test_any_scheduled_stays_scheduled(self) -> None:
        assert (
            rollup_status([PresentationStatus.NO_SHOW, PresentationStatus.SCHEDULED])
            == PresentationStatus.SCHEDULED
        )

    def test_empty_is_scheduled(self) -> None:
        assert rollup_status([]) == PresentationStatus.SCHEDULED


class TestMemberAttendance:
    def test_upsert_and_get_for_slot(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        upsert_students(session, [_participant("u1"), _participant("u2")])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)
        upsert_member_attendance(session, "u1", slot.id, "g1", rnd.id, PresentationStatus.PRESENTED)
        upsert_member_attendance(session, "u2", slot.id, "g1", rnd.id, PresentationStatus.NO_SHOW)

        rows = {
            a.student_id: a.status for a in get_member_attendance_for_slot(session, rnd.id, slot.id)
        }
        assert rows == {
            "u1": PresentationStatus.PRESENTED,
            "u2": PresentationStatus.NO_SHOW,
        }

    def test_upsert_overwrites(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        upsert_students(session, [_participant("u1")])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        upsert_member_attendance(session, "u1", slot.id, "g1", rnd.id, PresentationStatus.NO_SHOW)
        upsert_member_attendance(session, "u1", slot.id, "g1", rnd.id, PresentationStatus.PRESENTED)

        rows = get_member_attendance_for_round(session, rnd.id)
        assert len(rows) == 1
        assert rows[0].status == PresentationStatus.PRESENTED
        assert rows[0].marked_at is not None

    def test_other_round_not_returned(self, session: Session) -> None:
        upsert_groups(session, [_domain_group()])
        upsert_students(session, [_participant("u1")])
        r1 = create_round(session)
        r2 = create_round(session)
        assert r1.id is not None and r2.id is not None
        slot = create_slot(session, r1.id, _T0)
        assert slot.id is not None
        upsert_member_attendance(session, "u1", slot.id, "g1", r1.id, PresentationStatus.PRESENTED)
        assert get_member_attendance_for_round(session, r2.id) == []


# ---------------------------------------------------------------------------
# Email Log
# ---------------------------------------------------------------------------


class TestEmailLog:
    def test_not_sent_initially(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        rnd = create_round(session)
        assert rnd.id is not None
        assert email_already_sent(session, "u1", "availability_request", rnd.id) is False

    def test_sent_after_log(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        rnd = create_round(session)
        assert rnd.id is not None
        log_email(session, "u1", "availability_request", rnd.id)
        assert email_already_sent(session, "u1", "availability_request", rnd.id) is True

    def test_error_log_does_not_count_as_sent(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        rnd = create_round(session)
        assert rnd.id is not None
        log_email(session, "u1", "availability_request", rnd.id, error="SMTP timeout")
        assert email_already_sent(session, "u1", "availability_request", rnd.id) is False

    def test_different_kind_not_counted_as_sent(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        rnd = create_round(session)
        assert rnd.id is not None
        log_email(session, "u1", "availability_request", rnd.id)
        assert email_already_sent(session, "u1", "slot_assignment", rnd.id) is False

    def test_different_round_not_counted_as_sent(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        r1 = create_round(session)
        r2 = create_round(session)
        assert r1.id is not None
        assert r2.id is not None
        log_email(session, "u1", "availability_request", r1.id)
        assert email_already_sent(session, "u1", "availability_request", r2.id) is False

    def test_success_after_error_counts_as_sent(self, session: Session) -> None:
        upsert_students(session, [_participant()])
        rnd = create_round(session)
        assert rnd.id is not None
        log_email(session, "u1", "availability_request", rnd.id, error="SMTP timeout")
        log_email(session, "u1", "availability_request", rnd.id)
        assert email_already_sent(session, "u1", "availability_request", rnd.id) is True
