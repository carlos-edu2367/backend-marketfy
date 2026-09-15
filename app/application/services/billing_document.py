"""Documento (CPF/CNPJ) usado na cobrança, sem expor o número completo na API."""

import re
from typing import Any, Optional, Sequence


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _oldest_market_document(markets: Optional[Sequence[Any]]) -> Optional[str]:
    if not markets:
        return None
    oldest = min(markets, key=lambda m: m.created_at)
    digits = _digits(getattr(oldest, "document", None))
    return digits or None


def registered_document(user: Any, markets: Optional[Sequence[Any]] = None) -> Optional[str]:
    """CPF cadastrado do usuário; se ausente, cai para o documento da loja mais antiga."""
    cpf = getattr(user, "cpf", None)
    digits = _digits(getattr(cpf, "value", cpf))
    if len(digits) == 11:
        return digits
    return _oldest_market_document(markets)


def mask_document(digits: Optional[str]) -> Optional[str]:
    if not digits:
        return None
    if len(digits) == 11:
        return f"***.{digits[3:6]}.{digits[6:9]}-**"
    if len(digits) == 14:
        return f"**.{digits[2:5]}.{digits[5:8]}/****-**"
    return None


def resolve_billing_document(
    provided: Optional[str], user: Any, markets: Optional[Sequence[Any]] = None
) -> Optional[str]:
    """Documento digitado no checkout; se ausente, o documento cadastrado (CPF ou loja)."""
    return _digits(provided) or registered_document(user, markets=markets)
