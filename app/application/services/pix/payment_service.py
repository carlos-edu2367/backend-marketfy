from __future__ import annotations
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from domain.pix import PixItem, PixAttemptStatus
from domain.sales import Sale, SaleStatus, BoxStatus
from infra.database.models import PixPaymentAttemptModel
from infra.config.settings import get_settings
from infra.config.logger import get_logger
from infra.observability.metrics import metrics_registry

logger = get_logger("pix_payment_service")


class PixNotConnectedError(Exception): ...
class PixActiveAttemptError(Exception):
    def __init__(self, attempt): self.attempt = attempt; super().__init__("Tentativa ativa existente.")
class PixBoxClosedError(Exception): ...
class PixInvalidItemsError(Exception): ...
class PixLocationNotConfiguredError(Exception):
    """A localização estruturada (rua/cidade/estado/lat/long) da loja não foi
    configurada pelo tenant. Nunca geocodificar ou inventar coordenadas — exigir
    configuração explícita antes de habilitar QR dinâmico."""


def _iso_expiration_to_delta(iso: str) -> timedelta:
    # suporta PTnM / PTnS simples
    import re
    m = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?", iso or "PT5M")
    minutes = int(m.group(1) or 0) if m else 5
    seconds = int(m.group(2) or 0) if m else 0
    return timedelta(minutes=minutes or (5 if not seconds else 0), seconds=seconds)


class PixPaymentService:
    _STATUS_EVENT = {
        "pending": "payment.pending", "approved": "payment.approved",
        "expired": "payment.expired", "canceled": "payment.cancelled",
        "divergent": "payment.error", "error": "payment.error",
        "confirmation_pending": "payment.confirmation_pending",
    }

    def __init__(self, *, attempt_repo, sale_repo, box_repo, product_repo,
                 connection_service, provider, lock,
                 market_repo=None, pos_location_provider=None, completer=None, event_bus=None):
        # market_repo/pos_location_provider: usados apenas por create_qr (Task 5b/6); ficam
        # opcionais para não quebrar a construção de PixPaymentService nos testes de
        # verify/cancel (Tasks 7-8), que não os utilizam.
        #
        # pos_location_provider: interface mínima `async get_location(market_id) -> dict` que
        # retorna o dict de localização (street_number/street_name/city_name/state_name/
        # latitude/longitude) configurado pelo tenant. Deve levantar
        # PixLocationNotConfiguredError se o tenant não configurou a localização
        # explicitamente — nunca geocodificar ou inventar coordenadas.
        self.attempt_repo = attempt_repo
        self.sale_repo = sale_repo
        self.box_repo = box_repo
        self.product_repo = product_repo
        self.market_repo = market_repo
        self.pos_location_provider = pos_location_provider
        self.connection_service = connection_service
        self.provider = provider
        self.lock = lock
        self.completer = completer
        self.event_bus = event_bus
        self.settings = get_settings()

    async def _publish_status_event(self, attempt):
        if not self.event_bus:
            return
        event = self._STATUS_EVENT.get(attempt.status, "payment.pending")
        await self._publish_best_effort(attempt.id, event, {
            "attempt_id": str(attempt.id), "status": attempt.status,
            "sale_id": str(attempt.sale_id) if attempt.sale_id else None,
            "sale_completed": attempt.status == "approved",
        })

    async def _publish_best_effort(self, attempt_id, event: str, data: dict) -> None:
        """Publica no event bus sem nunca deixar uma falha (Redis fora do ar,
        problema de rede) propagar e derrubar o fluxo de pagamento — o SSE é só
        um reflexo em tempo real do estado já persistido, nunca fonte de verdade,
        então uma falha aqui vira log, não uma venda/verificação/cancelamento
        que falhou por um motivo que nada tem a ver com o pagamento em si."""
        try:
            await self.event_bus.publish(str(attempt_id), event, data)
        except Exception:
            logger.warning("pix_event_bus_publish_failed",
                           extra={"extra_data": {"attempt_id": str(attempt_id)[:8], "event": event}})

    async def create_qr(self, *, market_id, terminal_id, box_id, operator_id, items):
        box = await self.box_repo.get_by_id(box_id)
        if not box or box.market_id != market_id or box.status != BoxStatus.OPEN:
            raise PixBoxClosedError("Caixa inválido ou fechado.")

        from application.services.pix.feature_flag import is_pix_qr_enabled, PixFeatureDisabledError
        connection_for_flag = await self.connection_service.repo.get_by_market(market_id)
        if not is_pix_qr_enabled(settings=self.settings, connection=connection_for_flag, terminal_id=terminal_id):
            raise PixFeatureDisabledError("Pix QR não habilitado para este caixa.")

        # 1. Resolver itens e total NO BACKEND
        pix_items, total = [], Decimal("0.00")
        for it in items:
            product = await self.product_repo.get_by_id(it["product_id"])
            if not product or product.market_id != market_id:
                raise PixInvalidItemsError("Produto não encontrado na loja.")
            qty = int(it["quantity"])
            total += product.price * qty
            pix_items.append(PixItem(title=product.name, unit_price=product.price,
                                     quantity=qty, external_code=getattr(product, "code", None)))
        if total <= 0:
            raise PixInvalidItemsError("Total inválido.")

        # 2. Token válido (garante integração conectada)
        try:
            access_token = await self.connection_service.get_valid_access_token(market_id)
        except Exception as exc:
            raise PixNotConnectedError(str(exc))

        # 2b. Garante Loja+Caixa registrados no MP (Task 5b) — nunca usar
        # str(terminal_id) diretamente como external_pos_id.
        connection = connection_for_flag
        market = await self.market_repo.get_by_id(market_id)
        location = await self.pos_location_provider.get_location(market_id)
        external_pos_id = await self.connection_service.ensure_pos_registered(
            market_id=market_id, terminal_id=terminal_id, access_token=access_token,
            market_name=market.name, location=location, mp_user_id=connection.mp_user_id,
        )

        # 3. Venda AWAITING_PAYMENT (estoque/caixa NÃO mudam aqui)
        sale = Sale(market_id=market_id, box_id=box_id, operator_id=operator_id,
                    status=SaleStatus.AWAITING_PAYMENT, total_amount=total)
        sale = await self.sale_repo.save(sale, commit=False)

        # 4. Tentativa ativa única
        active = await self.attempt_repo.get_active_by_sale(sale.id)
        if active:
            raise PixActiveAttemptError(active)

        attempt_id = uuid.uuid4()
        external_reference = f"pix{attempt_id.hex}"[:64]  # sem PII, só [a-z0-9]
        idempotency_key = str(uuid.uuid4())
        attempt = PixPaymentAttemptModel(
            id=attempt_id, market_id=market_id, sale_id=sale.id, box_id=box_id,
            terminal_id=terminal_id, operator_id=operator_id, amount=total, currency="BRL",
            external_reference=external_reference, idempotency_key=idempotency_key,
            status="pending",
        )
        await self.attempt_repo.save(attempt, commit=False)

        # 5. Lock por venda + chamar provider
        lock_key = f"pix:create:{sale.id}"
        got = await self.lock.acquire(lock_key, ttl=30)
        try:
            expiration = self.settings.MP_ORDER_DEFAULT_EXPIRATION
            result = await self.provider.create_qr_payment(
                access_token=access_token, amount=total, external_reference=external_reference,
                external_pos_id=external_pos_id, description="Venda PDV", items=pix_items,
                expiration=expiration, idempotency_key=idempotency_key,
            )
            attempt.order_id = result.order_id
            attempt.qr_data = result.qr_data
            attempt.receiver_account_id = result.receiver_account_id
            attempt.external_status = result.external_status
            attempt.expires_at = datetime.now(timezone.utc) + _iso_expiration_to_delta(expiration)
            await self.attempt_repo.save(attempt, commit=True)
            metrics_registry.record_pix_qr_created()
            if self.event_bus:
                await self._publish_best_effort(attempt.id, "payment.created", {
                    "attempt_id": str(attempt.id), "status": attempt.status,
                    "sale_id": str(attempt.sale_id) if attempt.sale_id else None,
                    "sale_completed": False,
                })
            return attempt
        finally:
            if got:
                await self.lock.release(lock_key)

    async def verify(self, *, market_id, attempt_id, source: str = "manual_button"):
        attempt = await self.attempt_repo.get_by_id_for_update(attempt_id, market_id)
        if attempt is None:
            raise PixInvalidItemsError("Tentativa não encontrada.")  # substituir por PixAttemptNotFoundError
        if attempt.status == "approved":
            return attempt  # idempotente
        access_token = await self.connection_service.get_valid_access_token(market_id)
        result = await self.provider.get_payment(access_token=access_token, order_id=attempt.order_id)
        attempt.external_status = result.external_status
        await self.attempt_repo.record_query(
            attempt_id=attempt.id, market_id=market_id, source=source,
            received_status=result.external_status)

        if result.mapped_status is PixAttemptStatus.APPROVED:
            if result.total_amount == attempt.amount:
                attempt.status = "approved"
                if self.completer:
                    await self.completer.complete_sale(attempt)
                metrics_registry.record_pix_payment_approved(source=source)
            else:
                attempt.status = "divergent"
                attempt.failure_reason = f"amount_mismatch:{result.total_amount}"
                metrics_registry.record_pix_divergence(kind="amount")
        elif result.mapped_status is PixAttemptStatus.EXPIRED:
            attempt.status = "expired"
            attempt.qr_data = None
            metrics_registry.record_pix_payment_expired()
        elif result.mapped_status is PixAttemptStatus.CANCELED:
            attempt.status = "canceled"
            attempt.qr_data = None
        # status desconhecido (None) ou pending: não avança
        await self.attempt_repo.save(attempt, commit=True)
        await self._publish_status_event(attempt)
        return attempt

    async def _apply_processed_or(self, attempt, result, otherwise_status):
        if result.mapped_status is PixAttemptStatus.APPROVED and result.total_amount == attempt.amount:
            attempt.status = "approved"
            if self.completer:
                await self.completer.complete_sale(attempt)
            return True
        attempt.status = otherwise_status
        attempt.qr_data = None
        return False

    async def cancel(self, *, market_id, attempt_id):
        attempt = await self.attempt_repo.get_by_id_for_update(attempt_id, market_id)
        if attempt is None:
            raise PixInvalidItemsError("Tentativa não encontrada.")
        if attempt.status in ("canceled", "expired", "approved"):
            return attempt
        access_token = await self.connection_service.get_valid_access_token(market_id)
        # reconsulta antes de cancelar
        pre = await self.provider.get_payment(access_token=access_token, order_id=attempt.order_id)
        if await self._apply_processed_or(attempt, pre, "pending") and attempt.status == "approved":
            await self.attempt_repo.save(attempt, commit=True)
            await self._publish_status_event(attempt)
            return attempt
        # cancela no MP e reconsulta
        import uuid as _u
        cancel_res = await self.provider.cancel_payment(
            access_token=access_token, order_id=attempt.order_id, idempotency_key=str(_u.uuid4()))
        await self._apply_processed_or(attempt, cancel_res, "canceled")
        if attempt.status == "canceled":
            metrics_registry.record_pix_attempt_canceled()
        await self.attempt_repo.save(attempt, commit=True)
        await self._publish_status_event(attempt)
        return attempt
