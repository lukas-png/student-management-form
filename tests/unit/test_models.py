import pytest

from planer.domain.models import parse_bool, parse_int


class TestParseBool:
    def test_wahr_returns_true(self) -> None:
        assert parse_bool("WAHR") is True

    def test_falsch_returns_false(self) -> None:
        assert parse_bool("FALSCH") is False

    def test_native_true_passthrough(self) -> None:
        assert parse_bool(True) is True

    def test_native_false_passthrough(self) -> None:
        assert parse_bool(False) is False

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse bool"):
            parse_bool("true")

    def test_lowercase_wahr_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_bool("wahr")


class TestParseInt:
    def test_native_int_passthrough(self) -> None:
        assert parse_int(5) == 5

    def test_zero(self) -> None:
        assert parse_int(0) == 0

    def test_string_digit(self) -> None:
        assert parse_int("3") == 3

    def test_string_zero(self) -> None:
        assert parse_int("0") == 0

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_int("abc")
