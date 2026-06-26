# Configure root logging to emit structured JSON on stdout (k8s: LOG_FORMAT=json,
# no LOG_FILE). See general_docs/LOGGING_INCIDENTINATOR.md §0.1.
from __future__ import annotations

import logging
import sys

from app.structured_log import JsonFormatter


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root.addHandler(handler)

    # Quiet noisy access logs at INFO; uvicorn errors still surface.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
