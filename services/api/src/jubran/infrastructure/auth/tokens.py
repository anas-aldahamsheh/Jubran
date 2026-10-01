"""Opaque tokens (sessions, QR codes, confirmations) are stored only as their SHA-256 hash."""
import hashlib


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
