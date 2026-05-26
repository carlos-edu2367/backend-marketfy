"""
FiscalArtifactStorage — armazenamento privado de XML/PDF fiscais.

Backends suportados:
- S3-compatible (Railway Object Storage, MinIO, AWS S3) — ativado automaticamente
  quando S3_ENDPOINT_URL, S3_ACCESS_KEY_ID e S3_SECRET_ACCESS_KEY estão configurados.
- Filesystem local — fallback para desenvolvimento e ambientes sem S3.

Organização de chaves:
    fiscal/{environment}/{market_id}/{document_id}/{artifact_type}.{ext}

Segurança:
- Nunca expor arquivo via URL pública direta.
- URLs assinadas com TTL curto (S3 presigned URLs ou HMAC local).
- Isolamento por tenant via prefixo market_id na storage_key.
- SHA-256 registrado para integridade de cada artefato.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Optional

from infra.config.logger import get_logger
from infra.config.settings import get_settings

logger = get_logger("fiscal_storage")

_SIGN_SECRET: Optional[bytes] = None


def _get_sign_secret() -> bytes:
    global _SIGN_SECRET
    if _SIGN_SECRET is None:
        settings = get_settings()
        key = settings.FISCAL_SECRET_KEY or settings.SECRET_KEY
        _SIGN_SECRET = key.encode()[:32]
    return _SIGN_SECRET


# =============================================================================
# S3 BACKEND
# =============================================================================

class _S3Backend:
    """Armazenamento em bucket S3-compatible (Railway Object Storage / MinIO / AWS)."""

    def __init__(self, settings):
        self._endpoint = settings.S3_ENDPOINT_URL
        self._access_key = settings.S3_ACCESS_KEY_ID
        self._secret_key = settings.S3_SECRET_ACCESS_KEY
        self._bucket = settings.S3_BUCKET_NAME
        self._region = settings.S3_REGION

    def _get_client(self):
        import boto3
        return boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        )

    def _ensure_bucket(self):
        """Cria o bucket se não existir (idempotente)."""
        try:
            client = self._get_client()
            existing = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
            if self._bucket not in existing:
                client.create_bucket(Bucket=self._bucket)
                logger.info("s3_bucket_created", extra={"extra_data": {"bucket": self._bucket}})
        except Exception as exc:
            logger.warning("s3_bucket_check_failed", extra={"extra_data": {"error": str(exc)}})

    async def save(self, storage_key: str, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        metadata = {"sha256": sha, "content-length": str(len(content))}

        def _upload():
            client = self._get_client()
            client.put_object(
                Bucket=self._bucket,
                Key=storage_key,
                Body=content,
                Metadata=metadata,
                ServerSideEncryption="AES256",
            )

        await asyncio.to_thread(_upload)
        logger.info(
            "fiscal_artifact_saved_s3",
            extra={"extra_data": {
                "storage_key": storage_key,
                "size": len(content),
                "sha256": sha[:8],
                "bucket": self._bucket,
            }},
        )
        return sha

    async def load(self, storage_key: str) -> Optional[bytes]:
        def _download():
            try:
                client = self._get_client()
                resp = client.get_object(Bucket=self._bucket, Key=storage_key)
                return resp["Body"].read()
            except Exception:
                return None

        return await asyncio.to_thread(_download)

    async def exists(self, storage_key: str) -> bool:
        def _head():
            try:
                client = self._get_client()
                client.head_object(Bucket=self._bucket, Key=storage_key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_head)

    async def delete(self, storage_key: str) -> bool:
        def _delete():
            try:
                client = self._get_client()
                client.delete_object(Bucket=self._bucket, Key=storage_key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_delete)

    def generate_signed_url(self, storage_key: str, expires_in: int = 300) -> str:
        """Gera presigned URL S3 com TTL."""
        try:
            client = self._get_client()
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": storage_key},
                ExpiresIn=expires_in,
            )
            return url
        except Exception as exc:
            logger.error("s3_presigned_url_failed", extra={"extra_data": {"error": str(exc)}})
            # Fallback para URL assinada interna
            return _hmac_signed_url(storage_key, expires_in)

    def validate_signed_url(self, storage_key: str, expires_at: int, sig: str) -> bool:
        # Para S3, presigned URLs são validadas pelo próprio S3.
        # Este método só é chamado quando usamos o fallback HMAC local.
        return _validate_hmac_url(storage_key, expires_at, sig)


# =============================================================================
# FILESYSTEM BACKEND (local/dev)
# =============================================================================

class _FilesystemBackend:
    """Armazenamento local (filesystem) — para dev/test sem S3."""

    def __init__(self, storage_root: Path):
        self._root = storage_root
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, storage_key: str) -> Path:
        resolved = (self._root / storage_key).resolve()
        if not str(resolved).startswith(str(self._root)):
            raise ValueError(f"Storage key inválido: {storage_key}")
        return resolved

    async def save(self, storage_key: str, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        path = self._resolve_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)
        logger.info(
            "fiscal_artifact_saved_local",
            extra={"extra_data": {"storage_key": storage_key, "size": len(content), "sha256": sha[:8]}},
        )
        return sha

    async def load(self, storage_key: str) -> Optional[bytes]:
        path = self._resolve_path(storage_key)
        if not path.exists():
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, storage_key: str) -> bool:
        return self._resolve_path(storage_key).exists()

    async def delete(self, storage_key: str) -> bool:
        path = self._resolve_path(storage_key)
        if path.exists():
            path.unlink()
            return True
        return False

    def generate_signed_url(self, storage_key: str, expires_in: int = 300) -> str:
        return _hmac_signed_url(storage_key, expires_in)

    def validate_signed_url(self, storage_key: str, expires_at: int, sig: str) -> bool:
        return _validate_hmac_url(storage_key, expires_at, sig)


# =============================================================================
# HMAC URL HELPERS (compartilhados)
# =============================================================================

def _hmac_signed_url(storage_key: str, expires_in: int) -> str:
    expires_at = int(time.time()) + expires_in
    msg = f"{storage_key}:{expires_at}".encode()
    sig = hmac.new(_get_sign_secret(), msg, hashlib.sha256).hexdigest()[:20]
    return f"/fiscal/artifacts/download?key={storage_key}&exp={expires_at}&sig={sig}"


def _validate_hmac_url(storage_key: str, expires_at: int, sig: str) -> bool:
    if int(time.time()) > expires_at:
        return False
    msg = f"{storage_key}:{expires_at}".encode()
    expected = hmac.new(_get_sign_secret(), msg, hashlib.sha256).hexdigest()[:20]
    return hmac.compare_digest(expected, sig)


# =============================================================================
# FACHADA PÚBLICA
# =============================================================================

class FiscalArtifactStorage:
    """
    Fachada única para armazenamento de artefatos fiscais.

    Seleciona automaticamente o backend:
    - S3 quando S3_ENDPOINT_URL + credenciais estiverem configurados.
    - Filesystem local como fallback.
    """

    def __init__(self, storage_root: Optional[Path] = None):
        settings = get_settings()
        if settings.s3_configured:
            self._backend = _S3Backend(settings)
            logger.info("fiscal_storage_backend", extra={"extra_data": {"backend": "s3", "bucket": settings.S3_BUCKET_NAME}})
        else:
            root = storage_root or Path(settings.FISCAL_STORAGE_ROOT).resolve()
            self._backend = _FilesystemBackend(root)
            logger.info("fiscal_storage_backend", extra={"extra_data": {"backend": "filesystem", "root": str(root)}})

    @staticmethod
    def build_storage_key(
        environment: str,
        market_id: uuid.UUID,
        document_id: uuid.UUID,
        artifact_type: str,
        ext: str,
    ) -> str:
        """
        Constrói chave de storage isolada por tenant e data.

        Formato: fiscal/{environment}/{market_id}/{document_id}/{artifact_type}.{ext}
        Exemplo:  fiscal/homologacao/abc-123/def-456/xml_authorized.xml
        """
        return f"fiscal/{environment}/{market_id}/{document_id}/{artifact_type}.{ext}"

    async def save(self, storage_key: str, content: bytes) -> str:
        """Salva conteúdo e retorna SHA-256 hex."""
        return await self._backend.save(storage_key, content)

    async def load(self, storage_key: str) -> Optional[bytes]:
        """Carrega conteúdo por storage_key. Retorna None se não existir."""
        return await self._backend.load(storage_key)

    async def exists(self, storage_key: str) -> bool:
        return await self._backend.exists(storage_key)

    async def delete(self, storage_key: str) -> bool:
        return await self._backend.delete(storage_key)

    def generate_signed_url(self, storage_key: str, expires_in: int = 300) -> str:
        """Gera URL assinada com TTL para download seguro."""
        return self._backend.generate_signed_url(storage_key, expires_in)

    def validate_signed_url(self, storage_key: str, expires_at: int, sig: str) -> bool:
        """Valida URL assinada HMAC (usado pelo router de download)."""
        return self._backend.validate_signed_url(storage_key, expires_at, sig)


# Singleton para uso direto (compatibilidade com código existente)
fiscal_artifact_storage = FiscalArtifactStorage()
