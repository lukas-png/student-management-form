"""No-show carry-over logic, pure domain.

Lifecycle:
- A group that has ``PRESENTED`` in any prior round is **done** — excluded from
  future assignment.
- A group that was ``NO_SHOW`` but has never presented must present again —
  it carries over into the next round's pool.
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class CarryOver:
    excluded: frozenset[str]  # presented at least once → out of future rounds
    pending: frozenset[str]  # no-show, never presented → back in the pool


def compute_carry_over(
    presented_group_ids: Iterable[str],
    no_show_group_ids: Iterable[str],
) -> CarryOver:
    """Split prior outcomes into excluded vs. still-pending groups.

    A presentation always wins over a no-show: if a group both no-showed once
    and presented later, it counts as done (excluded), never pending.
    """
    presented = frozenset(presented_group_ids)
    pending = frozenset(no_show_group_ids) - presented
    return CarryOver(excluded=presented, pending=pending)
