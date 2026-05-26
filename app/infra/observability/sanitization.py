from __future__ import annotations

from typing import Any

SENSITIVE_KEYS = {
    "authorization",
    "access_token",
    "token",
    "api_key",
    "x_api_key",
    "password",
    "password_hash",
    "certificate_password",
    "certificate",
    "certificate_file",
    "csc_token",
    "secret",
    "secret_key",
    "cpf",
    "customer_cpf",
}

SENSITIVE_PAYLOAD_KEYS = {
    "sale",
    "sales",
    "items",
    "payments",
    "raw_payload",
    "payload",
}

REDACTED = "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or normalized in SENSITIVE_PAYLOAD_KEYS


def sanitize_log_data(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                sanitized[key] = REDACTED
            else:
                sanitized[key] = sanitize_log_data(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_log_data(item) for item in value[:20]]
    return value

