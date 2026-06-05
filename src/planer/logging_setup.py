"""Central logging configuration.

Two formats: human-readable text (dev) and one-JSON-object-per-line (prod, for
log aggregation). Magic-link tokens are credentials — `redact_path` strips them
from any URL path before it is logged, so tokens never land in the logs; logs
carry only `student_id`/`group_id`, never PII or secrets.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

# Standard LogRecord attributes — anything else attached via `extra=` is treated
# as a structured field and included in JSON output.
_STD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}

# Path segments that carry a signed token: /availability/<tok>, /tutor/<tok>.
_TOKEN_PATH = re.compile(r"(/(?:availability|tutor)/)[^/?#]+")

LOGGER_NAME = "planer"


def redact_path(path: str) -> str:
    """Replace magic-link tokens in a URL path with a placeholder."""
    return _TOKEN_PATH.sub(r"\1<token>", path)


class _TextFormatter(logging.Formatter):
    """Human-readable lines with any structured extras appended as key=value."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STD_ATTRS and not k.startswith("_")
        }
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        return base


class _JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message + structured extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", *, json_format: bool = False) -> None:
    """Idempotently configure the `planer` logger hierarchy.

    Attaches a single stderr handler with the chosen formatter and sets the
    level. Safe to call more than once (e.g. CLI + reload) — existing handlers
    are cleared first so logs are never duplicated.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False  # don't double-emit through the root logger

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    if json_format:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            _TextFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        )
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a child of the `planer` logger (e.g. get_logger("web.admin"))."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
