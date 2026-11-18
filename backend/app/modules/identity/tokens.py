"""Random tokens, access codes, CSRF tokens and keyed hashes (all from vetted primitives)."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid

# Crockford base32 without I, L, O, U: easy to read and type, 5 bits per character.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_PREFIX = "PAI"
CODE_GROUPS = 4
CODE_GROUP_LEN = 5  # 20 random characters = 100 bits


def new_id() -> str:
    return uuid.uuid4().hex


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 is appropriate here: tokens are long random values, not human passwords."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_access_code() -> str:
    groups = ["".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_GROUP_LEN)) for _ in range(CODE_GROUPS)]
    return "-".join([CODE_PREFIX] + groups)


def normalize_access_code(raw: str) -> str:
    """Case, spacing and hyphens do not matter; O/I/L are read as 0/1/1 (Crockford)."""
    value = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    return value.replace("O", "0").replace("I", "1").replace("L", "1")


def hash_access_code(raw: str) -> str:
    return hash_token(normalize_access_code(raw))


def csrf_token(secret: bytes, session_id: str) -> str:
    return hmac.new(secret, f"csrf:{session_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def keyed_hash(secret: bytes, purpose: str, value: str) -> str:
    """Stable pseudonym for rate-limit keys, so raw emails and IPs are never stored."""
    return hmac.new(secret, f"{purpose}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
