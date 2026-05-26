"""Cliente HTTP interno para o Billing Core.

Fase 4 / PR 14.

Regras de segurança:
  - API key e URL nunca retornadas ao frontend.
  - Logs não incluem API key, payloads sensíveis ou dados de pagamento.
  - Timeouts curtos e retry apenas para erros seguros.
  - X-System: marketfy obrigatório em todas as requisições.
  - Idempotency-Key obrigatório em POST /v1/subscriptions.

Contrato conhecido (billing_core.md):
  POST /v1/subscriptions — cria assinatura
    Payload: system, system_sub_id, customer_provider_id, description,
             value, subscription_type, expires_at, webhook_link
    Retorna: necessita validação manual — assumimos { job_id: str, ... }

  GET /v1/jobs/{job_id} — polling de status
    Retorna: necessita validação manual

Campos marcados como "necessita validação manual" indicam que o
billing_core.md não documenta o formato exato de resposta.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from infra.config.logger import get_logger
from infra.config.settings import get_settings

logger = get_logger("billing_core_client")
settings = get_settings()


class BillingCoreError(Exception):
    """Erro controlado na comunicação com o Billing Core."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


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
            "X-Api-Key": self._api_key,
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

    # ------------------------------------------------------------------
    # POST /v1/subscriptions
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
        """Cria assinatura no Billing Core.

        Retorno esperado: { job_id: str, ... }
        O formato exato do retorno necessita validação manual com o Billing Core.

        Raises:
            BillingCoreError: quando o Billing Core retorna erro ou não está disponível.
        """
        if not self._enabled:
            return self._mock_create_subscription(system_sub_id, subscription_type)

        if not self._base_url or not self._api_key:
            raise BillingCoreError(
                "Billing Core não configurado. Defina BILLING_CORE_BASE_URL e BILLING_CORE_API_KEY."
            )

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

        path = "/v1/subscriptions"
        self._log_request("POST", path)

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            ) as client:
                response = await client.post(
                    path,
                    json=payload,
                    headers=self._headers(idempotency_key=idempotency_key),
                )
                self._log_response("POST", path, response.status_code)

                if response.status_code not in (200, 201, 202):
                    raise BillingCoreError(
                        f"Billing Core retornou {response.status_code} em POST /v1/subscriptions.",
                        status_code=response.status_code,
                    )

                return response.json()

        except httpx.TimeoutException:
            raise BillingCoreError("Timeout ao contatar Billing Core.")
        except httpx.NetworkError as exc:
            raise BillingCoreError(f"Erro de rede ao contatar Billing Core: {type(exc).__name__}")
        except BillingCoreError:
            raise
        except Exception as exc:
            raise BillingCoreError(f"Erro inesperado ao contatar Billing Core: {type(exc).__name__}")

    # ------------------------------------------------------------------
    # GET /v1/jobs/{job_id}
    # ------------------------------------------------------------------

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Polling de status de job.

        Retorno esperado: { status: str, result: dict, ... }
        O formato exato necessita validação manual com o Billing Core.
        """
        if not self._enabled:
            return {"job_id": job_id, "status": "done", "note": "mock"}

        if not self._base_url or not self._api_key:
            raise BillingCoreError("Billing Core não configurado.")

        path = f"/v1/jobs/{job_id}"
        self._log_request("GET", path)

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            ) as client:
                response = await client.get(path, headers=self._headers())
                self._log_response("GET", path, response.status_code)

                if response.status_code == 404:
                    raise BillingCoreError(f"Job {job_id} não encontrado.", status_code=404)

                if response.status_code != 200:
                    raise BillingCoreError(
                        f"Billing Core retornou {response.status_code} em GET /v1/jobs/{job_id}.",
                        status_code=response.status_code,
                    )

                return response.json()

        except httpx.TimeoutException:
            raise BillingCoreError(f"Timeout ao consultar job {job_id}.")
        except httpx.NetworkError as exc:
            raise BillingCoreError(f"Erro de rede: {type(exc).__name__}")
        except BillingCoreError:
            raise
        except Exception as exc:
            raise BillingCoreError(f"Erro inesperado: {type(exc).__name__}")

    # ------------------------------------------------------------------
    # Mocks para desenvolvimento / testes
    # ------------------------------------------------------------------

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
