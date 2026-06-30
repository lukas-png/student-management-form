from __future__ import annotations

import argparse
from pathlib import Path

from sqlmodel import Session

from planer.adapters.db import (
    create_tables,
    delete_round,
    make_engine,
    upsert_groups,
    upsert_students,
)
from planer.adapters.ingest_api import fetch_participants
from planer.adapters.ingest_excel import parse_excel
from planer.adapters.mail import SmtpMailSender
from planer.adapters.submit_results import submit_presentations
from planer.config import get_settings
from planer.domain.filtering import build_groups
from planer.logging_setup import configure_logging, get_logger
from planer.mailing import (
    SendReport,
    send_availability_requests,
    send_reminders,
    send_slot_assignments,
)
from planer.planning import solve_round

logger = get_logger("cli")


def _build_sender() -> SmtpMailSender:
    s = get_settings()
    return SmtpMailSender(
        host=s.smtp_host,
        port=s.smtp_port,
        user=s.smtp_user,
        password=s.smtp_password,
        mail_from=s.mail_from,
        reply_to=s.mail_reply_to,
        override_to=s.mail_override_to,
        starttls=s.smtp_starttls,
    )


def _load_settings():  # type: ignore[no-untyped-def]
    """Load settings and apply the configured log level/format to the CLI."""
    settings = get_settings()
    configure_logging(settings.log_level, json_format=settings.log_json)
    return settings


def _print_report(report: SendReport) -> None:
    print(
        f"sent={len(report.sent)} skipped={len(report.skipped)} "
        f"excluded={len(report.excluded)} not_due={len(report.not_due)} "
        f"failed={len(report.failed)}"
    )
    for student_id, error in report.failed:
        print(f"  FAILED {student_id}: {error}")


def _cmd_import(args: argparse.Namespace) -> None:
    settings = _load_settings()
    source = "excel" if args.excel else "api"
    if args.excel:
        participants = parse_excel(Path(args.excel))
    else:
        participants = fetch_participants(
            settings.stumgmt_api_url,
            settings.stumgmt_course_id,
            settings.sparky_auth_url,
            settings.sparky_user,
            settings.sparky_password,
            presentation_assignment_id=settings.stumgmt_presentation_assignment_id,
            presentation_assignment_name=settings.stumgmt_presentation_assignment_name,
            presentation_pass_points=settings.presentation_pass_points,
        )
    groups = build_groups(participants)
    engine = make_engine(settings.database_url)
    create_tables(engine)
    with Session(engine) as session:
        upsert_students(session, participants)
        upsert_groups(session, groups)
    logger.info(
        "import complete",
        extra={"source": source, "participants": len(participants), "groups": len(groups)},
    )
    print(f"Imported {len(participants)} participants into {len(groups)} groups.")


def _cmd_plan(args: argparse.Namespace) -> None:
    settings = _load_settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        result = solve_round(session, args.round_id, settings.individual_threshold)
    print(f"Assigned {len(result.assignments)} group(s); {len(result.unplaced)} unplaced.")
    for group_id, reason in result.unplaced:
        print(f"  UNPLACED {group_id}: {reason}")


def _cmd_send(args: argparse.Namespace) -> None:
    settings = _load_settings()
    engine = make_engine(settings.database_url)
    sender = _build_sender()
    with Session(engine) as session:
        if args.what == "availability":
            report = send_availability_requests(
                session,
                sender,
                args.round_id,
                base_url=settings.public_base_url,
                secret_key=settings.secret_key,
                threshold=settings.individual_threshold,
                force=args.force,
            )
        else:  # assignment
            report = send_slot_assignments(
                session,
                sender,
                args.round_id,
                threshold=settings.individual_threshold,
                force=args.force,
            )
    _print_report(report)


def _cmd_remind(args: argparse.Namespace) -> None:
    settings = _load_settings()
    engine = make_engine(settings.database_url)
    sender = _build_sender()
    with Session(engine) as session:
        report = send_reminders(
            session,
            sender,
            args.round_id,
            base_url=settings.public_base_url,
            secret_key=settings.secret_key,
            threshold=settings.individual_threshold,
            force=args.force,
        )
    _print_report(report)


def _cmd_sync(args: argparse.Namespace) -> None:
    settings = _load_settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        report = submit_presentations(
            session,
            settings,
            args.round_id,
            force=args.force,
            dry_run=args.dry_run,
        )
    prefix = "DRY-RUN " if report.dry_run else ""
    print(
        f"{prefix}assignment={report.assignment_id} created={len(report.created)} "
        f"updated={len(report.updated)} skipped={len(report.skipped)} "
        f"failed={len(report.failed)}"
    )
    for user_id, error in report.failed:
        print(f"  FAILED {user_id}: {error}")


def _cmd_purge(args: argparse.Namespace) -> None:
    if not args.yes:
        print(
            f"This will permanently delete round {args.round_id} and all its slots, "
            "availabilities, presentations, curations and email logs.\n"
            "Re-run with --yes to confirm."
        )
        return
    settings = _load_settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        deleted = delete_round(session, args.round_id)
    if deleted:
        logger.warning("round purged", extra={"round_id": args.round_id})
        print(f"Round {args.round_id} purged.")
    else:
        print(f"Round {args.round_id} not found.")


def main() -> None:
    # Configure with defaults so `--help` works without a populated .env; the
    # actual commands read settings (incl. log level) when they run.
    configure_logging()

    parser = argparse.ArgumentParser(description="Tutorium-Planer CLI")
    subparsers = parser.add_subparsers(dest="command")

    import_p = subparsers.add_parser("import", help="Import participants from Excel or API")
    import_p.add_argument("--excel", help="Path to a stu-mgmt Excel export; omit to use the API")
    import_p.set_defaults(func=_cmd_import)

    plan_p = subparsers.add_parser("plan", help="Run the scheduling solver for a round")
    plan_p.add_argument("round_id", type=int)
    plan_p.set_defaults(func=_cmd_plan)

    send_p = subparsers.add_parser("send", help="Send availability-request or assignment emails")
    send_p.add_argument("what", choices=["availability", "assignment"])
    send_p.add_argument("round_id", type=int)
    send_p.add_argument("--force", action="store_true", help="Bypass idempotency skip (resend)")
    send_p.set_defaults(func=_cmd_send)

    remind_p = subparsers.add_parser("remind", help="Remind students with no availability yet")
    remind_p.add_argument("round_id", type=int)
    remind_p.add_argument("--force", action="store_true", help="Bypass idempotency skip (resend)")
    remind_p.set_defaults(func=_cmd_remind)

    sync_p = subparsers.add_parser(
        "sync", help="Write presentation results back to stu-mgmt as assessments"
    )
    sync_p.add_argument("round_id", type=int)
    sync_p.add_argument("--force", action="store_true", help="Update existing assessments (PATCH)")
    sync_p.add_argument(
        "--dry-run", action="store_true", help="Show what would be written, make no changes"
    )
    sync_p.set_defaults(func=_cmd_sync)

    purge_p = subparsers.add_parser("purge", help="Delete a planning round and all its data")
    purge_p.add_argument("--round", dest="round_id", type=int, required=True)
    purge_p.add_argument("--yes", action="store_true", help="Confirm irreversible deletion")
    purge_p.set_defaults(func=_cmd_purge)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    func = getattr(args, "func", None)
    if func is not None:
        func(args)


if __name__ == "__main__":
    main()
