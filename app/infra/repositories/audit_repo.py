from infra.database.models import AuditLogModel


class SQLAlchemyAuditLogRepository:
    def __init__(self, db):
        self.db = db

    async def save(self, audit_log: AuditLogModel) -> AuditLogModel:
        self.db.add(audit_log)
        await self.db.commit()
        return audit_log
