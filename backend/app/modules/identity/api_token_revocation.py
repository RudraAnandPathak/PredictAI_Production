"""Revoke every personal API token of a person (password change or reset, account blocked, owner recovery). No other imports, so the
identity services can use it without import cycles."""
from typing import Any


def revoke_user_tokens(conn: Any, user_id: str, reason: str, now: str) -> int:
    return conn.execute("UPDATE api_tokens SET revoked_at = ?, revoked_reason = ? WHERE user_id = ? AND revoked_at IS NULL", (now, reason, user_id)).rowcount
