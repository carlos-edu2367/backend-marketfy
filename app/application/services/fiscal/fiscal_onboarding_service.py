"""
FiscalOnboardingService — guia de onboarding fiscal por mercado.

Responsabilidades:
- Avaliar cada etapa do checklist de homologação.
- Indicar o que está faltando de forma clara e acionável.
- Registrar resultado de emissão de teste.
- Proteger ativação de produção sem checklist completo.
- Orquestrar steps de onboarding no Neectify Fiscal.

Etapas do checklist Neectify (provider_steps):
1. neectify_issuer_created  — POST /v1/issuers
2. neectify_config_set      — POST /v1/issuers/{id}/nfce-configs
3. neectify_cert_uploaded   — POST /v1/issuers/{id}/certificates
4. neectify_cert_active     — POST /v1/certificates/{id}/activate
5. homolog_test_emitted     — NFC-e fictícia em homologação
6. homolog_test_authorized  — status authorized confirmado
7. production_enabled       — flag de habilitação de produção

Etapas do checklist interno (onboarding_status):
1. dados_fiscais       — CNPJ, razão social, regime, endereço, UF
2. certificado_a1      — Certificado carregado, não expirado, senha válida
3. csc                 — CSC ID e Token configurados
4. serie_numeracao     — Série NFC-e definida
5. perfil_tributario   — Ao menos um perfil fiscal de produto
6. emissao_teste       — Pelo menos uma emissão em homologação concluída
7. ambiente_producao   — Pronto para ativar produção
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from domain.fiscal import (
    FiscalDocumentStatus,
    FiscalEnvironment,
    FiscalTenantConfig,
    FiscalValidationError,
    NeectifyOnboardingStep,
    ValidationStatus,
)
from infra.config.logger import get_logger

logger = get_logger("fiscal_onboarding_service")


@dataclass
class ChecklistItem:
    key: str
    label: str
    description: str
    status: str          # "ok" | "pending" | "error" | "warning"
    details: Optional[str] = None
    required_for_production: bool = True


@dataclass
class OnboardingStatus:
    market_id: uuid.UUID
    overall_status: str   # "incomplete" | "ready_for_test" | "test_done" | "ready_for_production"
    checklist: List[ChecklistItem] = field(default_factory=list)
    can_activate_production: bool = False
    missing_required: List[str] = field(default_factory=list)
    completed_at: Optional[datetime] = None

    @property
    def completion_pct(self) -> int:
        if not self.checklist:
            return 0
        ok = sum(1 for c in self.checklist if c.status == "ok")
        return round((ok / len(self.checklist)) * 100)


class FiscalOnboardingService:

    def __init__(self, config_repo, doc_repo, tax_profile_repo, neectify_provider=None):
        self._config_repo = config_repo
        self._doc_repo = doc_repo
        self._tax_profile_repo = tax_profile_repo
        self._neectify = neectify_provider  # NeectifyFiscalProvider (opcional em tests)

    async def get_onboarding_status(self, market_id: uuid.UUID) -> OnboardingStatus:
        """
        Avalia o checklist fiscal completo para um mercado.
        Retorna o estado atual e o que ainda falta completar.
        """
        cfg: Optional[FiscalTenantConfig] = await self._config_repo.get_by_market(market_id)
        checklist = []

        # ── 1. Dados fiscais ─────────────────────────────────────────────────
        dados_ok, dados_detail = self._check_dados_fiscais(cfg)
        checklist.append(ChecklistItem(
            key="dados_fiscais",
            label="Dados Fiscais",
            description="CNPJ, razão social, regime tributário e endereço completos",
            status="ok" if dados_ok else "pending",
            details=dados_detail,
        ))

        # ── 2. Certificado A1 ────────────────────────────────────────────────
        cert_ok, cert_detail, cert_warning = self._check_certificado(cfg)
        if not cert_ok:
            cert_status = "pending"
        elif cert_warning:
            cert_status = "warning"
        else:
            cert_status = "ok"
        checklist.append(ChecklistItem(
            key="certificado_a1",
            label="Certificado A1",
            description="Certificado digital (.pfx) carregado e válido",
            status=cert_status,
            details=cert_detail,
        ))

        # ── 3. CSC ───────────────────────────────────────────────────────────
        csc_ok, csc_detail = self._check_csc(cfg)
        checklist.append(ChecklistItem(
            key="csc",
            label="CSC (Código de Segurança do Contribuinte)",
            description="CSC ID e Token SEFAZ configurados para NFC-e",
            status="ok" if csc_ok else "pending",
            details=csc_detail,
        ))

        # ── 4. Série e numeração ─────────────────────────────────────────────
        serie_ok, serie_detail = self._check_serie(cfg)
        checklist.append(ChecklistItem(
            key="serie_numeracao",
            label="Série e Numeração",
            description="Série NFC-e e modo de numeração definidos",
            status="ok" if serie_ok else "pending",
            details=serie_detail,
        ))

        # ── 5. Perfil tributário ─────────────────────────────────────────────
        profiles = await self._tax_profile_repo.list_by_market(market_id)
        perfil_ok = len(profiles) > 0 or bool(cfg and cfg.default_ncm and cfg.default_cfop)
        checklist.append(ChecklistItem(
            key="perfil_tributario",
            label="Perfil Tributário",
            description="Ao menos um perfil fiscal configurado (NCM, CFOP, CSOSN)",
            status="ok" if perfil_ok else "warning",
            details=None if perfil_ok else "Configure NCM/CFOP padrão ou crie um perfil fiscal.",
            required_for_production=False,
        ))

        # ── 6. Emissão de teste ──────────────────────────────────────────────
        test_ok, test_detail = await self._check_emissao_teste(market_id, cfg)
        checklist.append(ChecklistItem(
            key="emissao_teste",
            label="Emissão de Teste",
            description="Ao menos uma NFC-e emitida com sucesso em homologação",
            status="ok" if test_ok else "pending",
            details=test_detail,
            required_for_production=False,
        ))

        # ── 7. Ambiente de produção ──────────────────────────────────────────
        prod_ready = (
            cfg is not None
            and cfg.environment == FiscalEnvironment.PRODUCTION
            and cfg.validation_status == ValidationStatus.VALIDATED
            and cfg.enabled
        )
        checklist.append(ChecklistItem(
            key="ambiente_producao",
            label="Ambiente de Produção",
            description="Configuração validada e produção ativada",
            status="ok" if prod_ready else "pending",
            details=None if prod_ready else "Execute a validação e ative a produção após os testes.",
            required_for_production=False,
        ))

        # ── Status geral ─────────────────────────────────────────────────────
        required_items = [c for c in checklist if c.required_for_production]
        missing = [c.key for c in required_items if c.status not in ("ok", "warning")]

        all_required_ok = len(missing) == 0
        test_done = test_ok

        if all_required_ok and test_done:
            overall = "ready_for_production"
        elif all_required_ok:
            overall = "ready_for_test"
        else:
            overall = "incomplete"

        can_activate = all_required_ok and test_done and (cfg is not None)

        logger.info(
            "onboarding_status_evaluated",
            extra={"extra_data": {
                "market_id": str(market_id),
                "overall": overall,
                "missing": missing,
                "completion_pct": round((sum(1 for c in checklist if c.status == "ok") / len(checklist)) * 100),
            }},
        )

        return OnboardingStatus(
            market_id=market_id,
            overall_status=overall,
            checklist=checklist,
            can_activate_production=can_activate,
            missing_required=missing,
        )

    # ── Verificações privadas ─────────────────────────────────────────────────

    @staticmethod
    def _check_dados_fiscais(cfg: Optional[FiscalTenantConfig]):
        if not cfg:
            return False, "Nenhuma configuração fiscal encontrada. Configure os dados do estabelecimento."

        missing = []
        if not cfg.cnpj:
            missing.append("CNPJ")
        if not cfg.legal_name:
            missing.append("Razão Social")
        if not cfg.tax_regime:
            missing.append("Regime Tributário")
        if not getattr(cfg, "address_json", None) and not getattr(cfg, "uf", None):
            missing.append("Endereço/UF")

        if missing:
            return False, f"Campos obrigatórios faltando: {', '.join(missing)}"
        return True, None

    @staticmethod
    def _check_certificado(cfg: Optional[FiscalTenantConfig]):
        if not cfg or not cfg.certificate_storage_key:
            return False, "Certificado A1 não carregado.", False

        now = datetime.utcnow()
        if cfg.certificate_valid_until:
            delta = (cfg.certificate_valid_until - now).days
            if delta < 0:
                return False, f"Certificado expirado em {cfg.certificate_valid_until.strftime('%d/%m/%Y')}.", False
            if delta <= 30:
                return True, f"Atenção: certificado vence em {delta} dias.", True

        return True, None, False

    @staticmethod
    def _check_csc(cfg: Optional[FiscalTenantConfig]):
        if not cfg:
            return False, "Configure os dados fiscais antes."
        has_csc = bool(cfg.csc_id_ciphertext and cfg.csc_token_ciphertext)
        if not has_csc:
            return False, "CSC ID e Token não configurados. Obtenha junto à SEFAZ do seu estado."
        return True, None

    @staticmethod
    def _check_serie(cfg: Optional[FiscalTenantConfig]):
        if not cfg:
            return False, "Configure os dados fiscais antes."
        if not cfg.nfce_series:
            return False, "Série NFC-e não definida. Defina ao menos a série 1."
        return True, None

    async def _check_emissao_teste(
        self, market_id: uuid.UUID, cfg: Optional[FiscalTenantConfig]
    ):
        if not cfg:
            return False, "Configure os dados fiscais antes de testar."

        # Buscar documentos autorizados em homologação
        try:
            docs, total = await self._doc_repo.list_by_market(
                market_id,
                status=FiscalDocumentStatus.AUTHORIZED.value,
                limit=1,
                offset=0,
            )
            if total > 0:
                return True, f"{total} NFC-e(s) emitida(s) com sucesso em homologação."
            return False, "Emita ao menos uma NFC-e em homologação para confirmar a configuração."
        except Exception:
            return False, "Não foi possível verificar emissões de teste."

    # =========================================================================
    # Onboarding Neectify — steps de integração com o provider
    # =========================================================================

    async def sync_issuer(self, market_id: uuid.UUID, market_data: dict) -> dict:
        """
        Cria ou atualiza o emitente no Neectify Fiscal.

        Atualiza neectify_issuer_id e neectify_onboarding_step no config.
        """
        if not self._neectify:
            raise RuntimeError("NeectifyFiscalProvider não configurado.")

        cfg = await self._config_repo.get_by_market(market_id)
        if not cfg:
            raise ValueError("Configuração fiscal não encontrada para este mercado.")

        issuer_payload = _build_issuer_payload(cfg, market_data)

        try:
            result = await self._neectify.create_issuer(issuer_payload)
        except FiscalValidationError as exc:
            logger.error(
                "neectify_create_issuer_failed",
                extra={"extra_data": {"market_id": str(market_id), "error": str(exc)}},
            )
            raise

        issuer_id = result["id"]

        # Persistir via config_repo — atualizamos campos Neectify diretamente no modelo
        await self._config_repo.update_neectify_fields(
            market_id=market_id,
            issuer_id=issuer_id,
            onboarding_step=NeectifyOnboardingStep.ISSUER_CREATED.value,
        )

        logger.info(
            "neectify_issuer_created",
            extra={"extra_data": {"market_id": str(market_id), "issuer_id": issuer_id}},
        )
        return {"issuer_id": issuer_id, "onboarding_step": NeectifyOnboardingStep.ISSUER_CREATED.value}

    async def sync_cert(
        self,
        market_id: uuid.UUID,
        pfx_bytes: bytes,
        certificate_password: str,
        environment: str,
    ) -> dict:
        """
        Faz upload e ativação do certificado A1 no Neectify Fiscal.

        Atualiza neectify_cert_id e neectify_onboarding_step no config.
        """
        if not self._neectify:
            raise RuntimeError("NeectifyFiscalProvider não configurado.")

        cfg = await self._config_repo.get_by_market(market_id)
        if not cfg:
            raise ValueError("Configuração fiscal não encontrada para este mercado.")

        issuer_id = getattr(cfg, "neectify_issuer_id", None)
        if not issuer_id:
            raise ValueError("Emitente não criado no Neectify. Execute sync-issuer primeiro.")

        cert_result = await self._neectify.upload_certificate(
            issuer_id=issuer_id,
            pfx_bytes=pfx_bytes,
            password=certificate_password,
            environment=environment,
        )
        cert_id = cert_result["id"]

        activate_result = await self._neectify.activate_certificate(cert_id)

        await self._config_repo.update_neectify_fields(
            market_id=market_id,
            cert_id=cert_id,
            onboarding_step=NeectifyOnboardingStep.CERT_ACTIVE.value,
        )

        logger.info(
            "neectify_cert_activated",
            extra={"extra_data": {"market_id": str(market_id), "cert_id": cert_id}},
        )
        return {
            "cert_id": cert_id,
            "onboarding_step": NeectifyOnboardingStep.CERT_ACTIVE.value,
            "cert_status": activate_result.get("status"),
        }

    async def register_neectify_webhook(
        self,
        market_id: uuid.UUID,
        callback_url: str,
    ) -> dict:
        """
        Registra o webhook do Marketfy no Neectify Fiscal via POST /v1/webhooks.

        Deve ser chamado após sync_issuer e sync_cert, quando o callback_url
        do Marketfy (BILLING_CORE_WEBHOOK_CALLBACK_URL) já está disponível.
        """
        if not self._neectify:
            raise RuntimeError("NeectifyFiscalProvider não configurado.")

        payload = {
            "url": callback_url,
            "events": [
                "nfce.authorized",
                "nfce.rejected",
                "nfce.failed",
                "nfce.cancelled",
                "certificate.expiring",
                "usage.threshold_reached",
            ],
            "description": "Marketfy callback — status de NFC-e e alertas",
        }

        try:
            result = await self._neectify._client.request("POST", "/v1/webhooks", json=payload)
        except FiscalValidationError as exc:
            logger.error(
                "neectify_register_webhook_failed",
                extra={"extra_data": {"market_id": str(market_id), "error": str(exc)}},
            )
            raise

        webhook_id = result.get("id", "")
        logger.info(
            "neectify_webhook_registered",
            extra={"extra_data": {"market_id": str(market_id), "webhook_id": webhook_id}},
        )
        return {"webhook_id": webhook_id, "url": callback_url}

    async def get_neectify_status(self, market_id: uuid.UUID) -> dict:
        """Retorna campos de onboarding Neectify do config."""
        cfg = await self._config_repo.get_by_market(market_id)
        if not cfg:
            return {
                "neectify_issuer_id": None,
                "neectify_cert_id": None,
                "neectify_config_id": None,
                "neectify_onboarding_step": NeectifyOnboardingStep.PENDING.value,
            }
        return {
            "neectify_issuer_id": getattr(cfg, "neectify_issuer_id", None),
            "neectify_cert_id": getattr(cfg, "neectify_cert_id", None),
            "neectify_config_id": getattr(cfg, "neectify_config_id", None),
            "neectify_onboarding_step": getattr(cfg, "neectify_onboarding_step", None)
                or NeectifyOnboardingStep.PENDING.value,
        }


def _build_issuer_payload(cfg: FiscalTenantConfig, market_data: dict) -> dict:
    """Mapeia FiscalTenantConfig + dados do mercado para o payload POST /v1/issuers."""
    import json

    address_data: dict = {}
    if cfg.address_json:
        try:
            address_data = json.loads(cfg.address_json) if isinstance(cfg.address_json, str) else cfg.address_json
        except (ValueError, TypeError):
            pass

    tax_regime_map = {
        "simples_nacional": "simples_nacional",
        "lucro_presumido": "lucro_presumido",
        "lucro_real": "lucro_real",
        "mei": "simples_nacional",  # MEI usa Simples Nacional para fins do Neectify
    }
    tax_regime = tax_regime_map.get(
        cfg.tax_regime.value if cfg.tax_regime else "", "simples_nacional"
    )

    payload: dict = {
        "legal_name": cfg.legal_name or "",
        "trade_name": market_data.get("trade_name") or cfg.trade_name or "",
        "cnpj": (cfg.cnpj or "").replace(".", "").replace("/", "").replace("-", ""),
        "state_registration": cfg.state_registration or "",
        "tax_regime": tax_regime,
        "uf": address_data.get("uf") or market_data.get("uf") or "",
        "municipality_code": address_data.get("city_code") or address_data.get("municipality_code") or "",
        "municipality_name": address_data.get("city_name") or address_data.get("municipality_name") or "",
        "address": {
            "street": address_data.get("street") or "",
            "number": address_data.get("number") or "",
            "complement": address_data.get("complement"),
            "district": address_data.get("district") or "",
            "zip_code": (address_data.get("zip_code") or "").replace("-", ""),
            "city_code": address_data.get("city_code") or address_data.get("municipality_code") or "",
            "city_name": address_data.get("city_name") or address_data.get("municipality_name") or "",
            "uf": address_data.get("uf") or market_data.get("uf") or "",
        },
    }
    return payload
