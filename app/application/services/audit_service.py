from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from infra.observability.sanitization import sanitize_log_data


class AuditService:
    def __init__(self, audit_repo):
        self.audit_repo = audit_repo

    async def record(
        self,
        *,
        action: str,
        resource_type: str,
        result: str,
        actor_user_id: Optional[uuid.UUID] = None,
        actor_role: Optional[str] = None,
        market_id: Optional[uuid.UUID] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        from infra.database.models import AuditLogModel

        sanitized = sanitize_log_data(metadata or {})
        audit_log = AuditLogModel(
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            market_id=market_id,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            action=action,
            result=result,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            metadata_json=json.dumps(sanitized, default=str),
        )
        return await self.audit_repo.save(audit_log)
