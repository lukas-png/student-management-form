from planer.domain.models import Group
from planer.domain.scheduling import ScheduleResult, assign


def _group(gid: str) -> Group:
    return Group(id=gid, name=gid, members=())


class TestAssign:
    def test_known_solution(self) -> None:
        # G2 and G3 are most constrained (1 feasible slot each).
        # G1 has 2 options; after G2→S1 and G3→S2 both have 1 capacity left.
        # Tie on capacity → lex tiebreak S1 < S2 → G1→S1.
        groups = [_group("G1"), _group("G2"), _group("G3")]
        feasible = {"G1": {"S1", "S2"}, "G2": {"S1"}, "G3": {"S2"}}
        capacities = {"S1": 2, "S2": 2}
        result = assign(groups, feasible, capacities, pinned={})
        assert result.assignments == {"G1": "S1", "G2": "S1", "G3": "S2"}
        assert result.unplaced == []

    def test_capacity_never_exceeded(self) -> None:
        groups = [_group(f"G{i}") for i in range(10)]
        feasible = {f"G{i}": {"S1"} for i in range(10)}
        capacities = {"S1": 3}
        result = assign(groups, feasible, capacities, pinned={})
        assert sum(1 for sid in result.assignments.values() if sid == "S1") == 3
        assert len(result.unplaced) == 7

    def test_pins_respected_and_reduce_capacity(self) -> None:
        # S1 has capacity 1 and is pinned to G1 → G2 cannot fit
        groups = [_group("G1"), _group("G2")]
        feasible = {"G1": {"S1"}, "G2": {"S1"}}
        capacities = {"S1": 1}
        result = assign(groups, feasible, capacities, pinned={"G1": "S1"})
        assert result.assignments["G1"] == "S1"
        assert any(gid == "G2" for gid, _ in result.unplaced)

    def test_no_feasible_slots_reason(self) -> None:
        groups = [_group("G1")]
        result = assign(groups, feasible_slots={}, slot_capacities={"S1": 5}, pinned={})
        assert result.assignments == {}
        assert result.unplaced == [("G1", "no feasible slots")]

    def test_all_feasible_slots_at_capacity_reason(self) -> None:
        groups = [_group("G1"), _group("G2")]
        feasible = {"G1": {"S1"}, "G2": {"S1"}}
        capacities = {"S1": 1}
        result = assign(groups, feasible, capacities, pinned={})
        assert result.assignments.get("G1") == "S1"
        assert any(reason == "all feasible slots at capacity" for _, reason in result.unplaced)

    def test_determinism(self) -> None:
        groups = [_group(f"G{i}") for i in range(5)]
        feasible = {f"G{i}": {"S1", "S2"} for i in range(5)}
        capacities = {"S1": 3, "S2": 3}
        r1 = assign(groups, feasible, capacities, pinned={})
        r2 = assign(groups, feasible, capacities, pinned={})
        assert r1.assignments == r2.assignments
        assert r1.unplaced == r2.unplaced

    def test_load_balancing_prefers_slot_with_more_capacity(self) -> None:
        # S1 has more capacity than S2 → group should land in S1
        groups = [_group("G1")]
        feasible = {"G1": {"S1", "S2"}}
        capacities = {"S1": 3, "S2": 1}
        result = assign(groups, feasible, capacities, pinned={})
        assert result.assignments["G1"] == "S1"

    def test_empty_groups_no_crash(self) -> None:
        result = assign([], feasible_slots={}, slot_capacities={}, pinned={})
        assert result == ScheduleResult(assignments={}, unplaced=[])

    def test_all_groups_pinned(self) -> None:
        groups = [_group("G1"), _group("G2")]
        feasible = {"G1": {"S1"}, "G2": {"S2"}}
        capacities = {"S1": 1, "S2": 1}
        result = assign(groups, feasible, capacities, pinned={"G1": "S1", "G2": "S2"})
        assert result.assignments == {"G1": "S1", "G2": "S2"}
        assert result.unplaced == []
