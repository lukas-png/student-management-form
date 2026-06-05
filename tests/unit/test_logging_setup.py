import json
import logging

from planer.logging_setup import (
    LOGGER_NAME,
    configure_logging,
    get_logger,
    redact_path,
)


class TestRedactPath:
    def test_redacts_availability_token(self) -> None:
        assert redact_path("/availability/abc.def.ghi") == "/availability/<token>"

    def test_redacts_tutor_token(self) -> None:
        assert redact_path("/tutor/some-long-token-value") == "/tutor/<token>"

    def test_keeps_non_token_paths(self) -> None:
        assert redact_path("/admin/rounds/1") == "/admin/rounds/1"
        assert redact_path("/health") == "/health"

    def test_redacts_with_trailing_segments_or_query(self) -> None:
        # token stops at a slash or query/hash boundary
        assert redact_path("/availability/tok?x=1") == "/availability/<token>?x=1"


class TestConfigureLogging:
    def test_idempotent_no_duplicate_handlers(self) -> None:
        configure_logging("INFO")
        configure_logging("INFO")
        configure_logging("DEBUG")
        logger = logging.getLogger(LOGGER_NAME)
        assert len(logger.handlers) == 1
        assert logger.level == logging.DEBUG

    def test_does_not_propagate_to_root(self) -> None:
        configure_logging("INFO")
        assert logging.getLogger(LOGGER_NAME).propagate is False


class TestJsonFormatter:
    def test_emits_valid_json_with_extras(self) -> None:
        configure_logging("INFO", json_format=True)
        logger = get_logger("test")
        records: list[str] = []
        # capture the formatted output of the configured handler
        handler = logging.getLogger(LOGGER_NAME).handlers[0]
        formatted = handler.format(
            logger.makeRecord(
                logger.name,
                logging.INFO,
                "f",
                1,
                "hello",
                (),
                None,
                extra={"student_id": "u1", "round_id": 3},
            )
        )
        records.append(formatted)
        payload = json.loads(formatted)
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["student_id"] == "u1"
        assert payload["round_id"] == 3
        assert payload["logger"] == "planer.test"

    def test_text_format_appends_extras(self) -> None:
        configure_logging("INFO", json_format=False)
        handler = logging.getLogger(LOGGER_NAME).handlers[0]
        record = logging.getLogger(LOGGER_NAME).makeRecord(
            LOGGER_NAME, logging.INFO, "f", 1, "msg", (), None, extra={"round_id": 7}
        )
        out = handler.format(record)
        assert "msg" in out
        assert "round_id=7" in out
