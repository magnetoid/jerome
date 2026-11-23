"""Encryption of secrets saved from the Settings page (Fernet, key from the environment)."""

from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

from jerome.config import Settings


class SecretUnreadable(Exception):
    """The stored ciphertext cannot be decrypted with the current key."""


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("JEROME_SECRET_KEY is not a valid Fernet key") from exc

    def encrypt(self, plain: str) -> bytes:
        return self._fernet.encrypt(plain.encode())

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken as exc:
            raise SecretUnreadable("secret cannot be decrypted with the current key") from exc


def secret_box(settings: Settings) -> SecretBox | None:
    """The box for the configured key, or None when it is unset, empty or malformed."""
    if settings.secret_key is None:
        return None
    key = settings.secret_key.get_secret_value().strip()
    if not key:
        return None
    try:
        return SecretBox(key)
    except ValueError:
        return None


def secret_storage_state(settings: Settings) -> Literal["ready", "missing", "invalid"]:
    if settings.secret_key is None or not settings.secret_key.get_secret_value().strip():
        return "missing"
    return "ready" if secret_box(settings) is not None else "invalid"
