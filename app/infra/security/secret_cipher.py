import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    def __init__(self, key: Optional[str]):
        if not key:
            self._fernet = None
            return

        digest = hashlib.sha256(key.encode("utf-8")).digest()
        fernet_key = base64.urlsafe_b64encode(digest)
        self._fernet = Fernet(fernet_key)

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return value
        if not self._fernet:
            return value
        if self.is_encrypted(value):
            return value
        token = self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")
        return f"enc:{token}"

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return value
        if not self._fernet or not self.is_encrypted(value):
            return value
        try:
            return self._fernet.decrypt(value[4:].encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return value

    @staticmethod
    def is_encrypted(value: str) -> bool:
        return isinstance(value, str) and value.startswith("enc:")
