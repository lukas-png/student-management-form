"""Pure assessment-payload building: per-user, PRESENTED-only, deterministic."""

from __future__ import annotations

from planer.domain.results_submission import build_assessments

_MEMBERS = {"g1": ["u1", "u2"], "g2": ["u3"], "u4": ["u4"]}


def test_one_payload_per_member_of_presented_group() -> None:
    payloads = build_assessments([("g1", "PRESENTED")], _MEMBERS, points=1, comment="c")
    assert [p.user_id for p in payloads] == ["u1", "u2"]


def test_no_show_and_scheduled_are_ignored() -> None:
    payloads = build_assessments(
        [("g1", "PRESENTED"), ("g2", "NO_SHOW"), ("u4", "SCHEDULED")],
        _MEMBERS,
        points=1,
        comment="c",
    )
    assert [p.user_id for p in payloads] == ["u1", "u2"]


def test_payload_fields() -> None:
    (p,) = build_assessments([("u4", "PRESENTED")], _MEMBERS, points=5, comment="hello")
    assert (p.user_id, p.achieved_points, p.is_draft, p.comment) == ("u4", 5, True, "hello")


def test_is_draft_can_be_overridden() -> None:
    (p,) = build_assessments([("u4", "PRESENTED")], _MEMBERS, points=1, comment="c", is_draft=False)
    assert p.is_draft is False


def test_deduplicated_and_sorted() -> None:
    members = {"g1": ["u2", "u1"], "g2": ["u2"]}  # u2 appears in two presented groups
    payloads = build_assessments(
        [("g2", "PRESENTED"), ("g1", "PRESENTED")], members, points=1, comment="c"
    )
    assert [p.user_id for p in payloads] == ["u1", "u2"]


def test_empty_when_nothing_presented() -> None:
    assert build_assessments([("g1", "NO_SHOW")], _MEMBERS, points=1, comment="c") == ()
