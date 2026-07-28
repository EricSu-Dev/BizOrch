"""Versioned scrypt password hashing using only Python's standard library."""

import base64
import hashlib
import hmac
import os


class PasswordHasher:
    """Hash and verify passwords without ever storing reversible secrets."""

    ALGORITHM = "scrypt"

    def __init__(
        self,
        *,
        n: int = 2**14,
        r: int = 8,
        p: int = 1,
        salt_bytes: int = 16,
        key_bytes: int = 32,
    ) -> None:
        self._n = n
        self._r = r
        self._p = p
        self._salt_bytes = salt_bytes
        self._key_bytes = key_bytes

    def hash(self, password: str) -> str:
        """Return a self-describing hash string with a random salt."""
        password_bytes = self._password_bytes(password)
        salt = os.urandom(self._salt_bytes)
        digest = self._derive(password_bytes, salt)
        return "$".join(
            (
                self.ALGORITHM,
                str(self._n),
                str(self._r),
                str(self._p),
                self._encode(salt),
                self._encode(digest),
            )
        )

    def verify(self, password: str, encoded: str) -> bool:
        """Verify a password and return false for malformed stored values."""
        try:
            algorithm, n, r, p, salt, expected = encoded.split("$")
            if algorithm != self.ALGORITHM:
                return False
            password_bytes = self._password_bytes(password)
            salt_bytes = self._decode(salt)
            expected_bytes = self._decode(expected)
            actual = hashlib.scrypt(
                password_bytes,
                salt=salt_bytes,
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(expected_bytes),
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected_bytes)

    def _derive(self, password: bytes, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password,
            salt=salt,
            n=self._n,
            r=self._r,
            p=self._p,
            dklen=self._key_bytes,
        )

    @staticmethod
    def _password_bytes(password: str) -> bytes:
        if len(password) < 8:
            raise ValueError("password must contain at least 8 characters")
        encoded = password.encode("utf-8")
        if len(encoded) > 1024:
            raise ValueError("password is too long")
        return encoded

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
