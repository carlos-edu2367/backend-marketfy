from functools import lru_cache
from typing import Any, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Aplicacao
    APP_NAME: str = "SGM Marketfy"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    DEBUG: bool = False

    # Banco de dados
    DATABASE_URL: str

    # Redis (cache, rate limit distribuido e filas futuras)
    REDIS_URL: str = "redis://localhost:6379/0"

    # Seguranca (JWT e sessao)
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    REFRESH_TOKEN_COOKIE_NAME: str = "marketfy_refresh"
    SECURE_COOKIES: Optional[bool] = None
    COOKIE_SAMESITE: str = "lax"

    # CORS e URLs publicas
    BACKEND_CORS_ORIGINS: list[str] | str = ["http://localhost:3000", "https://app.sgmmarketfy.com"]
    PUBLIC_FRONTEND_URL: Optional[str] = None
    PUBLIC_API_BASE_URL: Optional[str] = None

    # Endpoints internos/operacionais
    METRICS_ACCESS_TOKEN: Optional[str] = None

    # Fiscal
    FISCAL_SECRET_KEY: Optional[str] = None
    FISCAL_PROVIDER: str = "neectify_fiscal"    # neectify_fiscal | fake
    FISCAL_WORKER_CONCURRENCY: int = 5
    FISCAL_JOB_TIMEOUT: int = 30
    FISCAL_STORAGE_ROOT: str = "storage/fiscal"

    # Neectify Fiscal
    NEECTIFY_API_KEY: str = ""
    NEECTIFY_BASE_URL: str = "https://api.neectify.com"
    NEECTIFY_TIMEOUT_SECONDS: int = 30

    # Mercado Pago
    MP_ACCESS_TOKEN: str = ""
    MP_WEBHOOK_SECRET: str = ""
    MP_SANDBOX: bool = True
    MP_BASE_URL: str = "https://api.mercadopago.com"
    FISCAL_CREDITS_BACK_URL_SUCCESS: str = ""
    FISCAL_CREDITS_BACK_URL_FAILURE: str = ""
    FISCAL_CREDITS_BACK_URL_PENDING: str = ""

    # S3 / Object Storage (Railway S3-compatible)
    S3_ENDPOINT_URL: Optional[str] = None       # Railway: RAILWAY_BUCKET_ENDPOINT_URL
    S3_ACCESS_KEY_ID: Optional[str] = None      # Railway: RAILWAY_BUCKET_ACCESS_KEY_ID
    S3_SECRET_ACCESS_KEY: Optional[str] = None  # Railway: RAILWAY_BUCKET_SECRET_ACCESS_KEY
    S3_BUCKET_NAME: str = "marketfy-fiscal"     # Railway: RAILWAY_BUCKET_NAME
    S3_REGION: str = "auto"                     # Railway: RAILWAY_BUCKET_REGION

    @property
    def s3_configured(self) -> bool:
        return bool(
            self.S3_ENDPOINT_URL
            and self.S3_ACCESS_KEY_ID
            and self.S3_SECRET_ACCESS_KEY
        )

    # Rate limit inicial em memoria para desenvolvimento/teste; producao exige Redis.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_BACKEND: str = "memory"

    # Billing Core (Fase 4)
    # Registrar o Marketfy em INTERNAL_API_CLIENTS no Billing Core com esta API key.
    # ALLOWED_INTERNAL_WEBHOOK_HOSTS deve incluir BILLING_CORE_WEBHOOK_HOST.
    BILLING_CORE_BASE_URL: Optional[str] = None
    BILLING_CORE_API_KEY: Optional[str] = None
    BILLING_CORE_SYSTEM: str = "marketfy"
    BILLING_CORE_WEBHOOK_HOST: Optional[str] = None
    BILLING_CORE_TIMEOUT_SECONDS: int = 10
    BILLING_CORE_ENABLED: bool = False

    class Config:
        env_file = ".env"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_staging(self) -> bool:
        return self.ENVIRONMENT == "staging"

    @property
    def cookie_secure(self) -> bool:
        if self.SECURE_COOKIES is not None:
            return self.SECURE_COOKIES
        return self.is_production or self.is_staging

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def normalize_environment(cls, value: Any) -> str:
        normalized = str(value or "development").strip().lower()
        aliases = {"dev": "development", "prod": "production", "release": "production", "stage": "staging"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"development", "staging", "production", "test"}:
            raise ValueError("ENVIRONMENT deve ser development, staging, production ou test.")
        return normalized

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug_value(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None or value == "":
            return False
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
            return True
        if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
            return False
        return bool(value)

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value_str = value.strip()
            # Clean outer quotes if any (e.g. '"[...]"' from docker-compose / dotenv mismatch)
            if value_str.startswith('"') and value_str.endswith('"'):
                value_str = value_str[1:-1].strip()
            
            # Check if it looks like a JSON array/list
            if value_str.startswith('[') and value_str.endswith(']'):
                import json
                try:
                    parsed = json.loads(value_str)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed]
                except Exception:
                    pass
            
            # Otherwise, split by comma
            return [origin.strip() for origin in value_str.split(",") if origin.strip()]
        return value

    @field_validator("COOKIE_SAMESITE", mode="before")
    @classmethod
    def normalize_samesite(cls, value: Any) -> str:
        normalized = str(value or "lax").strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE deve ser lax, strict ou none.")
        return normalized

    @model_validator(mode="after")
    def validate_production_configuration(self):
        if not self.is_production:
            return self

        if self.DEBUG:
            raise ValueError("DEBUG nao pode estar ativo em producao.")
        if not self.BACKEND_CORS_ORIGINS or "*" in self.BACKEND_CORS_ORIGINS:
            raise ValueError("BACKEND_CORS_ORIGINS nao pode conter wildcard em producao.")
        if any(not origin.startswith("https://") for origin in self.BACKEND_CORS_ORIGINS):
            raise ValueError("BACKEND_CORS_ORIGINS deve usar HTTPS em producao.")
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 32:
            raise ValueError("SECRET_KEY precisa ter pelo menos 32 caracteres em producao.")
        if self.ACCESS_TOKEN_EXPIRE_MINUTES > 30:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES deve ser no maximo 30 em producao.")
        if not self.FISCAL_SECRET_KEY or len(self.FISCAL_SECRET_KEY) < 32:
            raise ValueError("FISCAL_SECRET_KEY e obrigatoria em producao.")
        if self.FISCAL_PROVIDER == "neectify_fiscal" and not self.NEECTIFY_API_KEY:
            raise ValueError("NEECTIFY_API_KEY e obrigatoria em producao com FISCAL_PROVIDER=neectify_fiscal.")
        if not self.MP_ACCESS_TOKEN or not self.MP_WEBHOOK_SECRET:
            raise ValueError("MP_ACCESS_TOKEN e MP_WEBHOOK_SECRET sao obrigatorios em producao.")
        if not self.PUBLIC_FRONTEND_URL or not self.PUBLIC_FRONTEND_URL.startswith("https://"):
            raise ValueError("PUBLIC_FRONTEND_URL HTTPS e obrigatoria em producao.")
        if not self.PUBLIC_API_BASE_URL or not self.PUBLIC_API_BASE_URL.startswith("https://"):
            raise ValueError("PUBLIC_API_BASE_URL HTTPS e obrigatoria em producao.")
        if not self.METRICS_ACCESS_TOKEN or len(self.METRICS_ACCESS_TOKEN) < 32:
            raise ValueError("METRICS_ACCESS_TOKEN e obrigatoria em producao.")
        if self.RATE_LIMIT_BACKEND != "redis":
            raise ValueError("RATE_LIMIT_BACKEND=redis e obrigatorio em producao.")
        if not self.BILLING_CORE_ENABLED:
            raise ValueError("BILLING_CORE_ENABLED=True e obrigatorio em producao.")
        if not self.BILLING_CORE_BASE_URL or not self.BILLING_CORE_API_KEY or not self.BILLING_CORE_WEBHOOK_HOST:
            raise ValueError("Billing Core habilitado exige URL, API key e webhook host.")
        return self


@lru_cache()
def get_settings():
    return Settings()
