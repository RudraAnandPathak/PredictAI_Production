"""All SQL for the identity module. No business rules here: callers decide what is allowed."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

Row = sqlite3.Row

_USER_FIELDS = {
    "email", "email_normalized", "display_name", "status", "verified_at", "blocked_at",
    "blocked_reason", "password_changed_at", "updated_at", "deleted_at",
}


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Row]:
        return self.conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: Sequence[Any] = ()) -> List[Row]:
        return self.conn.execute(sql, params).fetchall()

    # ---------------- users ----------------
    def create_user(self, *, id: str, email: str, email_normalized: str, display_name: Optional[str],
                    status: str, verified_at: Optional[str], now: str) -> None:
        self.conn.execute(
            "INSERT INTO users (id, email, email_normalized, display_name, status, verified_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id, email, email_normalized, display_name, status, verified_at, now, now),
        )

    def user_by_id(self, user_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM users WHERE id = ?", (user_id,))

    def user_by_email(self, normalized: str) -> Optional[Row]:
        return self._one("SELECT * FROM users WHERE email_normalized = ?", (normalized,))

    def update_user(self, user_id: str, **fields: Any) -> None:
        unknown = set(fields) - _USER_FIELDS
        if unknown:
            raise ValueError(f"Unknown user fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.conn.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*fields.values(), user_id))

    def bump_authz_version(self, user_id: str, now: str) -> None:
        self.conn.execute("UPDATE users SET authz_version = authz_version + 1, updated_at = ? WHERE id = ?", (now, user_id))

    def search_users(self, *, query: Optional[str], status: Optional[str], limit: int, offset: int) -> Tuple[List[Row], int]:
        where, params = ["status != 'deleted'"], []  # type: List[str], List[Any]
        if query:
            escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(email_normalized LIKE ? ESCAPE '\\' OR LOWER(COALESCE(display_name, '')) LIKE ? ESCAPE '\\')")
            params += [f"%{escaped}%", f"%{escaped}%"]
        if status:
            where.append("status = ?")
            params.append(status)
        clause = " AND ".join(where)
        total = self._one(f"SELECT COUNT(*) AS n FROM users WHERE {clause}", params)["n"]
        rows = self._all(f"SELECT * FROM users WHERE {clause} ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (*params, limit, offset))
        return rows, total

    # ---------------- identities ----------------
    def add_identity(self, *, id: str, user_id: str, provider: str, subject: str, password_hash: Optional[str],
                     verified: bool, now: str) -> None:
        self.conn.execute(
            "INSERT INTO identities (id, user_id, provider, provider_subject, password_hash, verified, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, user_id, provider, subject, password_hash, int(verified), now),
        )

    def identity(self, provider: str, subject: str) -> Optional[Row]:
        return self._one("SELECT * FROM identities WHERE provider = ? AND provider_subject = ?", (provider, subject))

    def identities_for_user(self, user_id: str) -> List[Row]:
        return self._all("SELECT * FROM identities WHERE user_id = ? ORDER BY created_at", (user_id,))

    def password_identity(self, user_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM identities WHERE user_id = ? AND provider = 'password'", (user_id,))

    def set_password_hash(self, user_id: str, password_hash: str) -> None:
        self.conn.execute("UPDATE identities SET password_hash = ? WHERE user_id = ? AND provider = 'password'", (password_hash, user_id))

    def mark_identities_verified(self, user_id: str) -> None:
        self.conn.execute("UPDATE identities SET verified = 1 WHERE user_id = ? AND provider = 'password'", (user_id,))

    def touch_identity(self, identity_id: str, now: str) -> None:
        self.conn.execute("UPDATE identities SET last_used_at = ? WHERE id = ?", (now, identity_id))

    def delete_identity(self, identity_id: str) -> None:
        self.conn.execute("DELETE FROM identities WHERE id = ?", (identity_id,))

    def delete_identities_for_user(self, user_id: str) -> None:
        self.conn.execute("DELETE FROM identities WHERE user_id = ?", (user_id,))

    # ---------------- sessions ----------------
    def create_session(self, *, id: str, token_hash: str, user_id: str, now: str, absolute_expires_at: str,
                       reauthenticated_at: str, rotated_from: Optional[str], persistent: bool, device_label: Optional[str]) -> None:
        self.conn.execute(
            "INSERT INTO sessions (id, token_hash, user_id, issued_at, last_seen_at, absolute_expires_at, "
            "reauthenticated_at, rotated_from, persistent, device_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, token_hash, user_id, now, now, absolute_expires_at, reauthenticated_at, rotated_from, int(persistent), device_label),
        )

    def session_by_token_hash(self, token_hash: str) -> Optional[Row]:
        return self._one("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))

    def session_by_id(self, session_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM sessions WHERE id = ?", (session_id,))

    def touch_session(self, session_id: str, now: str) -> None:
        self.conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now, session_id))

    def revoke_session(self, session_id: str, reason: str, now: str) -> bool:
        cur = self.conn.execute(
            "UPDATE sessions SET revoked_at = ?, revoked_reason = ? WHERE id = ? AND revoked_at IS NULL", (now, reason, session_id)
        )
        return cur.rowcount == 1

    def revoke_user_sessions(self, user_id: str, reason: str, now: str, except_id: Optional[str] = None) -> int:
        cur = self.conn.execute(
            "UPDATE sessions SET revoked_at = ?, revoked_reason = ? WHERE user_id = ? AND revoked_at IS NULL AND id != ?",
            (now, reason, user_id, except_id or ""),
        )
        return cur.rowcount

    def live_sessions(self, user_id: str, now: str) -> List[Row]:
        return self._all(
            "SELECT * FROM sessions WHERE user_id = ? AND revoked_at IS NULL AND absolute_expires_at > ? ORDER BY issued_at DESC",
            (user_id, now),
        )

    def delete_sessions_for_user(self, user_id: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # ---------------- roles ----------------
    def active_roles(self, user_id: str) -> Set[str]:
        return {r["role"] for r in self._all("SELECT role FROM user_roles WHERE user_id = ? AND revoked_at IS NULL", (user_id,))}

    def grant_role(self, *, id: str, user_id: str, role: str, granted_by: Optional[str], now: str) -> None:
        self.conn.execute(
            "INSERT INTO user_roles (id, user_id, role, granted_by, granted_at) VALUES (?, ?, ?, ?, ?)",
            (id, user_id, role, granted_by, now),
        )

    def revoke_role(self, user_id: str, role: str, revoked_by: Optional[str], now: str) -> bool:
        cur = self.conn.execute(
            "UPDATE user_roles SET revoked_at = ?, revoked_by = ? WHERE user_id = ? AND role = ? AND revoked_at IS NULL",
            (now, revoked_by, user_id, role),
        )
        return cur.rowcount == 1

    def active_owner_ids(self) -> List[str]:
        rows = self._all(
            "SELECT r.user_id FROM user_roles r JOIN users u ON u.id = r.user_id "
            "WHERE r.role = 'owner' AND r.revoked_at IS NULL AND u.status = 'active' ORDER BY r.granted_at"
        )
        return [r["user_id"] for r in rows]

    # ---------------- entitlements ----------------
    def active_entitlements(self, user_id: str) -> List[Row]:
        return self._all("SELECT * FROM entitlements WHERE user_id = ? AND revoked_at IS NULL ORDER BY activated_at", (user_id,))

    def entitlement_by_id(self, entitlement_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM entitlements WHERE id = ?", (entitlement_id,))

    def insert_entitlement(self, *, id: str, user_id: str, tier: str, source: str, access_code_id: Optional[str],
                           granted_by: Optional[str], now: str) -> None:
        self.conn.execute(
            "INSERT INTO entitlements (id, user_id, tier, source, access_code_id, granted_by, activated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, user_id, tier, source, access_code_id, granted_by, now),
        )

    def revoke_entitlement(self, entitlement_id: str, revoked_by: Optional[str], reason: Optional[str], now: str) -> bool:
        cur = self.conn.execute(
            "UPDATE entitlements SET revoked_at = ?, revoked_by = ?, revoked_reason = ? WHERE id = ? AND revoked_at IS NULL",
            (now, revoked_by, reason, entitlement_id),
        )
        return cur.rowcount == 1

    def revoke_entitlements_for_user(self, user_id: str, revoked_by: Optional[str], reason: str, now: str) -> None:
        self.conn.execute(
            "UPDATE entitlements SET revoked_at = ?, revoked_by = ?, revoked_reason = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, revoked_by, reason, user_id),
        )

    def revoke_roles_for_user(self, user_id: str, revoked_by: Optional[str], now: str) -> None:
        self.conn.execute(
            "UPDATE user_roles SET revoked_at = ?, revoked_by = ? WHERE user_id = ? AND revoked_at IS NULL", (now, revoked_by, user_id)
        )

    # ---------------- access codes ----------------
    def insert_code(self, *, id: str, code_hash: str, tier: str, label: Optional[str], created_by: str,
                    target_user_id: Optional[str], now: str, expires_at: str) -> None:
        self.conn.execute(
            "INSERT INTO access_codes (id, code_hash, tier, label, created_by, target_user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id, code_hash, tier, label, created_by, target_user_id, now, expires_at),
        )

    def code_by_hash(self, code_hash: str) -> Optional[Row]:
        return self._one("SELECT * FROM access_codes WHERE code_hash = ?", (code_hash,))

    def code_by_id(self, code_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM access_codes WHERE id = ?", (code_id,))

    def list_codes(self, limit: int) -> List[Row]:
        return self._all(
            "SELECT c.*, u.email AS used_by_email, t.email AS target_email FROM access_codes c "
            "LEFT JOIN users u ON u.id = c.used_by LEFT JOIN users t ON t.id = c.target_user_id "
            "ORDER BY c.created_at DESC LIMIT ?", (limit,),
        )

    def consume_code(self, code_id: str, user_id: str, now: str) -> bool:
        """Single-use redemption: succeeds for exactly one caller, even under concurrency."""
        cur = self.conn.execute(
            "UPDATE access_codes SET used_at = ?, used_by = ? "
            "WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL AND expires_at > ?",
            (now, user_id, code_id, now),
        )
        return cur.rowcount == 1

    def revoke_code(self, code_id: str, revoked_by: str, now: str) -> bool:
        cur = self.conn.execute(
            "UPDATE access_codes SET revoked_at = ?, revoked_by = ? WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL",
            (now, revoked_by, code_id),
        )
        return cur.rowcount == 1

    # ---------------- tokens ----------------
    def insert_token(self, *, id: str, purpose: str, token_hash: str, user_id: Optional[str], now: str,
                     expires_at: str, payload: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT INTO auth_tokens (id, purpose, token_hash, user_id, created_at, expires_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, purpose, token_hash, user_id, now, expires_at, payload),
        )

    def token_by_hash(self, purpose: str, token_hash: str) -> Optional[Row]:
        return self._one("SELECT * FROM auth_tokens WHERE purpose = ? AND token_hash = ?", (purpose, token_hash))

    def consume_token(self, token_id: str, now: str) -> bool:
        cur = self.conn.execute(
            "UPDATE auth_tokens SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL AND expires_at > ?", (now, token_id, now)
        )
        return cur.rowcount == 1

    def invalidate_tokens(self, user_id: str, purpose: str, now: str) -> None:
        self.conn.execute(
            "UPDATE auth_tokens SET consumed_at = ? WHERE user_id = ? AND purpose = ? AND consumed_at IS NULL", (now, user_id, purpose)
        )

    def delete_tokens_for_user(self, user_id: str) -> None:
        self.conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))

    # ---------------- audit ----------------
    def insert_audit(self, *, occurred_at: str, event_type: str, outcome: str, actor: Optional[str], target: Optional[str],
                     request_id: Optional[str], source_ip: Optional[str], reason: Optional[str], metadata: Optional[str]) -> None:
        self.conn.execute(
            "INSERT INTO audit_events (occurred_at, event_type, outcome, actor_user_id, target_user_id, request_id, source_ip, reason, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (occurred_at, event_type, outcome, actor, target, request_id, source_ip, reason, metadata),
        )

    def query_audit(self, *, event_type: Optional[str], outcome: Optional[str], actor: Optional[str], target: Optional[str],
                    since: Optional[str], until: Optional[str], before_id: Optional[int], limit: int) -> List[Row]:
        where, params = ["1 = 1"], []  # type: List[str], List[Any]
        for clause, value in (
            ("e.event_type LIKE ? ESCAPE '\\'", None if not event_type else event_type.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"),
            ("e.outcome = ?", outcome), ("e.actor_user_id = ?", actor), ("e.target_user_id = ?", target),
            ("e.occurred_at >= ?", since), ("e.occurred_at < ?", until), ("e.id < ?", before_id),
        ):
            if value is not None:
                where.append(clause)
                params.append(value)
        return self._all(
            "SELECT e.*, a.email AS actor_email, t.email AS target_email FROM audit_events e "
            "LEFT JOIN users a ON a.id = e.actor_user_id LEFT JOIN users t ON t.id = e.target_user_id "
            f"WHERE {' AND '.join(where)} ORDER BY e.id DESC LIMIT ?", (*params, limit),
        )

    def purge_audit(self, before: str) -> int:
        return self.conn.execute("DELETE FROM audit_events WHERE occurred_at < ?", (before,)).rowcount

    # ---------------- rate limiting / housekeeping ----------------
    def insert_rate_event(self, bucket: str, key_hash: str, now: str) -> None:
        self.conn.execute("INSERT INTO rate_limit_events (bucket, key_hash, occurred_at) VALUES (?, ?, ?)", (bucket, key_hash, now))

    def rate_events(self, bucket: str, key_hash: str, since: str) -> Tuple[int, Optional[str]]:
        row = self._one(
            "SELECT COUNT(*) AS n, MIN(occurred_at) AS oldest FROM rate_limit_events WHERE bucket = ? AND key_hash = ? AND occurred_at > ?",
            (bucket, key_hash, since),
        )
        return row["n"], row["oldest"]

    def purge_rate_events(self, before: str) -> int:
        return self.conn.execute("DELETE FROM rate_limit_events WHERE occurred_at < ?", (before,)).rowcount

    def purge_sessions(self, before: str) -> int:
        return self.conn.execute(
            "DELETE FROM sessions WHERE absolute_expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)", (before, before)
        ).rowcount

    def purge_tokens(self, before: str) -> int:
        return self.conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (before,)).rowcount
