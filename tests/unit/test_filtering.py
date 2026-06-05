from planer.domain.filtering import build_groups, is_due, select_due_groups
from planer.domain.models import Participant, Result

_RULE = "INDIVIDUAL_PERCENT_WITH_ALLOWED_FAILURES"
THRESHOLD = 3


def _result(
    *,
    rule: str = _RULE,
    assignment_type: str = "HOMEWORK",
    achieved_points: int = 0,
    passed: bool = False,
) -> Result:
    return Result(
        rule=rule,
        assignment_type=assignment_type,
        achieved_points=achieved_points,
        passed=passed,
    )


def _participant(
    *,
    user_id: str = "u1",
    display_name: str = "Alice",
    email: str = "alice@example.com",
    matr_nr: str = "12345",
    group_id: str = "g1",
    group_name: str = "Group 1",
    has_admission: bool = True,
    results: tuple[Result, ...] = (),
) -> Participant:
    return Participant(
        user_id=user_id,
        display_name=display_name,
        email=email,
        matr_nr=matr_nr,
        group_id=group_id,
        group_name=group_name,
        has_admission=has_admission,
        results=results,
    )


class TestIsDue:
    def test_no_results_returns_false(self) -> None:
        assert is_due(_participant(results=()), THRESHOLD) is False

    def test_missing_rule_returns_false(self) -> None:
        p = _participant(results=(_result(rule="OTHER_RULE"),))
        assert is_due(p, THRESHOLD) is False

    def test_wrong_assignment_type_returns_false(self) -> None:
        p = _participant(results=(_result(assignment_type="EXAM"),))
        assert is_due(p, THRESHOLD) is False

    def test_below_threshold_returns_true(self) -> None:
        p = _participant(results=(_result(achieved_points=2),))
        assert is_due(p, THRESHOLD) is True

    def test_at_threshold_returns_false(self) -> None:
        # boundary: strictly less-than, so == threshold is NOT due
        p = _participant(results=(_result(achieved_points=3),))
        assert is_due(p, THRESHOLD) is False

    def test_above_threshold_returns_false(self) -> None:
        p = _participant(results=(_result(achieved_points=5),))
        assert is_due(p, THRESHOLD) is False

    def test_no_admission_returns_false_even_below_threshold(self) -> None:
        p = _participant(has_admission=False, results=(_result(achieved_points=0),))
        assert is_due(p, THRESHOLD) is False

    def test_rule_matched_by_name_not_index(self) -> None:
        # Matching rule is second in the list — must not rely on index 0
        p = _participant(
            results=(
                _result(rule="SOMETHING_ELSE", achieved_points=99),
                _result(rule=_RULE, achieved_points=1),
            )
        )
        assert is_due(p, THRESHOLD) is True

    def test_custom_threshold(self) -> None:
        p = _participant(results=(_result(achieved_points=5),))
        assert is_due(p, threshold=6) is True
        assert is_due(p, threshold=5) is False


class TestBuildGroups:
    def test_all_members_included_not_just_due(self) -> None:
        p1 = _participant(user_id="u1", group_id="g1", results=(_result(achieved_points=0),))
        p2 = _participant(user_id="u2", group_id="g1", results=(_result(achieved_points=9),))
        groups = build_groups([p1, p2])
        assert len(groups) == 1
        assert {m.user_id for m in groups[0].members} == {"u1", "u2"}

    def test_members_split_into_correct_groups(self) -> None:
        p1 = _participant(user_id="u1", group_id="g1")
        p2 = _participant(user_id="u2", group_id="g2")
        groups = build_groups([p1, p2])
        assert len(groups) == 2
        ids = {g.id for g in groups}
        assert ids == {"g1", "g2"}

    def test_solo_student_empty_group_id_forms_own_group(self) -> None:
        p = _participant(user_id="u99", group_id="", group_name="", display_name="Solo")
        groups = build_groups([p])
        assert len(groups) == 1
        assert groups[0].id == "u99"
        assert groups[0].name == "Solo"

    def test_group_name_preserved(self) -> None:
        p1 = _participant(user_id="u1", group_id="g1", group_name="Gruppe Alpha")
        p2 = _participant(user_id="u2", group_id="g1", group_name="Gruppe Alpha")
        groups = build_groups([p1, p2])
        assert groups[0].name == "Gruppe Alpha"

    def test_empty_participants_returns_empty(self) -> None:
        assert build_groups([]) == []


class TestSelectDueGroups:
    def test_one_flagged_member_makes_whole_group_due(self) -> None:
        p_due = _participant(user_id="u1", group_id="g1", results=(_result(achieved_points=1),))
        p_ok = _participant(user_id="u2", group_id="g1", results=(_result(achieved_points=9),))
        groups = build_groups([p_due, p_ok])
        due = select_due_groups(groups, THRESHOLD)
        assert len(due) == 1
        assert due[0].id == "g1"

    def test_no_flagged_members_group_not_due(self) -> None:
        p1 = _participant(user_id="u1", results=(_result(achieved_points=5),))
        p2 = _participant(user_id="u2", results=(_result(achieved_points=4),))
        groups = build_groups([p1, p2])
        assert select_due_groups(groups, THRESHOLD) == []

    def test_empty_groups_returns_empty(self) -> None:
        assert select_due_groups([], THRESHOLD) == []

    def test_only_due_groups_selected(self) -> None:
        p_due = _participant(user_id="u1", group_id="g1", results=(_result(achieved_points=1),))
        p_ok = _participant(user_id="u2", group_id="g2", results=(_result(achieved_points=5),))
        groups = build_groups([p_due, p_ok])
        due = select_due_groups(groups, THRESHOLD)
        assert len(due) == 1
        assert due[0].id == "g1"
