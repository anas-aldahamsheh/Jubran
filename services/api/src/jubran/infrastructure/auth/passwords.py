"""Password hashing utilities using Argon2id."""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash plaintext password using Argon2id."""
    return ph.hash(password)


def verify_password(hash_value: str, password: str) -> bool:
    """Verify plaintext password against Argon2id hash."""
    try:
        return ph.verify(hash_value, password)
    except (VerifyMismatchError, VerificationError):
        return False
