import json
import logging
import sys
from datetime import datetime
from typing import Any

from infra.observability.request_context import get_request_id
from infra.observability.sanitization import sanitize_log_data


class StructuredJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "level": record.levelname,
            "logger": record.name.replace("sgm_marketfy.", ""),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id

        extra_data = getattr(record, "extra_data", None)
        if isinstance(extra_data, dict):
            payload.update(sanitize_log_data(extra_data))

        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__

        return json.dumps(payload, default=str, ensure_ascii=False)


def get_logger(name: str):
    logger = logging.getLogger(f"sgm_marketfy.{name}")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJSONFormatter())
        logger.addHandler(handler)

    logger.propagate = False
    return logger
