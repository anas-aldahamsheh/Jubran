"""Password hashing unit tests."""
from jubran.infrastructure.auth.passwords import hash_password, verify_password


def test_password_hash_and_verify():
    password = "SuperSecretPassword123"
    hashed = hash_password(password)
    assert hashed != password
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, password) is True
    assert verify_password(hashed, "WrongPassword") is False
