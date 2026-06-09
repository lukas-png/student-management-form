from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    rule: str
    assignment_type: str
    achieved_points: int
    passed: bool


@dataclass(frozen=True)
class Participant:
    user_id: str
    display_name: str
    email: str
    matr_nr: str
    group_id: str  # empty string → solo student (Einzelgruppe)
    group_name: str
    has_admission: bool
    results: tuple[Result, ...]


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    members: tuple[Participant, ...]


@dataclass(frozen=True)
class Slot:
    id: str
    capacity: int = 5


def resolve_group_id(participant: Participant) -> str:
    """Canonical group id for a participant.

    Returns the real ``group_id`` if present, otherwise the ``user_id`` so that
    a solo student (empty ``group_id``) forms a stable single-member group. Both
    the domain group builder and the persistence layer use this so a solo
    student's stored ``group_id`` matches the synthesised ``Group.id``.
    """
    return participant.group_id or participant.user_id


def parse_bool(value: str | bool) -> bool:
    """Convert WAHR/FALSCH strings or native bools. Raises ValueError for anything else."""
    if isinstance(value, bool):
        return value
    if value == "WAHR":
        return True
    if value == "FALSCH":
        return False
    raise ValueError(f"Cannot parse bool: {value!r}")


def parse_int(value: str | int) -> int:
    """Convert int-string or native int. Raises ValueError for non-numeric input."""
    if isinstance(value, int):
        return value
    return int(value)
