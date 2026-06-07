"""Shared logging configuration for CLI and API adapters."""

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any


DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s trace_id=%(trace_id)s %(message)s"


class JsonFormatter(logging.Formatter):
    """Minimal structured formatter with stable fields for run debugging."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace_id = getattr(record, "trace_id", None)
        if trace_id:
            payload["trace_id"] = trace_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(default_level: str = "INFO", override_level: str | None = None) -> None:
    """Configure app logging from env vars without touching server loggers.

    Env:
      LOG_LEVEL: adtech logger level, e.g. DEBUG, INFO, WARNING.
      LOG_FORMAT: "text" (default) or "json".
      LITELLM_LOG_LEVEL: LiteLLM logger level; defaults to ERROR.
    """
    level_name = os.getenv("LOG_LEVEL", override_level or default_level).upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    if os.getenv("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT, defaults={"trace_id": "-"}))

    adtech_logger = logging.getLogger("adtech")
    adtech_logger.handlers.clear()
    adtech_logger.addHandler(handler)
    adtech_logger.setLevel(level)
    adtech_logger.propagate = False

    litellm_level_name = os.getenv("LITELLM_LOG_LEVEL", "ERROR").upper()
    logging.getLogger("LiteLLM").setLevel(getattr(logging, litellm_level_name, logging.ERROR))
