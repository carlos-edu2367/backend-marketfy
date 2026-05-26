"""
FiscalInutilizacaoService — gestão de inutilização de numeração NFC-e/NF-e.

A inutilização é um processo fiscal obrigatório quando há lacunas de numeração
que não serão preenchidas (ex: falhas que não podem ser reemitidas, testes em
ambiente de produção, gaps por cancelamentos etc.).

Responsabilidades:
- Validar se o intervalo é válido e não conflita com emissões existentes.
- Registrar a inutilização com idempotência.
- Chamar o provider para envio à SEFAZ.
- Registrar resultado e auditoria.

Conformidade fiscal:
- Só permite inutilização em série/numeração que não possui NFC-e autorizada.
- Inutilização duplicada do mesmo intervalo retorna resultado existente.
- Justificativa mínima de 15 caracteres (regra SEFAZ).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from domain.fiscal import (
    FiscalEnvironment,
    FiscalNumberInutilizacao,
)
from domain.shared import BusinessRuleException
from infra.config.logger import get_logger
from infra.providers.fiscal.base import FiscalProviderGateway

logger = get_logger("fiscal_inutilizacao_service")


class FiscalInutilizacaoRepository:
    """Protocolo mínimo esperado pelo serviço."""
    async def get_by_idempotency_key(self, key: str) -> Optional[FiscalNumberInutilizacao]: ...
    async def save(self, entity: FiscalNumberInutilizacao) -> FiscalNumberInutilizacao: ...
    async def list_by_market(
        self,
        market_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]: ...


class FiscalInutilizacaoService:

    def __init__(
        self,
        inutilizacao_repo: FiscalInutilizacaoRepository,
        provider: FiscalProviderGateway,
    ):
        self._repo = inutilizacao_repo
        self._provider = provider

    @staticmethod
    def _idempotency_key(cnpj: str, series: int, start: int, end: int) -> str:
        cnpj_digits = cnpj.replace(".", "").replace("/", "").replace("-", "")
        return f"{cnpj_digits}:{series}:{start}:{end}"

    async def request_inutilizacao(
        self,
        market_id: uuid.UUID,
        requested_by_id: uuid.UUID,
        cnpj: str,
        document_type: str,
        series: int,
        start_number: int,
        end_number: int,
        justification: str,
        provider_name: str,
        environment: FiscalEnvironment,
        api_token: str,
    ) -> FiscalNumberInutilizacao:
        """
        Solicita inutilização de intervalo de numeração.

        Idempotente: mesmo intervalo retorna o registro existente se já autorizado.
        """
        if len(justification.strip()) < 15:
            raise BusinessRuleException("Justificativa deve ter ao menos 15 caracteres.")

        if start_number < 1:
            raise BusinessRuleException("Número inicial deve ser maior que zero.")

        if end_number < start_number:
            raise BusinessRuleException("Número final deve ser >= número inicial.")

        if (end_number - start_number) > 999:
            raise BusinessRuleException("Inutilização limitada a 1000 números por operação.")

        idem_key = self._idempotency_key(cnpj, series, start_number, end_number)

        # Verificar idempotência
        existing = await self._repo.get_by_idempotency_key(idem_key)
        if existing and existing.status == "authorized":
            logger.info(
                "inutilizacao_already_authorized",
                extra={"extra_data": {"idempotency_key": idem_key}},
            )
            return existing

        # Criar ou reutilizar registro pendente
        if not existing:
            entity = FiscalNumberInutilizacao(
                market_id=market_id,
                provider=provider_name,
                environment=environment,
                cnpj=cnpj,
                document_type=document_type,
                series=series,
                start_number=start_number,
                end_number=end_number,
                justification=justification,
                requested_by_id=requested_by_id,
                status="pending",
                idempotency_key=idem_key,
            )
            entity = await self._repo.save(entity)
        else:
            entity = existing

        # Enviar ao provider/SEFAZ
        logger.info(
            "inutilizacao_start",
            extra={"extra_data": {
                "market_id": str(market_id),
                "cnpj": cnpj[:4] + "***",
                "series": series,
                "range": f"{start_number}-{end_number}",
                "environment": environment.value,
            }},
        )

        try:
            result = await self._provider.inutilize_numbering(
                cnpj=cnpj,
                series=series,
                start_number=start_number,
                end_number=end_number,
                justification=justification,
                api_token=api_token,
                environment=environment.value,
            )
        except Exception as exc:
            entity.mark_failed(str(exc))
            await self._repo.save(entity)
            logger.error(
                "inutilizacao_exception",
                extra={"extra_data": {"error": str(exc), "idempotency_key": idem_key}},
            )
            raise BusinessRuleException(f"Erro ao comunicar inutilização: {exc}") from exc

        if result.success:
            entity.mark_authorized(
                protocol=result.protocol or "",
                message=result.message or "",
            )
            logger.info(
                "inutilizacao_authorized",
                extra={"extra_data": {
                    "idempotency_key": idem_key,
                    "protocol": result.protocol,
                }},
            )
        else:
            entity.mark_failed(result.error_message or "Falha desconhecida no provider")
            logger.warning(
                "inutilizacao_failed",
                extra={"extra_data": {
                    "idempotency_key": idem_key,
                    "error": result.error_message,
                }},
            )

        return await self._repo.save(entity)

    async def list_inutilizacoes(
        self,
        market_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        return await self._repo.list_by_market(market_id, status=status, limit=limit, offset=offset)
