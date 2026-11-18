"""Secret references. A secret is a NAME the project may use; its value is encrypted at rest (Fernet, key derived from the server secret), is never returned by any API,
is never written to project files, logs or events, and reaches a job only as an environment variable of a job that named it. Revocation takes effect on the next job."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any, Dict, List

from .common import DevService, dev_error, invalid, new_id, not_found, conflict

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,59}$")
RESERVED = {"PATH", "HOME", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "TMPDIR", "SHELL", "USER", "PREDICTAI_OUTPUT_DIR", "PREDICTAI_DATA_DIR"}
PURPOSES = ("job", "git", "webhook")


def _fernet(secret_key: bytes):
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    okm = hmac.new(secret_key, b"predictai-dev-secrets-v1", hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(okm))


def available(settings) -> bool:
    return _fernet(settings.secret_key or b"x") is not None


def encrypt(settings, value: str) -> str:
    f = _fernet(settings.secret_key)
    if f is None:
        raise dev_error("SECRETS_UNAVAILABLE", "Secret storage needs the 'cryptography' package (pip install -r requirements-devspace.txt).", 503)
    return f.encrypt(value.encode()).decode()


def decrypt(settings, token: str) -> str:
    f = _fernet(settings.secret_key)
    if f is None:
        raise dev_error("SECRETS_UNAVAILABLE", "Secret storage needs the 'cryptography' package.", 503)
    return f.decrypt(token.encode()).decode()


def resolve_values(conn, settings, now: str, project_id: str, names: List[str], purposes=("job",)) -> Dict[str, str]:
    """Internal only: name -> value for live (non-revoked) secrets of THIS project. Marks use; never logs the value."""
    out: Dict[str, str] = {}
    for n in names:
        row = conn.execute("SELECT id, ciphertext, purpose FROM dv_secrets WHERE project_id = ? AND name = ? AND revoked_at IS NULL", (project_id, n)).fetchone()
        if row is None or row["purpose"] not in purposes:
            raise not_found("secret")
        out[n] = decrypt(settings, row["ciphertext"])
        conn.execute("UPDATE dv_secrets SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    return out


class SecretService(DevService):
    def create(self, principal, project_id: Any, name: Any, value: Any, purpose: Any = "job") -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_write:user", principal)
        if not isinstance(name, str) or not NAME.match(name) or name.upper() in RESERVED or name.upper().startswith("PREDICTAI_"):
            raise invalid("name", "Use letters, digits and underscores, starting with a letter (not a reserved name).")
        if purpose not in PURPOSES:
            raise invalid("purpose", "Choose job, git or webhook.")
        if not isinstance(value, str) or not 1 <= len(value) <= 4096:
            raise invalid("value", "Enter a value of up to 4096 characters.")
        token = encrypt(self.ctx.settings, value)
        sid = new_id()
        try:
            self.conn.execute("INSERT INTO dv_secrets (id, project_id, owner_user_id, name, purpose, ciphertext, created_at) VALUES (?,?,?,?,?,?,?)", (sid, project["id"], principal.user_id, name, purpose, token, self.now()))
        except Exception:  # noqa: BLE001 - unique index
            raise conflict("SECRET_EXISTS", "A secret with that name already exists in this project. Revoke it first to replace it.") from None
        self.event(project["id"], principal, "secret.created", "secret", sid, name=name, purpose=purpose)
        return self._present(self.conn.execute("SELECT * FROM dv_secrets WHERE id = ?", (sid,)).fetchone())

    def list(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        rows = self.conn.execute("SELECT * FROM dv_secrets WHERE project_id = ? AND owner_user_id = ? ORDER BY created_at DESC", (project["id"], principal.user_id)).fetchall()
        return {"items": [self._present(r) for r in rows], "storage_available": available(self.ctx.settings)}

    def revoke(self, principal, project_id: Any, secret_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        row = self.conn.execute("SELECT * FROM dv_secrets WHERE id = ? AND project_id = ? AND owner_user_id = ?", (secret_id if isinstance(secret_id, str) else "", project["id"], principal.user_id)).fetchone()
        if row is None:
            raise not_found("secret")
        self.conn.execute("UPDATE dv_secrets SET revoked_at = ?, ciphertext = '' WHERE id = ?", (self.now(), row["id"]))
        self.event(project["id"], principal, "secret.revoked", "secret", row["id"], name=row["name"])
        return self._present(self.conn.execute("SELECT * FROM dv_secrets WHERE id = ?", (row["id"],)).fetchone())

    @staticmethod
    def _present(r) -> Dict[str, Any]:
        return {"id": r["id"], "name": r["name"], "purpose": r["purpose"], "created_at": r["created_at"], "last_used_at": r["last_used_at"], "revoked": r["revoked_at"] is not None}
