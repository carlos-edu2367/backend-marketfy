"""Cliente HTTP interno para o Billing Core."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from infra.config.logger import get_logger
from infra.config.settings import get_settings

logger = get_logger("billing_core_client")
settings = get_settings()


class BillingCoreError(Exception):
    """Base exception for Billing Core communication."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BillingCoreAuthError(BillingCoreError):
    """Credenciais inválidas ou sem permissão (401/403)."""
    pass


class BillingCoreValidationError(BillingCoreError):
    """Erro de validação ou payload incorreto (400/422)."""
    def __init__(self, message: str, details: dict | None = None, status_code: int | None = None):
        super().__init__(message, status_code=status_code)
        self.details = details or {}


class BillingCoreUnavailableError(BillingCoreError):
    """Billing Core temporariamente indisponível (timeout, rede ou 5xx)."""
    pass


class BillingCoreRateLimitError(BillingCoreError):
    """Estouro de limite de requisições (429)."""
    def __init__(self, message: str, retry_after: int | None = None, status_code: int | None = None):
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class BillingCoreClient:
    """HTTP client para o Billing Core.

    Usa httpx assíncrono com timeout e headers obrigatórios.
    Quando BILLING_CORE_ENABLED é False, retorna respostas mock
    para não bloquear desenvolvimento e testes.
    """

    def __init__(self):
        self._base_url = settings.BILLING_CORE_BASE_URL or ""
        self._api_key = settings.BILLING_CORE_API_KEY or ""
        self._system = settings.BILLING_CORE_SYSTEM
        self._timeout = settings.BILLING_CORE_TIMEOUT_SECONDS
        self._enabled = settings.BILLING_CORE_ENABLED

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "X-System": self._system,
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _log_request(self, method: str, path: str) -> None:
        logger.info(f"[BillingCore] {method} {path}")

    def _log_response(self, method: str, path: str, status: int) -> None:
        if status < 400:
            logger.info(f"[BillingCore] {method} {path} → {status}")
        else:
            logger.warning(f"[BillingCore] {method} {path} → {status} (erro controlado)")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        if not self._base_url or not self._api_key:
            raise BillingCoreUnavailableError(
                "Billing Core não configurado. Defina BILLING_CORE_BASE_URL e BILLING_CORE_API_KEY."
            )

        headers = self._headers(idempotency_key=idempotency_key)
        self._log_request(method, path)

        last_exc: Exception | None = None
        _RETRYABLE_STATUS = {429, 503, 504}
        _MAX_RETRIES = 3

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=float(self._timeout)) as client:
                    url = f"{self._base_url.rstrip('/')}/{path.lstrip('/')}"
                    response = await client.request(
                        method,
                        url,
                        json=json,
                        params=params,
                        headers=headers,
                    )
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.warning(
                    "billing_core_timeout",
                    extra={"extra_data": {"method": method, "path": path, "attempt": attempt + 1}},
                )
                raise BillingCoreUnavailableError("Timeout ao contatar Billing Core.") from exc
            except (httpx.NetworkError, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.warning(
                    "billing_core_network_error",
                    extra={"extra_data": {"method": method, "path": path, "attempt": attempt + 1}},
                )
                raise BillingCoreUnavailableError(f"Erro de rede ao contatar Billing Core: {type(exc).__name__}") from exc

            status = response.status_code
            self._log_response(method, path, status)

            if status in (200, 201, 202):
                if status == 204 or not response.content:
                    return {}
                return response.json()

            if status in (401, 403):
                raise BillingCoreAuthError(
                    f"Credenciais Billing Core inválidas ou sem permissão: {status}.",
                    status_code=status,
                )

            if status in (400, 422):
                try:
                    details = response.json()
                except Exception:
                    details = {}
                raise BillingCoreValidationError(
                    "Payload inválido para o Billing Core.",
                    details=details,
                    status_code=status,
                )

            if status == 429:
                retry_after_str = response.headers.get("Retry-After")
                retry_after = int(retry_after_str) if retry_after_str and retry_after_str.isdigit() else None
                if attempt < _MAX_RETRIES - 1:
                    delay = float(retry_after) if retry_after is not None else float(2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                raise BillingCoreRateLimitError(
                    "Limite de requisições excedido no Billing Core (429).",
                    retry_after=retry_after,
                    status_code=status,
                )

            if status in (503, 504):
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise BillingCoreUnavailableError(
                    f"Billing Core indisponível: {status}.",
                    status_code=status,
                )

            # Para qualquer outro erro de status (ex: 404, 409, 500 sem retry)
            raise BillingCoreError(
                f"Billing Core retornou status {status} inesperado.",
                status_code=status,
            )

        if last_exc:
            raise BillingCoreUnavailableError(
                f"Billing Core indisponível após {_MAX_RETRIES} tentativas."
            ) from last_exc
        raise BillingCoreUnavailableError("Billing Core indisponível.")

    # ------------------------------------------------------------------
    # NOVOS MÉTODOS PR 1
    # ------------------------------------------------------------------

    async def create_customer(
        self,
        *,
        nome_completo: str,
        email: str,
        system_customer_id: str,
        system: str,
        cpf: str | None = None,
        cnpj: str | None = None,
    ) -> dict:
        """Cria um cliente no Billing Core.

        POST /v1/customers
        Idempotente por CPF/CNPJ.
        """
        if not cpf and not cnpj:
            raise ValueError("Deve fornecer cpf ou cnpj.")
        if cpf and cnpj:
            raise ValueError("Forneça apenas cpf ou cnpj, não ambos.")

        if not self._enabled:
            return {"provider_customer_id": f"cus_mock_{uuid.uuid4().hex[:12]}"}

        payload = {
            "nome_completo": nome_completo,
            "email": email,
            "system_customer_id": system_customer_id,
            "system": system,
        }
        if cpf:
            payload["cpf"] = cpf
        if cnpj:
            payload["cnpj"] = cnpj

        return await self._request("POST", "/v1/customers", json=payload)

    async def create_payment(
        self,
        *,
        customer_provider_id: str,
        value: str,
        billing_type: str,
        due_date: str,
        description: str,
        system: str,
        system_payment_id: str,
        webhook_link: str,
        idempotency_key: str,
    ) -> dict:
        """Cria um pagamento avulso no Billing Core.

        POST /v1/payments
        """
        if not self._enabled:
            return {
                "job_id": f"job_mock_{uuid.uuid4().hex[:12]}",
                "message": "Payment creation accepted (mock)"
            }

        payload = {
            "customer_provider_id": customer_provider_id,
            "value": value,
            "billing_type": billing_type,
            "due_date": due_date,
            "description": description,
            "system": system,
            "system_payment_id": system_payment_id,
            "webhook_link": webhook_link,
        }

        return await self._request(
            "POST",
            "/v1/payments",
            json=payload,
            idempotency_key=idempotency_key,
        )

    async def get_job(self, job_id: str) -> dict:
        """Consulta o status de um job de processamento no Billing Core.

        GET /v1/jobs/{job_id}
        """
        if not self._enabled:
            return {
                "job_id": job_id,
                "status": "done",
                "job_name": "create_payment",
                "attempt": 1,
                "max_tries": 3,
                "request_id": f"req_{uuid.uuid4().hex[:12]}",
                "created_at": datetime.utcnow().isoformat(),
                "started_at": datetime.utcnow().isoformat(),
                "finished_at": datetime.utcnow().isoformat(),
                "error_code": None,
                "error_message": None,
            }

        return await self._request("GET", f"/v1/jobs/{job_id}")

    async def get_payment(self, payment_id: str) -> dict:
        """Consulta o estado de um pagamento no Billing Core.

        GET /v1/payments/{payment_id}
        """
        if not self._enabled:
            return {
                "payment_id": payment_id,
                "status": "paid",
                "value": "100.00",
                "billing_type": "UNDEFINED",
                "due_date": datetime.utcnow().isoformat(),
                "description": "Mock payment",
            }

        return await self._request("GET", f"/v1/payments/{payment_id}")

    # ------------------------------------------------------------------
    # MÉTODOS DE COMPATIBILIDADE - PLANOS (FASE 4)
    # ------------------------------------------------------------------

    async def create_subscription(
        self,
        system_sub_id: str,
        customer_provider_id: str,
        description: str,
        value: float,
        subscription_type: str,
        expires_at: datetime,
        webhook_link: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Cria assinatura no Billing Core (Planos)."""
        if not self._enabled:
            return self._mock_create_subscription(system_sub_id, subscription_type)

        payload = {
            "system": self._system,
            "system_sub_id": system_sub_id,
            "customer_provider_id": customer_provider_id,
            "description": description,
            "value": value,
            "subscription_type": subscription_type,
            "expires_at": expires_at.isoformat(),
            "webhook_link": webhook_link,
        }

        return await self._request(
            "POST",
            "/v1/subscriptions",
            json=payload,
            idempotency_key=idempotency_key,
        )

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Polling de status de job (Planos)."""
        return await self.get_job(job_id)

    def _mock_create_subscription(self, system_sub_id: str, subscription_type: str) -> Dict[str, Any]:
        mock_job_id = f"mock-job-{uuid.uuid4().hex[:8]}"
        mock_sub_id = f"mock-sub-{uuid.uuid4().hex[:8]}"
        logger.info(
            f"[BillingCore] MOCK create_subscription system_sub_id={system_sub_id} "
            f"type={subscription_type} job_id={mock_job_id}"
        )
        return {
            "job_id": mock_job_id,
            "subscription_id": mock_sub_id,
            "status": "pending",
            "note": "mock — BILLING_CORE_ENABLED=False",
        }
