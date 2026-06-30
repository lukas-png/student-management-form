"""Mail orchestration tests: idempotency + per-recipient error isolation."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import Session

from planer.adapters.db import (
    create_presentation,
    create_round,
    create_slot,
    create_tables,
    email_already_sent,
    make_engine,
    upsert_availability,
    upsert_groups,
    upsert_students,
)
from planer.domain.models import Group as DomainGroup
from planer.domain.models import Participant
from planer.mailing import (
    KIND_AVAILABILITY,
    KIND_REMINDER,
    render_mail,
    send_availability_requests,
    send_reminders,
    send_slot_assignments,
)

_SECRET = "test_secret_key_that_is_at_least_32_chars_long"
_BASE = "https://planer.example.de"
_T0 = datetime(2024, 3, 18, 10, 0)


class FakeMailSender:
    """Records every send; can be told to fail for specific recipients."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.sent: list[tuple[str, str, str]] = []  # (to, subject, body)
        self._fail_for = fail_for or set()

    def send(self, *, to: str, subject: str, body: str) -> None:
        if to in self._fail_for:
            raise RuntimeError(f"SMTP refused {to}")
        self.sent.append((to, subject, body))


def _participant(
    uid: str,
    group_id: str = "g1",
    email: str | None = None,
    *,
    has_assessment: bool = False,
) -> Participant:
    return Participant(
        user_id=uid,
        display_name=f"Student {uid}",
        email=email or f"{uid}@uni.de",
        matr_nr="111111",
        group_id=group_id,
        group_name="G1",
        has_admission=True,
        has_assessment=has_assessment,
        results=(),
    )


@pytest.fixture
def session():  # type: ignore[no-untyped-def]
    engine = make_engine("sqlite:///:memory:")
    create_tables(engine)
    with Session(engine) as sess:
        yield sess


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestApplyOverride:
    def test_no_override_keeps_recipient(self) -> None:
        from planer.adapters.mail import apply_override

        assert apply_override("a@uni.de", "Hi", "") == ("a@uni.de", "Hi")

    def test_override_redirects_and_tags_subject(self) -> None:
        from planer.adapters.mail import apply_override

        to, subject = apply_override("a@uni.de", "Hi", "test@me.de")
        assert to == "test@me.de"
        assert subject == "[TEST → a@uni.de] Hi"


class TestRenderMail:
    def test_availability_subject_and_link(self) -> None:
        subject, body = render_mail(
            KIND_AVAILABILITY, {"name": "Alice", "link": "https://x/availability/tok"}
        )
        assert subject == "[Tutorium] Bitte deine Verfügbarkeit eintragen"
        assert "Alice" in body
        assert "https://x/availability/tok" in body
        assert "Subject:" not in body

    def test_availability_includes_round_label_when_set(self) -> None:
        subject, body = render_mail(
            KIND_AVAILABILITY,
            {"name": "Alice", "link": "https://x/tok", "round_label": "Blatt 5"},
        )
        assert "Blatt 5" in subject
        assert "Blatt 5" in body

    def test_availability_omits_label_when_empty(self) -> None:
        subject, _ = render_mail(
            KIND_AVAILABILITY,
            {"name": "Alice", "link": "https://x/tok", "round_label": ""},
        )
        assert subject == "[Tutorium] Bitte deine Verfügbarkeit eintragen"

    def test_assignment_renders_slot(self) -> None:
        subject, body = render_mail(
            "slot_assignment",
            {"name": "Bob", "group_name": "G1", "slot_time": "18.03.2024 10:00", "room": "A101"},
        )
        assert "Vorstellungstermin" in subject
        assert "18.03.2024 10:00" in body
        assert "A101" in body


# ---------------------------------------------------------------------------
# Availability requests
# ---------------------------------------------------------------------------


class TestAvailabilityRequests:
    def test_sends_to_all_students(self, session: Session) -> None:
        upsert_students(session, [_participant("u1"), _participant("u2")])
        rnd = create_round(session)
        assert rnd.id is not None
        sender = FakeMailSender()

        report = send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        assert len(sender.sent) == 2
        assert sorted(report.sent) == ["u1", "u2"]
        assert report.skipped == []
        assert report.failed == []

    def test_not_due_member_gets_no_mail(self, session: Session) -> None:
        # u2 already has an assessment → not due → not summoned, even though its
        # group is invited again for the still-due u1.
        upsert_students(
            session,
            [_participant("u1", "g1"), _participant("u2", "g1", has_assessment=True)],
        )
        rnd = create_round(session)
        assert rnd.id is not None
        sender = FakeMailSender()

        report = send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        assert report.sent == ["u1"]
        assert report.not_due == ["u2"]
        assert len(sender.sent) == 1
        # the not-due member is never logged as mailed → no idempotency record
        assert email_already_sent(session, "u2", KIND_AVAILABILITY, rnd.id) is False

    def test_excluded_group_gets_no_mail(self, session: Session) -> None:
        from planer.adapters.db import upsert_curation, upsert_groups
        from planer.domain.models import Group as DomainGroup

        upsert_students(session, [_participant("u1", "g1"), _participant("u2", "g2")])
        upsert_groups(
            session,
            [
                DomainGroup(id="g1", name="G1", members=()),
                DomainGroup(id="g2", name="G2", members=()),
            ],
        )
        rnd = create_round(session)
        assert rnd.id is not None
        upsert_curation(session, "g1", rnd.id, included=False)  # g1 deselected
        sender = FakeMailSender()

        report = send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        assert report.sent == ["u2"]  # only the included group's member
        assert report.excluded == ["u1"]  # deselected group reported
        assert len(sender.sent) == 1

    def test_link_contains_base_url(self, session: Session) -> None:
        upsert_students(session, [_participant("u1")])
        rnd = create_round(session)
        assert rnd.id is not None
        sender = FakeMailSender()

        send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        _, _, body = sender.sent[0]
        assert f"{_BASE}/availability/" in body

    def test_idempotent_second_run_skips(self, session: Session) -> None:
        upsert_students(session, [_participant("u1"), _participant("u2")])
        rnd = create_round(session)
        assert rnd.id is not None
        sender = FakeMailSender()

        send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )
        report2 = send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        assert len(sender.sent) == 2  # no new sends
        assert sorted(report2.skipped) == ["u1", "u2"]
        assert report2.sent == []

    def test_force_resends(self, session: Session) -> None:
        upsert_students(session, [_participant("u1")])
        rnd = create_round(session)
        assert rnd.id is not None
        sender = FakeMailSender()

        send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )
        report2 = send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3, force=True
        )

        assert len(sender.sent) == 2
        assert report2.sent == ["u1"]

    def test_failure_isolated_and_logged(self, session: Session) -> None:
        upsert_students(session, [_participant("u1"), _participant("u2")])
        rnd = create_round(session)
        assert rnd.id is not None
        sender = FakeMailSender(fail_for={"u1@uni.de"})

        report = send_availability_requests(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        assert report.sent == ["u2"]  # u2 still went through
        assert len(report.failed) == 1
        assert report.failed[0][0] == "u1"
        # failed send is NOT counted as successfully sent → retryable
        assert email_already_sent(session, "u1", KIND_AVAILABILITY, rnd.id) is False
        assert email_already_sent(session, "u2", KIND_AVAILABILITY, rnd.id) is True

    def test_failed_send_retried_on_next_run(self, session: Session) -> None:
        upsert_students(session, [_participant("u1")])
        rnd = create_round(session)
        assert rnd.id is not None
        failing = FakeMailSender(fail_for={"u1@uni.de"})
        send_availability_requests(
            session, failing, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        ok = FakeMailSender()
        report = send_availability_requests(
            session, ok, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )
        assert report.sent == ["u1"]


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


class TestReminders:
    def test_only_to_non_responders(self, session: Session) -> None:
        upsert_students(session, [_participant("u1"), _participant("u2")])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        upsert_availability(session, "u1", slot.id, available=True)
        sender = FakeMailSender()

        report = send_reminders(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        assert report.sent == ["u2"]
        assert len(sender.sent) == 1

    def test_reminder_idempotent(self, session: Session) -> None:
        upsert_students(session, [_participant("u1")])
        rnd = create_round(session)
        assert rnd.id is not None
        sender = FakeMailSender()

        send_reminders(session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3)
        report2 = send_reminders(
            session, sender, rnd.id, base_url=_BASE, secret_key=_SECRET, threshold=3
        )

        assert report2.skipped == ["u1"]
        assert email_already_sent(session, "u1", KIND_REMINDER, rnd.id) is True


# ---------------------------------------------------------------------------
# Slot assignments
# ---------------------------------------------------------------------------


class TestSlotAssignments:
    def test_notifies_group_members(self, session: Session) -> None:
        upsert_students(session, [_participant("u1", "g1"), _participant("u2", "g1")])
        upsert_groups(session, [DomainGroup(id="g1", name="Gruppe Eins", members=())])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0, room="A101")
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)
        sender = FakeMailSender()

        report = send_slot_assignments(session, sender, rnd.id, threshold=3)

        assert sorted(report.sent) == ["u1", "u2"]
        assert all("Gruppe Eins" in body for _, _, body in sender.sent)
        assert all("A101" in body for _, _, body in sender.sent)

    def test_assignment_idempotent(self, session: Session) -> None:
        upsert_students(session, [_participant("u1", "g1")])
        upsert_groups(session, [DomainGroup(id="g1", name="G1", members=())])
        rnd = create_round(session)
        assert rnd.id is not None
        slot = create_slot(session, rnd.id, _T0)
        assert slot.id is not None
        create_presentation(session, "g1", slot.id, rnd.id)
        sender = FakeMailSender()

        send_slot_assignments(session, sender, rnd.id, threshold=3)
        report2 = send_slot_assignments(session, sender, rnd.id, threshold=3)

        assert len(sender.sent) == 1
        assert report2.skipped == ["u1"]
