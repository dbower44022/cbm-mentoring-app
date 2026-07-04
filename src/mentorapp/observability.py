"""Structured logging — the one logging seam for the whole application.

Every service logs structured JSON to stdout only; the hosting platform
captures it. All error reporting flows through this logger and the error
SDK — never bare prints, never swallowed exceptions.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Context fields (request id, user id, entity/record) ride in via
        # ``extra={"context": {...}}`` so every log line carries them uniformly.
        context = getattr(record, "context", None)
        if context:
            line.update(context)
        if record.exc_info and record.exc_info[0] is not None:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def get_logger(name: str) -> logging.Logger:
    """The application logger: structured JSON on stdout, configured once."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return logging.getLogger(name)
