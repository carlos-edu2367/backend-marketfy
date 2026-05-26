from __future__ import annotations

from typing import Any, Optional

from fastapi import Request

from application.services.audit_service import AuditService
from domain.identity import User
from infra.config.logger import get_logger
from infra.observability.request_context import get_request_id
from infra.security.rate_limiter import get_client_ip

logger = get_logger("audit")


async def record_audit_event(
    audit: Optional[AuditService],
    request: Request,
    *,
    actor: Optional[User],
    action: str,
    resource_type: str,
    result: str,
    market_id=None,
    resource_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    if audit is None:
        return
    try:
        role = None
        if actor is not None:
            role = actor.role.value if hasattr(actor.role, "value") else str(actor.role)
        await audit.record(
            actor_user_id=actor.id if actor is not None else None,
            actor_role=role,
            market_id=market_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            result=result,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            request_id=get_request_id(),
            metadata=metadata,
        )
    except Exception:
        logger.exception(
            "audit_record_failed",
            extra={
                "extra_data": {
                    "action": action,
                    "resource_type": resource_type,
                    "result": result,
                    "request_id": get_request_id(),
                }
            },
        )
