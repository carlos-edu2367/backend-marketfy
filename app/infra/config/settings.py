from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional

class Settings(BaseSettings):
    # Aplicação
    APP_NAME: str = "SGM Marketfy"
    API_V1_STR: str = "/api/v1"
    
    DEBUG: bool
    # Banco de Dados
    DATABASE_URL: str 
    
    # Redis (Cache & Filas)
    REDIS_URL: str = "redis://localhost:6379/0" # Em produção: redis://:senha@host:port/db

    # Segurança (JWT)
    SECRET_KEY: str 
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440 # 24 horas

    # CORS
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000", "https://app.sgmmarketfy.com"]

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    return Settings()