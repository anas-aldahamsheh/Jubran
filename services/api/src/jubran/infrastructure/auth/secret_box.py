"""Server-side encryption for secrets the application must be able to read back.

Used for values that are never shown to customers but must remain recoverable
by an administrator, such as provider API keys and printable table QR tokens.

Keys come from ``DATA_ENCRYPTION_KEYS`` (comma-separated Fernet keys), separate
from ``SECRET_KEY`` so either can be changed on its own. The first key encrypts;
every key listed can still decrypt. To rotate: put a new key first, keep the old
one after it, and restart. The server re-encrypts what it stores with the new key
(see ``application/secret_rotation.py``); after that the old key can be removed.

Data stored before ``DATA_ENCRYPTION_KEYS`` existed was encrypted with a key
derived from ``SECRET_KEY``; that key stays readable as a last fallback.
"""
import base64
import hashlib
from functools import lru_cache
from typing import Tuple

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from jubran.settings import settings


def configured_keys(value: str) -> Tuple[str, ...]:
    return tuple(key.strip() for key in (value or "").split(",") if key.strip())


def _legacy_key(secret_key: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode("utf-8")).digest())


@lru_cache(maxsize=8)
def _ciphers(keys_setting: str, secret_key: str) -> Tuple[MultiFernet, Fernet]:
    """(every key for reading, the key new data is written with)."""
    keys = [Fernet(key) for key in configured_keys(keys_setting)]
    legacy = Fernet(_legacy_key(secret_key))
    return MultiFernet(keys + [legacy]), (keys[0] if keys else legacy)


def _current() -> Tuple[MultiFernet, Fernet]:
    return _ciphers(settings.DATA_ENCRYPTION_KEYS or "", settings.SECRET_KEY)


def encrypt_secret(raw: str) -> str:
    return _current()[1].encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _current()[0].decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("SECRET_DECRYPTION_FAILED") from exc


def uses_current_key(ciphertext: str) -> bool:
    try:
        _current()[1].decrypt(ciphertext.encode("ascii"))
        return True
    except InvalidToken:
        return False


def reencrypt_secret(ciphertext: str) -> str:
    """The same secret, encrypted with the current key (ValueError if no key can read it)."""
    return encrypt_secret(decrypt_secret(ciphertext))
