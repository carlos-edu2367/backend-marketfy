import uuid
import re
from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# =============================================================================
# EXCEÇÕES DO DOMÍNIO
# =============================================================================

class DomainException(Exception):
    """Classe base para todas as exceções de regra de negócio."""
    pass

class ValidationException(DomainException):
    """Erro de validação de dados fundamentais (Value Objects)."""
    pass

class BusinessRuleException(DomainException):
    """Violação de regra de negócio (ex: estoque negativo, caixa fechado)."""
    pass

# =============================================================================
# VALUE OBJECTS
# =============================================================================

@dataclass(frozen=True)
class CPF:
    value: str

    def __post_init__(self):
        # Remove caracteres não numéricos
        clean_value = re.sub(r'\D', '', self.value)
        # Em produção, aqui entra o algoritmo de validação de digito verificador
        if len(clean_value) != 11:
            raise ValidationException(f"CPF inválido: {self.value}")
        # Truque para 'congelar' o valor limpo no dataclass frozen
        object.__setattr__(self, 'value', clean_value)

    def __str__(self):
        return f"{self.value[:3]}.{self.value[3:6]}.{self.value[6:9]}-{self.value[9:]}"

def is_valid_cpf(value: str) -> bool:
    """Valida CPF pelos dígitos verificadores (rejeita sequências repetidas como 111.111.111-11)."""
    digits = re.sub(r'\D', '', value or '')
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(d) * w for d, w in zip(digits[:size], range(size + 1, 1, -1)))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


@dataclass(frozen=True)
class CNPJ:
    value: str

    def __post_init__(self):
        clean_value = re.sub(r'\D', '', self.value)
        if len(clean_value) != 14:
            raise ValidationException(f"CNPJ inválido: {self.value}")
        object.__setattr__(self, 'value', clean_value)

    def __str__(self):
        return f"{self.value[:2]}.{self.value[2:5]}.{self.value[5:8]}/{self.value[8:12]}-{self.value[12:]}"

@dataclass(frozen=True)
class Email:
    value: str

    def __post_init__(self):
        if "@" not in self.value:
            raise ValidationException(f"Email inválido: {self.value}")

# =============================================================================
# BASE ENTITY
# =============================================================================

@dataclass(kw_only=True)
class Entity(ABC):
    """
    Classe base para todas as entidades.
    Garante identidade única (UUID), rastreabilidade temporal e Versionamento para Sync.
    """
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    # CORREÇÃO: Usar datetime.utcnow() para gerar datas 'naive' (sem timezone)
    # Isso evita o erro de "can't subtract offset-naive and offset-aware datetimes" no AsyncPG
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    version: int = 1  # Crucial para Optimistic Locking e Sync Offline

    def update_timestamp(self):
        self.updated_at = datetime.utcnow()
        self.increment_version()

    def increment_version(self):
        self.version += 1