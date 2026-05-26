import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database.models import RefreshSessionModel


class SQLAlchemyRefreshSessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: uuid.UUID,
        jti_hash: str,
        expires_at: datetime,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> RefreshSessionModel:
        model = RefreshSessionModel(
            user_id=user_id,
            jti_hash=jti_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.session.add(model)
        await self.session.commit()
        return model

    async def get_active(self, jti_hash: str) -> Optional[RefreshSessionModel]:
        stmt = select(RefreshSessionModel).where(
            RefreshSessionModel.jti_hash == jti_hash,
            RefreshSessionModel.revoked_at == None,
            RefreshSessionModel.expires_at > datetime.utcnow(),
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def revoke(self, jti_hash: str) -> bool:
        model = await self.get_active(jti_hash)
        if not model:
            return False
        model.revoked_at = datetime.utcnow()
        await self.session.commit()
        return True
