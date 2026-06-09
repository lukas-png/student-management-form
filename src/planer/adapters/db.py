from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select

from planer.domain.models import Group as DomainGroup
from planer.domain.models import Participant, resolve_group_id


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PresentationStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    PRESENTED = "PRESENTED"
    NO_SHOW = "NO_SHOW"


# ---------------------------------------------------------------------------
# SQLModel table definitions
# ---------------------------------------------------------------------------


class Student(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    email: str
    matr_nr: str
    group_id: str = ""


class Group(SQLModel, table=True):
    __tablename__ = "groups"

    id: str = Field(primary_key=True)
    name: str


class PlanningRound(SQLModel, table=True):
    __tablename__ = "planning_round"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    label: str = ""
    # "availability" = ask students which slots they can do (default);
    # "confirm" = admin fixes a slot per group, students only confirm yes/no.
    mode: str = "availability"


class Slot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    round_id: int = Field(foreign_key="planning_round.id")
    starts_at: datetime
    room: str = ""
    capacity: int = 5


class Availability(SQLModel, table=True):
    student_id: str = Field(foreign_key="student.id", primary_key=True)
    slot_id: int = Field(foreign_key="slot.id", primary_key=True)
    available: bool
    submitted_at: datetime = Field(default_factory=_now)


class Presentation(SQLModel, table=True):
    group_id: str = Field(foreign_key="groups.id", primary_key=True)
    slot_id: int = Field(foreign_key="slot.id", primary_key=True)
    round_id: int = Field(foreign_key="planning_round.id")
    status: PresentationStatus = PresentationStatus.SCHEDULED
    marked_at: datetime | None = None


class Curation(SQLModel, table=True):
    group_id: str = Field(foreign_key="groups.id", primary_key=True)
    round_id: int = Field(foreign_key="planning_round.id", primary_key=True)
    included: bool = True
    pinned_slot_id: int | None = Field(default=None, foreign_key="slot.id")
    # Schedule the pinned slot even if a member declined it (admin emergency override).
    override: bool = False


class EmailLog(SQLModel, table=True):
    __tablename__ = "email_log"

    id: int | None = Field(default=None, primary_key=True)
    student_id: str = Field(foreign_key="student.id")
    kind: str
    round_id: int = Field(foreign_key="planning_round.id")
    sent_at: datetime = Field(default_factory=_now)
    error: str | None = None


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------


def make_engine(url: str, echo: bool = False) -> Engine:
    engine = create_engine(url, echo=echo)

    @event.listens_for(engine, "connect")
    def _set_fk_pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        if hasattr(dbapi_conn, "execute"):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def create_tables(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_columns(engine)


def _ensure_columns(engine: Engine) -> None:
    """Lightweight SQLite migration: add columns that create_all can't add to
    pre-existing tables. Idempotent — skips columns that already exist."""
    with engine.begin() as conn:
        round_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(planning_round)")}
        if "mode" not in round_cols:
            conn.exec_driver_sql(
                "ALTER TABLE planning_round ADD COLUMN mode VARCHAR NOT NULL DEFAULT 'availability'"
            )
        cur_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(curation)")}
        if "override" not in cur_cols:
            conn.exec_driver_sql(
                "ALTER TABLE curation ADD COLUMN override BOOLEAN NOT NULL DEFAULT 0"
            )


# ---------------------------------------------------------------------------
# Repository: Students & Groups
# ---------------------------------------------------------------------------


def upsert_students(session: Session, participants: list[Participant]) -> None:
    for p in participants:
        session.merge(
            Student(
                id=p.user_id,
                name=p.display_name,
                email=p.email,
                matr_nr=p.matr_nr,
                group_id=resolve_group_id(p),
            )
        )
    session.commit()


def upsert_groups(session: Session, groups: list[DomainGroup]) -> None:
    for g in groups:
        session.merge(Group(id=g.id, name=g.name))
    session.commit()


# ---------------------------------------------------------------------------
# Repository: Planning Rounds
# ---------------------------------------------------------------------------


def create_round(session: Session, label: str = "") -> PlanningRound:
    rnd = PlanningRound(label=label)
    session.add(rnd)
    session.commit()
    session.refresh(rnd)
    return rnd


def get_round(session: Session, round_id: int) -> PlanningRound | None:
    return session.get(PlanningRound, round_id)


def set_round_mode(session: Session, round_id: int, mode: str) -> None:
    rnd = session.get(PlanningRound, round_id)
    if rnd is not None:
        rnd.mode = mode
        session.add(rnd)
        session.commit()


def list_rounds(session: Session) -> list[PlanningRound]:
    return list(session.exec(select(PlanningRound)).all())


# ---------------------------------------------------------------------------
# Repository: Slots
# ---------------------------------------------------------------------------


def create_slot(
    session: Session,
    round_id: int,
    starts_at: datetime,
    room: str = "",
    capacity: int = 5,
) -> Slot:
    slot = Slot(round_id=round_id, starts_at=starts_at, room=room, capacity=capacity)
    session.add(slot)
    session.commit()
    session.refresh(slot)
    return slot


def get_slots_for_round(session: Session, round_id: int) -> list[Slot]:
    return list(session.exec(select(Slot).where(Slot.round_id == round_id)).all())


# ---------------------------------------------------------------------------
# Repository: Availability
# ---------------------------------------------------------------------------


def upsert_availability(session: Session, student_id: str, slot_id: int, available: bool) -> None:
    stmt = select(Availability).where(
        Availability.student_id == student_id,
        Availability.slot_id == slot_id,
    )
    existing = session.exec(stmt).first()
    if existing:
        existing.available = available
        existing.submitted_at = _now()
        session.add(existing)
    else:
        session.add(Availability(student_id=student_id, slot_id=slot_id, available=available))
    session.commit()


def get_availabilities_for_round(session: Session, round_id: int) -> list[Availability]:
    stmt = (
        select(Availability)
        .join(Slot, Slot.id == Availability.slot_id)  # type: ignore[arg-type]
        .where(Slot.round_id == round_id)
    )
    return list(session.exec(stmt).all())


# ---------------------------------------------------------------------------
# Repository: Curation
# ---------------------------------------------------------------------------


def upsert_curation(
    session: Session,
    group_id: str,
    round_id: int,
    *,
    included: bool = True,
    pinned_slot_id: int | None = None,
    override: bool = False,
) -> None:
    stmt = select(Curation).where(
        Curation.group_id == group_id,
        Curation.round_id == round_id,
    )
    existing = session.exec(stmt).first()
    if existing:
        existing.included = included
        existing.pinned_slot_id = pinned_slot_id
        existing.override = override
        session.add(existing)
    else:
        session.add(
            Curation(
                group_id=group_id,
                round_id=round_id,
                included=included,
                pinned_slot_id=pinned_slot_id,
                override=override,
            )
        )
    session.commit()


def get_curations_for_round(session: Session, round_id: int) -> list[Curation]:
    return list(session.exec(select(Curation).where(Curation.round_id == round_id)).all())


def get_curation(session: Session, round_id: int, group_id: str) -> Curation | None:
    stmt = select(Curation).where(Curation.round_id == round_id, Curation.group_id == group_id)
    return session.exec(stmt).first()


def set_all_curations_included(
    session: Session, round_id: int, group_ids: list[str], *, included: bool
) -> None:
    """Bulk include/exclude every given group for the round, preserving any pins."""
    existing = {c.group_id: c for c in get_curations_for_round(session, round_id)}
    for gid in group_ids:
        cur = existing.get(gid)
        if cur is not None:
            cur.included = included
            session.add(cur)
        else:
            session.add(Curation(group_id=gid, round_id=round_id, included=included))
    session.commit()


# ---------------------------------------------------------------------------
# Repository: Presentations
# ---------------------------------------------------------------------------


def create_presentation(
    session: Session, group_id: str, slot_id: int, round_id: int
) -> Presentation:
    p = Presentation(group_id=group_id, slot_id=slot_id, round_id=round_id)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def update_presentation_status(
    session: Session,
    group_id: str,
    slot_id: int,
    status: PresentationStatus,
) -> None:
    stmt = select(Presentation).where(
        Presentation.group_id == group_id,
        Presentation.slot_id == slot_id,
    )
    p = session.exec(stmt).first()
    if p is None:
        raise ValueError(f"No presentation for group {group_id!r} in slot {slot_id}")
    p.status = status
    p.marked_at = _now()
    session.add(p)
    session.commit()


def get_presentations_for_round(session: Session, round_id: int) -> list[Presentation]:
    return list(session.exec(select(Presentation).where(Presentation.round_id == round_id)).all())


def get_presentations_for_slot(session: Session, round_id: int, slot_id: int) -> list[Presentation]:
    stmt = select(Presentation).where(
        Presentation.round_id == round_id,
        Presentation.slot_id == slot_id,
    )
    return list(session.exec(stmt).all())


def get_presentations_before_round(session: Session, round_id: int) -> list[Presentation]:
    """All presentations from rounds strictly earlier than ``round_id`` (for carry-over)."""
    return list(
        session.exec(select(Presentation).where(Presentation.round_id < round_id)).all()  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Repository: Email Log
# ---------------------------------------------------------------------------


def log_email(
    session: Session,
    student_id: str,
    kind: str,
    round_id: int,
    *,
    error: str | None = None,
) -> None:
    session.add(EmailLog(student_id=student_id, kind=kind, round_id=round_id, error=error))
    session.commit()


def email_already_sent(session: Session, student_id: str, kind: str, round_id: int) -> bool:
    stmt = select(EmailLog).where(
        EmailLog.student_id == student_id,
        EmailLog.kind == kind,
        EmailLog.round_id == round_id,
        EmailLog.error.is_(None),  # type: ignore[union-attr]
    )
    return session.exec(stmt).first() is not None


# ---------------------------------------------------------------------------
# Repository: Admin helpers
# ---------------------------------------------------------------------------


def get_all_groups(session: Session) -> list[Group]:
    return list(session.exec(select(Group)).all())


def get_all_students(session: Session) -> list[Student]:
    return list(session.exec(select(Student)).all())


def get_students_in_groups(session: Session, group_ids: list[str]) -> list[Student]:
    if not group_ids:
        return []
    return list(session.exec(select(Student).where(Student.group_id.in_(group_ids))).all())  # type: ignore[arg-type]


def build_domain_groups(db_groups: list[Group], db_students: list[Student]) -> list[DomainGroup]:
    from collections import defaultdict

    members_by_group: defaultdict[str, list[Participant]] = defaultdict(list)
    for s in db_students:
        members_by_group[s.group_id].append(
            Participant(
                user_id=s.id,
                display_name=s.name,
                email=s.email,
                matr_nr=s.matr_nr,
                group_id=s.group_id,
                group_name="",
                has_admission=False,
                results=(),
            )
        )
    return [
        DomainGroup(id=g.id, name=g.name, members=tuple(members_by_group.get(g.id, [])))
        for g in db_groups
    ]


def delete_presentations_for_round(session: Session, round_id: int) -> None:
    for p in session.exec(select(Presentation).where(Presentation.round_id == round_id)).all():
        session.delete(p)
    session.commit()


def delete_round(session: Session, round_id: int) -> bool:
    """Delete a planning round and everything scoped to it (data minimization).

    Removes email logs, presentations, curations, availabilities, slots and the
    round itself, in FK-safe order. Shared Students and Groups are kept — they
    belong to no single round. Returns False if the round does not exist.
    """
    rnd = session.get(PlanningRound, round_id)
    if rnd is None:
        return False

    slot_ids = [s.id for s in get_slots_for_round(session, round_id)]

    # Delete children before parents and flush between stages: no ORM relationships
    # are declared, so SQLAlchemy won't order the DELETEs itself, and SQLite's
    # PRAGMA foreign_keys=ON enforces the constraints on every statement.
    for log in session.exec(select(EmailLog).where(EmailLog.round_id == round_id)).all():
        session.delete(log)
    for pres in session.exec(select(Presentation).where(Presentation.round_id == round_id)).all():
        session.delete(pres)
    for cur in session.exec(select(Curation).where(Curation.round_id == round_id)).all():
        session.delete(cur)
    if slot_ids:
        avails = session.exec(
            select(Availability).where(Availability.slot_id.in_(slot_ids))  # type: ignore[attr-defined]
        ).all()
        for avail in avails:
            session.delete(avail)
    session.flush()

    for slot in session.exec(select(Slot).where(Slot.round_id == round_id)).all():
        session.delete(slot)
    session.flush()

    session.delete(rnd)
    session.commit()
    return True
