import pytest

from app.auth.passwords import PasswordHasher


def test_scrypt_hash_uses_random_salt_and_verifies() -> None:
    hasher = PasswordHasher(n=2**10)

    first = hasher.hash("correct-password")
    second = hasher.hash("correct-password")

    assert first.startswith("scrypt$")
    assert first != second
    assert "correct-password" not in first
    assert hasher.verify("correct-password", first)
    assert not hasher.verify("wrong-password", first)


def test_password_policy_rejects_short_password() -> None:
    with pytest.raises(ValueError, match="at least 8"):
        PasswordHasher(n=2**10).hash("short")


def test_malformed_hash_fails_closed() -> None:
    assert not PasswordHasher(n=2**10).verify("correct-password", "not-a-hash")
