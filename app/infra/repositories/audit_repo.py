from infra.database.models import AuditLogModel


class SQLAlchemyAuditLogRepository:
    def __init__(self, db):
        self.db = db

    async def save(self, audit_log: AuditLogModel, commit: bool = True) -> AuditLogModel:
        self.db.add(audit_log)
        if commit:
            await self.db.commit()
        return audit_log
