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
