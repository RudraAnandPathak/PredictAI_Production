"""Password hashing with scrypt (RFC 7914) from the Python standard library.

Encoded form: ``scrypt$N$r$p$salt$hash`` (base64). Parameters travel with every hash,
so they can be raised later and old hashes are upgraded at the next successful sign-in.
Passwords are NFKC-normalised first so visually identical input verifies identically.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import unicodedata
from typing import Optional, Tuple

_SALT_BYTES = 16
_KEY_BYTES = 32


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    secret = unicodedata.normalize("NFKC", password).encode("utf-8")
    maxmem = 128 * n * r + 128 * r * p + (8 << 20)
    return hashlib.scrypt(secret, salt=salt, n=n, r=r, p=p, maxmem=maxmem, dklen=_KEY_BYTES)


class PasswordHasher:
    def __init__(self, n: int = 2**15, r: int = 8, p: int = 3) -> None:
        self.params: Tuple[int, int, int] = (n, r, p)
        self._dummy: Optional[str] = None

    def hash(self, password: str) -> str:
        n, r, p = self.params
        salt = os.urandom(_SALT_BYTES)
        return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(_derive(password, salt, n, r, p))}"

    @staticmethod
    def _parse(encoded: str) -> Optional[Tuple[int, int, int, bytes, bytes]]:
        try:
            scheme, n, r, p, salt, digest = encoded.split("$")
            if scheme != "scrypt":
                return None
            return int(n), int(r), int(p), base64.b64decode(salt, validate=True), base64.b64decode(digest, validate=True)
        except (ValueError, AttributeError):
            return None

    def verify(self, password: str, encoded: Optional[str]) -> bool:
        parsed = self._parse(encoded or "")
        if parsed is None:
            self.burn(password)  # keep timing similar for malformed or missing hashes
            return False
        n, r, p, salt, expected = parsed
        try:
            actual = _derive(password, salt, n, r, p)
        except (ValueError, MemoryError):
            return False
        return hmac.compare_digest(actual, expected)

    def needs_rehash(self, encoded: str) -> bool:
        parsed = self._parse(encoded or "")
        return parsed is None or parsed[:3] != self.params

    def burn(self, password: str) -> None:
        """Spend the time of one verification (used when no account exists)."""
        if self._dummy is None:
            self._dummy = self.hash(secrets.token_urlsafe(12))
        self.verify(password, self._dummy)
