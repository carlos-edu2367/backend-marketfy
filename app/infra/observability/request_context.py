import re
import uuid
from contextvars import ContextVar
from typing import Optional

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def ensure_request_id(value: Optional[str]) -> str:
    if value and _REQUEST_ID_RE.match(value):
        return value
    return str(uuid.uuid4())


def set_request_id(value: str):
    return request_id_var.set(value)


def reset_request_id(token) -> None:
    request_id_var.reset(token)


def get_request_id() -> Optional[str]:
    return request_id_var.get()

