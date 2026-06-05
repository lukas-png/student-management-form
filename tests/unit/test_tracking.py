from planer.domain.tracking import CarryOver, compute_carry_over


class TestComputeCarryOver:
    def test_presented_is_excluded(self) -> None:
        result = compute_carry_over(presented_group_ids=["g1"], no_show_group_ids=[])
        assert result.excluded == frozenset({"g1"})
        assert result.pending == frozenset()

    def test_no_show_is_pending(self) -> None:
        result = compute_carry_over(presented_group_ids=[], no_show_group_ids=["g2"])
        assert result.pending == frozenset({"g2"})
        assert result.excluded == frozenset()

    def test_present_wins_over_later_noshow_record(self) -> None:
        # g1 no-showed once but presented later → done, not pending
        result = compute_carry_over(presented_group_ids=["g1"], no_show_group_ids=["g1"])
        assert result.excluded == frozenset({"g1"})
        assert result.pending == frozenset()

    def test_mixed(self) -> None:
        result = compute_carry_over(
            presented_group_ids=["g1", "g2"],
            no_show_group_ids=["g3", "g2"],
        )
        assert result.excluded == frozenset({"g1", "g2"})
        assert result.pending == frozenset({"g3"})

    def test_empty(self) -> None:
        assert compute_carry_over([], []) == CarryOver(frozenset(), frozenset())
