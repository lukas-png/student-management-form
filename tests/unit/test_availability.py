from planer.domain.availability import group_feasible_slots, member_declined
from planer.domain.models import Group, Participant, Slot

S1 = Slot(id="S1", capacity=5)
S2 = Slot(id="S2", capacity=5)
ALL_SLOTS = [S1, S2]


def _participant(user_id: str) -> Participant:
    return Participant(
        user_id=user_id,
        display_name=user_id,
        email=f"{user_id}@test.com",
        matr_nr="0",
        group_id="g1",
        group_name="G1",
        has_admission=True,
        results=(),
    )


def _group(*user_ids: str) -> Group:
    return Group(id="g1", name="G1", members=tuple(_participant(uid) for uid in user_ids))


class TestGroupFeasibleSlots:
    def test_all_members_available_returns_all_slots(self) -> None:
        avail = {
            ("u1", "S1"): True,
            ("u1", "S2"): True,
            ("u2", "S1"): True,
            ("u2", "S2"): True,
        }
        assert group_feasible_slots(_group("u1", "u2"), avail, ALL_SLOTS) == {"S1", "S2"}

    def test_one_member_unavailable_removes_slot(self) -> None:
        avail = {
            ("u1", "S1"): True,
            ("u1", "S2"): True,
            ("u2", "S1"): False,
            ("u2", "S2"): True,
        }
        assert group_feasible_slots(_group("u1", "u2"), avail, ALL_SLOTS) == {"S2"}

    def test_disjoint_availability_empty_intersection(self) -> None:
        avail = {
            ("u1", "S1"): True,
            ("u1", "S2"): False,
            ("u2", "S1"): False,
            ("u2", "S2"): True,
        }
        assert group_feasible_slots(_group("u1", "u2"), avail, ALL_SLOTS) == set()

    def test_missing_entry_counts_as_unavailable(self) -> None:
        # u2 never submitted a response
        avail = {("u1", "S1"): True, ("u1", "S2"): True}
        assert group_feasible_slots(_group("u1", "u2"), avail, ALL_SLOTS) == set()

    def test_false_entry_not_treated_as_available(self) -> None:
        avail = {("u1", "S1"): False, ("u1", "S2"): True}
        assert group_feasible_slots(_group("u1"), avail, ALL_SLOTS) == {"S2"}

    def test_single_member_group(self) -> None:
        avail = {("u1", "S1"): True, ("u1", "S2"): False}
        assert group_feasible_slots(_group("u1"), avail, ALL_SLOTS) == {"S1"}

    def test_empty_slots_list_returns_empty(self) -> None:
        avail: dict[tuple[str, str], bool] = {}
        assert group_feasible_slots(_group("u1"), avail, []) == set()

    def test_group_with_no_members(self) -> None:
        empty_group = Group(id="g0", name="G0", members=())
        avail: dict[tuple[str, str], bool] = {}
        # No members → intersection starts as all slots and is never narrowed
        assert group_feasible_slots(empty_group, avail, ALL_SLOTS) == {"S1", "S2"}


class TestGroupFeasibleSlotsAnyQuorum:
    def test_one_member_available_makes_slot_feasible(self) -> None:
        # u2 can do nothing, u1 can do S1 → S1 is feasible under "any"
        avail = {
            ("u1", "S1"): True,
            ("u1", "S2"): False,
            ("u2", "S1"): False,
            ("u2", "S2"): False,
        }
        assert group_feasible_slots(_group("u1", "u2"), avail, ALL_SLOTS, require_all=False) == {
            "S1"
        }

    def test_one_no_one_yes_slot_still_feasible(self) -> None:
        # On S1: u1 yes, u2 no → union keeps S1 (intersection would drop it)
        avail = {("u1", "S1"): True, ("u2", "S1"): False}
        assert group_feasible_slots(_group("u1", "u2"), avail, ALL_SLOTS, require_all=False) == {
            "S1"
        }

    def test_slot_nobody_can_is_excluded(self) -> None:
        avail = {
            ("u1", "S1"): True,
            ("u1", "S2"): False,
            ("u2", "S1"): False,
            # nobody is available for S2
        }
        assert group_feasible_slots(_group("u1", "u2"), avail, ALL_SLOTS, require_all=False) == {
            "S1"
        }

    def test_no_responses_empty(self) -> None:
        avail: dict[tuple[str, str], bool] = {}
        assert (
            group_feasible_slots(_group("u1", "u2"), avail, ALL_SLOTS, require_all=False) == set()
        )


class TestMemberDeclined:
    def test_explicit_no_is_decline(self) -> None:
        avail = {("u1", "S1"): True, ("u2", "S1"): False}
        assert member_declined(_group("u1", "u2"), "S1", avail) is True

    def test_non_response_is_not_decline(self) -> None:
        avail = {("u1", "S1"): True}  # u2 never answered
        assert member_declined(_group("u1", "u2"), "S1", avail) is False

    def test_all_yes_no_decline(self) -> None:
        avail = {("u1", "S1"): True, ("u2", "S1"): True}
        assert member_declined(_group("u1", "u2"), "S1", avail) is False


class TestMemberDeclinedAnyQuorum:
    def test_one_no_one_yes_is_not_conflict(self) -> None:
        # "any" mode: one available member keeps the pinned slot viable
        avail = {("u1", "S1"): True, ("u2", "S1"): False}
        assert member_declined(_group("u1", "u2"), "S1", avail, require_all=False) is False

    def test_one_no_one_silent_is_not_conflict(self) -> None:
        avail = {("u2", "S1"): False}  # u1 never answered
        assert member_declined(_group("u1", "u2"), "S1", avail, require_all=False) is False

    def test_all_decline_is_conflict(self) -> None:
        avail = {("u1", "S1"): False, ("u2", "S1"): False}
        assert member_declined(_group("u1", "u2"), "S1", avail, require_all=False) is True

    def test_no_responses_is_not_conflict(self) -> None:
        avail: dict[tuple[str, str], bool] = {}
        assert member_declined(_group("u1", "u2"), "S1", avail, require_all=False) is False
