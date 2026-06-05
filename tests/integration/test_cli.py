"""CLI tests: the destructive purge command and its confirmation guard."""

from __future__ import annotations

import argparse
from datetime import datetime

from sqlmodel import Session, select

from planer import cli
from planer.adapters.db import (
    Group,
    Student,
    create_round,
    create_slot,
    create_tables,
    get_presentations_for_round,
    get_round,
    make_engine,
    upsert_availability,
    upsert_groups,
    upsert_students,
)
from planer.config import Settings
from planer.domain.models import Group as DomainGroup
from planer.domain.models import Participant


def _settings(db_url: str) -> Settings:
    return Settings(secret_key="x" * 32, database_url=db_url)


def test_purge_without_yes_does_not_delete(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path}/c.db"
    engine = make_engine(db_url)
    create_tables(engine)
    with Session(engine) as sess:
        rnd = create_round(sess)
        assert rnd.id is not None
        round_id = rnd.id

    monkeypatch.setattr(cli, "get_settings", lambda: _settings(db_url))
    cli._cmd_purge(argparse.Namespace(round_id=round_id, yes=False))

    with Session(engine) as sess:
        assert get_round(sess, round_id) is not None  # untouched
    assert "--yes" in capsys.readouterr().out


def test_purge_with_yes_deletes(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path}/c.db"
    engine = make_engine(db_url)
    create_tables(engine)
    with Session(engine) as sess:
        rnd = create_round(sess)
        assert rnd.id is not None
        round_id = rnd.id

    monkeypatch.setattr(cli, "get_settings", lambda: _settings(db_url))
    cli._cmd_purge(argparse.Namespace(round_id=round_id, yes=True))

    with Session(engine) as sess:
        assert get_round(sess, round_id) is None
    assert "purged" in capsys.readouterr().out


def test_purge_missing_round_reports(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path}/c.db"
    create_tables(make_engine(db_url))

    monkeypatch.setattr(cli, "get_settings", lambda: _settings(db_url))
    cli._cmd_purge(argparse.Namespace(round_id=42, yes=True))

    assert "not found" in capsys.readouterr().out


def test_import_excel_persists_students_and_groups(  # type: ignore[no-untyped-def]
    tmp_path, monkeypatch, capsys, sample_excel
) -> None:
    db_url = f"sqlite:///{tmp_path}/c.db"
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(db_url))

    cli._cmd_import(argparse.Namespace(excel=str(sample_excel)))

    engine = make_engine(db_url)
    with Session(engine) as sess:
        assert len(sess.exec(select(Student)).all()) > 0
        assert len(sess.exec(select(Group)).all()) > 0
    assert "Imported" in capsys.readouterr().out


def test_plan_creates_presentations(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path}/c.db"
    engine = make_engine(db_url)
    create_tables(engine)
    with Session(engine) as sess:
        upsert_students(
            sess,
            [
                Participant(
                    user_id="u1",
                    display_name="Alice",
                    email="a@uni.de",
                    matr_nr="1",
                    group_id="g1",
                    group_name="G1",
                    has_admission=True,
                    results=(),
                )
            ],
        )
        upsert_groups(sess, [DomainGroup(id="g1", name="G1", members=())])
        rnd = create_round(sess)
        assert rnd.id is not None
        slot = create_slot(sess, rnd.id, datetime(2024, 3, 18, 10, 0))
        assert slot.id is not None
        upsert_availability(sess, "u1", slot.id, available=True)
        round_id = rnd.id

    monkeypatch.setattr(cli, "get_settings", lambda: _settings(db_url))
    cli._cmd_plan(argparse.Namespace(round_id=round_id))

    with Session(engine) as sess:
        assert len(get_presentations_for_round(sess, round_id)) == 1
    assert "Assigned" in capsys.readouterr().out
