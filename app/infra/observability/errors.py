from typing import Any, Optional


def error_response(
    code: str,
    message: str,
    request_id: Optional[str],
    details: Any = None,
) -> dict:
    error = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if details is not None:
        error["details"] = details
    return {"error": error}

