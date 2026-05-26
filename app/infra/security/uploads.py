import uuid
from pathlib import Path
from typing import Optional


MAX_PFX_UPLOAD_BYTES = 2 * 1024 * 1024
ALLOWED_PFX_CONTENT_TYPES = {
    "application/octet-stream",
    "application/x-pkcs12",
    "application/pkcs12",
    "application/x-pkcs-12",
}


def validate_pfx_upload(
    filename: str,
    content_type: Optional[str],
    size_bytes: int,
    market_id: Optional[uuid.UUID] = None,
) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix != ".pfx":
        raise ValueError("O certificado deve ser um arquivo .pfx")

    if size_bytes <= 0:
        raise ValueError("O certificado enviado está vazio.")

    if size_bytes > MAX_PFX_UPLOAD_BYTES:
        raise ValueError("O certificado deve ter no máximo 2 MB.")

    if content_type and content_type not in ALLOWED_PFX_CONTENT_TYPES:
        raise ValueError("Tipo de arquivo de certificado inválido.")

    prefix = str(market_id) if market_id else "certificate"
    return f"{prefix}_{uuid.uuid4().hex}.pfx"
