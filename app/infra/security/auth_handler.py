import hashlib
from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
from infra.config.settings import get_settings

settings = get_settings()

# Configuração do Bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def pre_hash_password(password: str) -> str:
    """
    Converte qualquer senha em um hash SHA-256 de 64 caracteres.
    Isso contorna o limite de 72 bytes do Bcrypt de forma segura.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

class AuthHandler:
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        # A senha plana deve passar pelo mesmo pré-hash antes de verificar
        pre_hashed = pre_hash_password(plain_password)
        return pwd_context.verify(pre_hashed, hashed_password)

    @staticmethod
    def get_password_hash(password: str) -> str:
        # A senha é reduzida para 64 bytes via SHA-256
        pre_hashed = pre_hash_password(password)
        # O Bcrypt encripta o hash SHA-256, não a senha original
        return pwd_context.hash(pre_hashed)

    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return encoded_jwt

    @staticmethod
    def decode_token(token: str) -> dict:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            return payload
        except JWTError:
            return None