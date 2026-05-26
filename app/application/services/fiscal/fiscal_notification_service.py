"""
FiscalNotificationService — gera e gerencia notificações fiscais.

Regras de dedupe:
- Notificação de 80% só é enviada UMA vez por período.
- Notificação de certificado vencendo: uma por fingerprint por janela.
- Evitar spam de alertas a cada venda.

Canal MVP: in-app + email (futuro: WhatsApp, webhook).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from domain.fiscal import (
    FiscalNotification,
    FiscalTenantConfig,
    NotificationSeverity,
    NotificationType,
)
from infra.config.logger import get_logger
from infra.repositories.fiscal_repo import SQLAlchemyFiscalNotificationRepository

logger = get_logger("fiscal_notification_service")


class FiscalNotificationService:
    def __init__(self, notification_repo: SQLAlchemyFiscalNotificationRepository):
        self.notification_repo = notification_repo

    async def notify_quota_usage(
        self,
        owner_id: uuid.UUID,
        market_id: uuid.UUID,
        period: str,
        usage_pct: float,
    ):
        """Envia notificação de uso de cota (80, 90, 100%)."""
        if usage_pct >= 100:
            await self._send(
                owner_id=owner_id,
                market_id=market_id,
                notification_type=NotificationType.FISCAL_LIMIT_100,
                severity=NotificationSeverity.CRITICAL,
                title="Limite de NFC-e atingido",
                message=(
                    "Você atingiu 100% do limite mensal de NFC-e. "
                    "Novas emissões estão bloqueadas. "
                    "Adquira um pacote adicional para continuar emitindo."
                ),
                dedupe_key=f"fiscal_limit_100:{owner_id}:{period}",
            )
        elif usage_pct >= 90:
            await self._send(
                owner_id=owner_id,
                market_id=market_id,
                notification_type=NotificationType.FISCAL_LIMIT_90,
                severity=NotificationSeverity.WARNING,
                title="90% do limite de NFC-e atingido",
                message=(
                    f"Você usou 90% do limite mensal de NFC-e "
                    f"({period[:4]}/{period[4:]}). "
                    "Considere adquirir um pacote adicional."
                ),
                dedupe_key=f"fiscal_limit_90:{owner_id}:{period}",
            )
        elif usage_pct >= 80:
            await self._send(
                owner_id=owner_id,
                market_id=market_id,
                notification_type=NotificationType.FISCAL_LIMIT_80,
                severity=NotificationSeverity.WARNING,
                title="80% do limite de NFC-e atingido",
                message=(
                    f"Você usou 80% do limite mensal de NFC-e. "
                    "Acompanhe o consumo no painel fiscal."
                ),
                dedupe_key=f"fiscal_limit_80:{owner_id}:{period}",
            )

    async def notify_certificate_expiring(
        self,
        owner_id: uuid.UUID,
        market_id: uuid.UUID,
        cfg: FiscalTenantConfig,
    ):
        """Alerta de certificado próximo do vencimento."""
        days = cfg.days_until_cert_expiry()
        if days is None:
            return

        fingerprint = cfg.certificate_fingerprint or "nofp"

        if days <= 1:
            await self._send(
                owner_id=owner_id,
                market_id=market_id,
                notification_type=NotificationType.CERTIFICATE_EXPIRING,
                severity=NotificationSeverity.CRITICAL,
                title="Certificado digital vence HOJE",
                message="O certificado digital vence hoje. Renove imediatamente para não interromper a emissão de NFC-e.",
                dedupe_key=f"cert_expiring_1:{market_id}:{fingerprint}",
            )
        elif days <= 7:
            await self._send(
                owner_id=owner_id,
                market_id=market_id,
                notification_type=NotificationType.CERTIFICATE_EXPIRING,
                severity=NotificationSeverity.CRITICAL,
                title=f"Certificado digital vence em {days} dias",
                message=f"O certificado digital vence em {days} dias. Renove para continuar emitindo NFC-e.",
                dedupe_key=f"cert_expiring_7:{market_id}:{fingerprint}",
            )
        elif days <= 15:
            await self._send(
                owner_id=owner_id,
                market_id=market_id,
                notification_type=NotificationType.CERTIFICATE_EXPIRING,
                severity=NotificationSeverity.WARNING,
                title=f"Certificado digital vence em {days} dias",
                message=f"O certificado digital vence em {days} dias. Programe a renovação.",
                dedupe_key=f"cert_expiring_15:{market_id}:{fingerprint}",
            )
        elif days <= 30:
            await self._send(
                owner_id=owner_id,
                market_id=market_id,
                notification_type=NotificationType.CERTIFICATE_EXPIRING,
                severity=NotificationSeverity.INFO,
                title=f"Certificado digital vence em {days} dias",
                message=f"O certificado digital vence em {days} dias.",
                dedupe_key=f"cert_expiring_30:{market_id}:{fingerprint}",
            )

    async def notify_invoice_rejected(
        self,
        owner_id: uuid.UUID,
        market_id: uuid.UUID,
        sale_id: uuid.UUID,
        sefaz_message: Optional[str],
    ):
        """Notifica rejeição de NFC-e para gerente."""
        await self._send(
            owner_id=owner_id,
            market_id=market_id,
            notification_type=NotificationType.INVOICE_REJECTED,
            severity=NotificationSeverity.WARNING,
            title="NFC-e rejeitada pela SEFAZ",
            message=(
                f"Uma NFC-e foi rejeitada. "
                f"Motivo: {sefaz_message or 'Consulte as pendências fiscais'}. "
                f"Venda: {str(sale_id)[:8]}..."
            ),
            dedupe_key=None,  # Cada rejeição é única
        )

    async def get_unread(self, owner_id: uuid.UUID) -> list:
        return await self.notification_repo.list_unread(owner_id)

    async def mark_read(self, notif_id: uuid.UUID, owner_id: uuid.UUID):
        await self.notification_repo.mark_read(notif_id, owner_id)

    async def create_notification(
        self,
        owner_id: uuid.UUID,
        notification_type,
        severity,
        title: str,
        message: str,
        dedupe_key: Optional[str] = None,
        market_id: Optional[uuid.UUID] = None,
    ):
        if isinstance(notification_type, str):
            notification_type = (
                NotificationType.ADDON_PURCHASED
                if notification_type == "credits_activated"
                else NotificationType(notification_type)
            )
        if isinstance(severity, str):
            severity = NotificationSeverity(severity)
        await self._send(
            owner_id=owner_id,
            market_id=market_id,
            notification_type=notification_type,
            severity=severity,
            title=title,
            message=message,
            dedupe_key=dedupe_key,
        )

    async def _send(
        self,
        owner_id: uuid.UUID,
        market_id: Optional[uuid.UUID],
        notification_type: NotificationType,
        severity: NotificationSeverity,
        title: str,
        message: str,
        dedupe_key: Optional[str],
    ):
        notif = FiscalNotification(
            owner_id=owner_id,
            market_id=market_id,
            notification_type=notification_type,
            severity=severity,
            title=title,
            message=message,
            dedupe_key=dedupe_key,
        )
        await self.notification_repo.save(notif)
        logger.info(
            "fiscal_notification_sent",
            extra={"extra_data": {
                "owner_id": str(owner_id),
                "type": notification_type.value,
                "severity": severity.value,
            }},
        )
