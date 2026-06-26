# Structured JSON logging per general_docs/LOGGING_INCIDENTINATOR.md §0.1.
# One JSON object per line on stdout; never log full tokens or webhook bodies.
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

SERVICE_NAME = "twenty-bridge"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": SERVICE_NAME,
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        # Attach structured extras passed via log_event(..., **fields).
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["error_class"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def log_event(
    logger: logging.Logger,
    event: str,
    message: str | None = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    # Emit a structured event. `fields` are merged into the JSON envelope.
    logger.log(level, message or event, extra={"event": event, "fields": fields})
