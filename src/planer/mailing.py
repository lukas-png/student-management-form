"""Mail orchestration with idempotency.

Recipient selection + EmailLog idempotency live here; the actual SMTP transport
is injected as a `MailSender` so this is fully unit-testable with a fake.

Idempotency rule: before each send, if a successful EmailLog row
for (student_id, kind, round_id) already exists, skip — unless `force=True`
(the explicit admin "resend" action). Each send is isolated in try/except; one
failure logs to EmailLog.error and never blocks the other recipients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session

from planer.adapters.db import (
    Student,
    email_already_sent,
    get_all_groups,
    get_all_students,
    get_availabilities_for_round,
    get_curations_for_round,
    get_presentations_for_round,
    get_round,
    get_slots_for_round,
    get_students_in_groups,
    log_email,
)
from planer.adapters.mail import MailSender
from planer.logging_setup import get_logger
from planer.security import sign_availability_token

logger = get_logger("mailing")

KIND_AVAILABILITY = "availability_request"
KIND_REMINDER = "reminder"
KIND_ASSIGNMENT = "slot_assignment"

_MAIL_DIR = Path(__file__).parent / "web" / "templates" / "mail"
_env = Environment(
    loader=FileSystemLoader(_MAIL_DIR),
    autoescape=select_autoescape(enabled_extensions=()),
    keep_trailing_newline=True,
)


@dataclass
class SendReport:
    sent: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already sent (idempotency)
    excluded: list[str] = field(default_factory=list)  # group deselected by admin
    failed: list[tuple[str, str]] = field(default_factory=list)  # (student_id, error)


def render_mail(kind: str, context: dict[str, object]) -> tuple[str, str]:
    """Render a mail template to (subject, body).

    Convention: the first line of the template is `Subject: ...`; the remainder
    (after the newline) is the plain-text body.
    """
    rendered = _env.get_template(f"{kind}.txt").render(**context)
    first, _, body = rendered.partition("\n")
    subject = first.removeprefix("Subject:").strip()
    return subject, body.lstrip("\n")


def _availability_link(base_url: str, student_id: str, round_id: int, secret_key: str) -> str:
    token = sign_availability_token(student_id, round_id, secret_key=secret_key)
    return f"{base_url.rstrip('/')}/availability/{token}"


def _excluded_group_ids(session: Session, round_id: int) -> set[str]:
    """Group ids the admin has explicitly excluded (curation included=False)."""
    return {c.group_id for c in get_curations_for_round(session, round_id) if not c.included}


def _is_excluded(student: Student, excluded: set[str]) -> bool:
    # Solo students group under their own id (empty group_id fallback).
    return (student.group_id or student.id) in excluded


def _log_summary(kind: str, round_id: int, report: SendReport) -> SendReport:
    logger.info(
        "mail batch complete",
        extra={
            "kind": kind,
            "round_id": round_id,
            "sent": len(report.sent),
            "skipped": len(report.skipped),
            "excluded": len(report.excluded),
            "failed": len(report.failed),
        },
    )
    return report


def _deliver(
    session: Session,
    sender: MailSender,
    *,
    student_id: str,
    to: str,
    kind: str,
    round_id: int,
    subject: str,
    body: str,
    report: SendReport,
) -> None:
    try:
        sender.send(to=to, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001 — isolate per-recipient failures
        # Persist only the exception type — error strings may contain the
        # recipient address (PII). The full message goes to the in-memory
        # report for the operator's console, never to the DB log.
        log_email(session, student_id, kind, round_id, error=type(exc).__name__)
        report.failed.append((student_id, str(exc)))
        logger.warning(
            "mail send failed",
            extra={
                "student_id": student_id,
                "kind": kind,
                "round_id": round_id,
                "error": type(exc).__name__,
            },
        )
    else:
        log_email(session, student_id, kind, round_id)
        report.sent.append(student_id)
        logger.debug(
            "mail sent", extra={"student_id": student_id, "kind": kind, "round_id": round_id}
        )


def send_availability_requests(
    session: Session,
    sender: MailSender,
    round_id: int,
    *,
    base_url: str,
    secret_key: str,
    force: bool = False,
) -> SendReport:
    """Send the magic-link availability form to every student in an included group."""
    report = SendReport()
    excluded = _excluded_group_ids(session, round_id)
    rnd = get_round(session, round_id)
    round_label = rnd.label if rnd else ""
    for student in get_all_students(session):
        if _is_excluded(student, excluded):
            report.excluded.append(student.id)  # group deselected by the admin → no mail
            continue
        if not force and email_already_sent(session, student.id, KIND_AVAILABILITY, round_id):
            report.skipped.append(student.id)
            continue
        link = _availability_link(base_url, student.id, round_id, secret_key)
        subject, body = render_mail(
            KIND_AVAILABILITY,
            {"name": student.name, "link": link, "round_label": round_label},
        )
        _deliver(
            session,
            sender,
            student_id=student.id,
            to=student.email,
            kind=KIND_AVAILABILITY,
            round_id=round_id,
            subject=subject,
            body=body,
            report=report,
        )
    return _log_summary(KIND_AVAILABILITY, round_id, report)


def send_reminders(
    session: Session,
    sender: MailSender,
    round_id: int,
    *,
    base_url: str,
    secret_key: str,
    force: bool = False,
) -> SendReport:
    """Remind students in an included group who have not submitted availability yet."""
    report = SendReport()
    excluded = _excluded_group_ids(session, round_id)
    responded = {a.student_id for a in get_availabilities_for_round(session, round_id)}
    rnd = get_round(session, round_id)
    round_label = rnd.label if rnd else ""
    for student in get_all_students(session):
        if _is_excluded(student, excluded):
            report.excluded.append(student.id)  # group deselected by the admin → no mail
            continue
        if student.id in responded:
            continue
        if not force and email_already_sent(session, student.id, KIND_REMINDER, round_id):
            report.skipped.append(student.id)
            continue
        link = _availability_link(base_url, student.id, round_id, secret_key)
        subject, body = render_mail(
            KIND_REMINDER,
            {"name": student.name, "link": link, "round_label": round_label},
        )
        _deliver(
            session,
            sender,
            student_id=student.id,
            to=student.email,
            kind=KIND_REMINDER,
            round_id=round_id,
            subject=subject,
            body=body,
            report=report,
        )
    return _log_summary(KIND_REMINDER, round_id, report)


def send_slot_assignments(
    session: Session,
    sender: MailSender,
    round_id: int,
    *,
    force: bool = False,
) -> SendReport:
    """Notify every member of each scheduled group of their assigned slot."""
    report = SendReport()
    presentations = get_presentations_for_round(session, round_id)
    slots = {s.id: s for s in get_slots_for_round(session, round_id)}
    group_names = {g.id: g.name for g in get_all_groups(session)}
    group_ids = [p.group_id for p in presentations]
    students_by_group: dict[str, list[Student]] = {}
    for student in get_students_in_groups(session, group_ids):
        students_by_group.setdefault(student.group_id, []).append(student)

    for pres in presentations:
        slot = slots.get(pres.slot_id)
        slot_time = slot.starts_at.strftime("%d.%m.%Y %H:%M") if slot else "(unbekannt)"
        room = slot.room if slot else ""
        group_name = group_names.get(pres.group_id, pres.group_id)
        for student in students_by_group.get(pres.group_id, []):
            if not force and email_already_sent(session, student.id, KIND_ASSIGNMENT, round_id):
                report.skipped.append(student.id)
                continue
            subject, body = render_mail(
                KIND_ASSIGNMENT,
                {
                    "name": student.name,
                    "group_name": group_name,
                    "slot_time": slot_time,
                    "room": room,
                },
            )
            _deliver(
                session,
                sender,
                student_id=student.id,
                to=student.email,
                kind=KIND_ASSIGNMENT,
                round_id=round_id,
                subject=subject,
                body=body,
                report=report,
            )
    return _log_summary(KIND_ASSIGNMENT, round_id, report)
