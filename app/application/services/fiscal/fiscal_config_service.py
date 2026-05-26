"""
FiscalConfigService — gerencia configuração fiscal segura por mercado.

Responsabilidades:
- Salvar/atualizar config com criptografia de segredos
- Upload e validação de certificado A1
- Validar configuração antes de ativar produção
- Registrar fingerprint e validade do certificado
- Garantir que segredos nunca retornem na API
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from domain.fiscal import (
    FiscalEnvironment,
    FiscalTenantConfig,
    NumberingMode,
    TaxRegime,
    ValidationStatus,
)
from domain.shared import BusinessRuleException
from infra.config.logger import get_logger
from infra.repositories.fiscal_repo import SQLAlchemyFiscalTenantConfigRepository
from infra.security.secret_cipher import SecretCipher
from infra.storage.fiscal_artifact_storage import FiscalArtifactStorage

logger = get_logger("fiscal_config_service")


class FiscalConfigService:
    def __init__(
        self,
        config_repo: SQLAlchemyFiscalTenantConfigRepository,
        cipher: SecretCipher,
        storage: FiscalArtifactStorage,
    ):
        self.config_repo = config_repo
        self.cipher = cipher
        self.storage = storage

    async def get_config(self, market_id: uuid.UUID) -> Optional[FiscalTenantConfig]:
        """Retorna config SEM descriptografar segredos (uso em API response)."""
        return await self.config_repo.get_by_market(market_id)

    async def get_config_decrypted(self, market_id: uuid.UUID) -> Optional[FiscalTenantConfig]:
        """Retorna config COM segredos descriptografados (uso interno no worker)."""
        cfg = await self.config_repo.get_by_market(market_id)
        if not cfg:
            return None
        return self._decrypt(cfg)

    async def save_config(
        self,
        market_id: uuid.UUID,
        data: dict,
        certificate_bytes: Optional[bytes] = None,
        certificate_filename: Optional[str] = None,
    ) -> FiscalTenantConfig:
        """
        Salva ou atualiza configuração fiscal.

        Regras:
        - Segredo vazio não sobrescreve segredo existente.
        - Certificado só é substituído se novo arquivo for enviado.
        - Mudança crítica (certificado, CSC, ambiente) volta status para needs_validation.
        """
        cfg = await self.config_repo.get_by_market(market_id)
        is_new = cfg is None

        if is_new:
            cfg = FiscalTenantConfig(market_id=market_id)

        changed_critical = False

        # Dados do estabelecimento
        for field in ("legal_name", "trade_name", "cnpj", "state_registration",
                      "municipal_registration", "crt", "cnae_primary", "provider"):
            if field in data and data[field] is not None:
                setattr(cfg, field, data[field])

        if "address" in data and data["address"]:
            cfg.address_json = data["address"]

        if "tax_regime" in data and data["tax_regime"]:
            cfg.tax_regime = TaxRegime(data["tax_regime"])

        # Ambiente
        if "environment" in data and data["environment"]:
            new_env = FiscalEnvironment(data["environment"])
            if new_env != cfg.environment:
                changed_critical = True
            cfg.environment = new_env

        # Certificado A1
        if certificate_bytes:
            cert_key, fingerprint, valid_until = await self._store_certificate(
                market_id, certificate_bytes, certificate_filename or "cert.pfx"
            )
            cfg.certificate_storage_key = cert_key
            cfg.certificate_fingerprint = fingerprint
            cfg.certificate_valid_until = valid_until
            changed_critical = True

        if "certificate_password" in data and data["certificate_password"]:
            cfg.certificate_password_ciphertext = self.cipher.encrypt(data["certificate_password"])
            changed_critical = True

        # CSC
        if "csc_id" in data and data["csc_id"]:
            new_encrypted = self.cipher.encrypt(data["csc_id"])
            if new_encrypted != cfg.csc_id_ciphertext:
                changed_critical = True
            cfg.csc_id_ciphertext = new_encrypted

        if "csc_token" in data and data["csc_token"]:
            new_encrypted = self.cipher.encrypt(data["csc_token"])
            if new_encrypted != cfg.csc_token_ciphertext:
                changed_critical = True
            cfg.csc_token_ciphertext = new_encrypted

        # Numeração
        if "nfce_series" in data and data["nfce_series"] is not None:
            cfg.nfce_series = int(data["nfce_series"])
        if "nfce_next_number" in data and data["nfce_next_number"] is not None:
            cfg.nfce_next_number = int(data["nfce_next_number"])
        if "numbering_mode" in data and data["numbering_mode"]:
            cfg.numbering_mode = NumberingMode(data["numbering_mode"])

        # Tributação padrão
        for field in ("default_cfop", "default_ncm", "default_csosn", "default_cst"):
            if field in data and data[field] is not None:
                setattr(cfg, field, data[field])

        # Responsável técnico
        if "responsavel_tecnico" in data:
            cfg.responsavel_tecnico = data["responsavel_tecnico"]

        # Resetar validação em mudanças críticas
        if changed_critical and cfg.validation_status == ValidationStatus.VALIDATED:
            cfg.validation_status = ValidationStatus.NEEDS_VALIDATION

        saved = await self.config_repo.save(cfg)
        logger.info(
            "fiscal_config_saved",
            extra={"extra_data": {
                "market_id": str(market_id),
                "environment": cfg.environment.value,
                "changed_critical": changed_critical,
                "is_new": is_new,
            }},
        )
        return saved

    async def validate_config(self, market_id: uuid.UUID) -> list[str]:
        """
        Valida a configuração para produção.
        Retorna lista de erros. Lista vazia = válido.
        """
        cfg = await self.config_repo.get_by_market(market_id)
        if not cfg:
            return ["Configuração fiscal não encontrada."]
        errors = cfg.validate_for_production()
        if not errors:
            cfg.validation_status = ValidationStatus.VALIDATED
            cfg.last_validated_at = datetime.utcnow()
        else:
            cfg.validation_status = ValidationStatus.FAILED
        await self.config_repo.save(cfg)
        return errors

    async def enable_fiscal(self, market_id: uuid.UUID) -> FiscalTenantConfig:
        """Ativa emissão fiscal. Exige validação prévia em produção."""
        cfg = await self.config_repo.get_by_market(market_id)
        if not cfg:
            raise BusinessRuleException("Configure o fiscal antes de ativar.")
        if cfg.environment == FiscalEnvironment.PRODUCTION:
            errors = cfg.validate_for_production()
            if errors:
                raise BusinessRuleException(f"Config inválida para produção: {'; '.join(errors[:3])}")
        cfg.enabled = True
        return await self.config_repo.save(cfg)

    def _decrypt(self, cfg: FiscalTenantConfig) -> FiscalTenantConfig:
        """Descriptografa segredos para uso interno no worker. Nunca retornar na API."""
        if cfg.certificate_password_ciphertext:
            cfg.certificate_password_ciphertext = self.cipher.decrypt(cfg.certificate_password_ciphertext)
        if cfg.csc_id_ciphertext:
            cfg.csc_id_ciphertext = self.cipher.decrypt(cfg.csc_id_ciphertext)
        if cfg.csc_token_ciphertext:
            cfg.csc_token_ciphertext = self.cipher.decrypt(cfg.csc_token_ciphertext)
        return cfg

    async def _store_certificate(
        self, market_id: uuid.UUID, cert_bytes: bytes, filename: str
    ) -> tuple[str, Optional[str], Optional[datetime]]:
        """
        Armazena certificado em storage privado.
        Retorna (storage_key, fingerprint, valid_until).

        Em MVP: armazena como-está e extrai fingerprint via cryptography lib se disponível.
        Produção: usar HSM ou KMS para envelope encryption.
        """
        import hashlib

        storage_key = f"fiscal/certs/{market_id}/{uuid.uuid4().hex}.pfx"
        self.storage.save(storage_key, cert_bytes)

        # Fingerprint básico (SHA-256 do conteúdo)
        fingerprint = hashlib.sha256(cert_bytes).hexdigest()

        # Tentar extrair validade via cryptography (opcional)
        valid_until = None
        try:
            from cryptography.hazmat.primitives.serialization import pkcs12
            p12 = pkcs12.load_pkcs12(cert_bytes, None)
            if p12.cert and p12.cert.certificate:
                valid_until = p12.cert.certificate.not_valid_after_utc.replace(tzinfo=None)
        except Exception:
            pass  # cryptography não instalado ou senha necessária

        return storage_key, fingerprint, valid_until
