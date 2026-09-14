"""Documento (CPF) usado na cobrança, sem expor o número completo na API."""

import re
from typing import Any, Optional


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def registered_document(user: Any) -> Optional[str]:
    """CPF cadastrado do usuário, só dígitos. UserModel não persiste CNPJ hoje."""
    cpf = getattr(user, "cpf", None)
    digits = _digits(getattr(cpf, "value", cpf))
    return digits if len(digits) == 11 else None


def mask_document(digits: Optional[str]) -> Optional[str]:
    if not digits or len(digits) != 11:
        return None
    return f"***.{digits[3:6]}.{digits[6:9]}-**"


def resolve_billing_document(provided: Optional[str], user: Any) -> Optional[str]:
    """Documento digitado no checkout; se ausente, o CPF do cadastro."""
    return _digits(provided) or registered_document(user)
