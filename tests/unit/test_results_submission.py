"""Pure assessment-payload building: per-user, PRESENTED-only, deterministic."""

from __future__ import annotations

from planer.domain.results_submission import build_assessments


def test_one_payload_per_presented_member() -> None:
    payloads = build_assessments([("u1", "PRESENTED"), ("u2", "PRESENTED")], points=1, comment="c")
    assert [p.user_id for p in payloads] == ["u1", "u2"]


def test_no_show_and_scheduled_are_ignored() -> None:
    payloads = build_assessments(
        [("u1", "PRESENTED"), ("u2", "PRESENTED"), ("u3", "NO_SHOW"), ("u4", "SCHEDULED")],
        points=1,
        comment="c",
    )
    assert [p.user_id for p in payloads] == ["u1", "u2"]


def test_absent_member_of_presented_group_excluded() -> None:
    # u1 and u2 are in the same group; u2 was marked absent.
    payloads = build_assessments([("u1", "PRESENTED"), ("u2", "NO_SHOW")], points=1, comment="c")
    assert [p.user_id for p in payloads] == ["u1"]


def test_payload_fields() -> None:
    (p,) = build_assessments([("u4", "PRESENTED")], points=5, comment="hello")
    assert (p.user_id, p.achieved_points, p.is_draft, p.comment) == ("u4", 5, True, "hello")


def test_is_draft_can_be_overridden() -> None:
    (p,) = build_assessments([("u4", "PRESENTED")], points=1, comment="c", is_draft=False)
    assert p.is_draft is False


def test_deduplicated_and_sorted() -> None:
    # u2 appears twice (e.g. via two presentations); emit once, sorted.
    payloads = build_assessments(
        [("u2", "PRESENTED"), ("u1", "PRESENTED"), ("u2", "PRESENTED")], points=1, comment="c"
    )
    assert [p.user_id for p in payloads] == ["u1", "u2"]


def test_empty_when_nothing_presented() -> None:
    assert build_assessments([("u1", "NO_SHOW")], points=1, comment="c") == ()
