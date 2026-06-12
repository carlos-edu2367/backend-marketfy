"""
Regressão (auditoria E2E 2026-06-12) — BUG E2E-2.

O onboarding do Marketfy nunca criava a config NFC-e no Neectify Fiscal
(POST /v1/issuers/{id}/nfce-configs) → toda emissão falhava com 422
`nfce.config_not_found`. `FiscalOnboardingService.sync_config` cria a config
(idempotente) decriptando o CSC.
"""
import uuid

import pytest

from application.services.fiscal.fiscal_onboarding_service import FiscalOnboardingService


class _Cfg:
    def __init__(self, **kw):
        self.neectify_issuer_id = kw.get("issuer_id", "iss_1")
        self.neectify_config_id = kw.get("config_id")
        self.csc_id_ciphertext = kw.get("csc_id_ct", "enc:cscid")
        self.csc_token_ciphertext = kw.get("csc_token_ct", "enc:csctok")
        self.nfce_series = kw.get("series", 1)
        self.nfce_next_number = kw.get("next_number", 1)
        self.environment = kw.get("environment", "homologacao")


class _Repo:
    def __init__(self, cfg):
        self._cfg = cfg
        self.updated = None

    async def get_by_market(self, market_id):
        return self._cfg

    async def update_neectify_fields(self, **kw):
        self.updated = kw


class _Provider:
    def __init__(self):
        self.called_with = None

    async def create_nfce_config(self, issuer_id, payload):
        self.called_with = (issuer_id, payload)
        return {"id": "nfce_cfg_X"}


class _Cipher:
    def decrypt(self, v):
        return {"enc:cscid": "000001", "enc:csctok": "TOKEN-XYZ"}.get(v, v)


def _svc(cfg, provider=None, cipher=None):
    return FiscalOnboardingService(
        config_repo=_Repo(cfg), doc_repo=None, tax_profile_repo=None,
        neectify_provider=provider or _Provider(), cipher=cipher or _Cipher(),
    )


@pytest.mark.asyncio
async def test_sync_config_creates_config_with_decrypted_csc():
    cfg = _Cfg()
    provider = _Provider()
    svc = _svc(cfg, provider=provider)
    out = await svc.sync_config(market_id=uuid.uuid4())

    assert out["config_id"] == "nfce_cfg_X"
    issuer_id, payload = provider.called_with
    assert issuer_id == "iss_1"
    assert payload["csc_id"] == "000001"
    assert payload["csc_token"] == "TOKEN-XYZ"
    assert payload["environment"] == "homologation"
    assert payload["series"] == 1 and payload["next_number"] == 1


@pytest.mark.asyncio
async def test_sync_config_is_idempotent_when_already_set():
    cfg = _Cfg(config_id="nfce_cfg_existing")
    provider = _Provider()
    out = await _svc(cfg, provider=provider).sync_config(market_id=uuid.uuid4())
    assert out["skipped"] == "already_set"
    assert provider.called_with is None  # não recria


@pytest.mark.asyncio
async def test_sync_config_skips_when_csc_not_configured():
    cfg = _Cfg(csc_id_ct=None, csc_token_ct=None)
    provider = _Provider()
    out = await _svc(cfg, provider=provider).sync_config(market_id=uuid.uuid4())
    assert out["skipped"] == "csc_not_configured"
    assert provider.called_with is None


@pytest.mark.asyncio
async def test_sync_config_maps_production_environment():
    cfg = _Cfg(environment="producao")
    provider = _Provider()
    await _svc(cfg, provider=provider).sync_config(market_id=uuid.uuid4())
    assert provider.called_with[1]["environment"] == "production"
