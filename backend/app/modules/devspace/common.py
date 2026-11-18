"""Shared plumbing: permission gate, paths, events, redaction, hashing."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ...core.errors import AppError
from ..identity.authorization import Principal
from ..identity.constants import Perm
from ..p7 import Service7, clean, invalid, new_id, not_found  # noqa: F401  (re-exported for the devspace modules)
from ..workspace.errors import conflict, quota  # noqa: F401

BLOCKED_NAMES = re.compile(r"(^|/)(\.env(\..*)?|id_rsa.*|id_ed25519.*|.*\.pem|.*\.key|\.netrc|\.pypirc)$|(^|/)(\.git|\.ssh|\.aws)(/|$)", re.I)
SEGMENT = re.compile(r"^[A-Za-z0-9._\- ]{1,100}$")
SECRET_KEY_HINT = re.compile(r"(secret|token|password|passwd|credential|api[_-]?key|authorization)", re.I)
TOKEN_PATTERNS = (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}\b"),
                  re.compile(r"\bAKIA[0-9A-Z]{16}\b"), re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"))
MAX_FILE_BYTES = 1024 * 1024
KINDS = {".py": "code", ".ipynb": "notebook", ".txt": "text", ".md": "text", ".json": "config", ".yaml": "config", ".yml": "config", ".toml": "config", ".cfg": "config", ".ini": "config",
         ".csv": "text", ".sql": "code", ".sh": "code", ".r": "code"}


def dev_error(code: str, message: str, status: int = 403) -> AppError:
    return AppError(code, message, status)


def dev_root(settings) -> Path:
    return Path(settings.dev_root or (Path(settings.storage_dir) / "dev"))


def project_dir(settings, project_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,64}", project_id or ""):
        raise not_found("project")
    return dev_root(settings) / project_id


def put_object_at(settings, project_id: str, data: bytes) -> str:
    sha = sha256_bytes(data)
    target = project_dir(settings, project_id) / "objects" / sha[:2] / sha
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return sha


def get_object_at(settings, project_id: str, sha: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        raise not_found("version")
    p = project_dir(settings, project_id) / "objects" / sha[:2] / sha
    if not p.is_file():
        raise not_found("version")
    data = p.read_bytes()
    if sha256_bytes(data) != sha:
        raise dev_error("OBJECT_CORRUPT", "A stored object failed its checksum and was not returned.", 500)
    return data


def env_python_path(settings, project_id: str, env_id: str, version: int) -> Path:
    return project_dir(settings, project_id) / "envs" / env_id / f"v{int(version)}" / "venv" / "bin" / "python"


def safe_path(raw: Any, *, allow_blocked: bool = False) -> str:
    """A project-relative path or a typed validation error. Never absolute, never above the tree, never a secret-bearing name."""
    if not isinstance(raw, str) or not raw or len(raw) > 300 or "\x00" in raw or "\\" in raw or any(ord(c) < 32 for c in raw):
        raise invalid("path", "Use a relative path such as src/train.py.")
    if raw.startswith("/") or raw.startswith("~"):
        raise invalid("path", "Paths must be relative to the project.")
    parts = raw.split("/")
    if len(parts) > 12 or any(p in ("", ".", "..") or not SEGMENT.match(p) for p in parts):
        raise invalid("path", "Use letters, digits, space, dot, dash and underscore in folder and file names, without '..'.")
    if not allow_blocked and BLOCKED_NAMES.search(raw):
        raise invalid("path", "Files that usually hold credentials (.env, keys, .git) cannot be stored in a project. Use a secret reference instead.")
    return raw


def kind_of(path: str) -> str:
    return KINDS.get(os.path.splitext(path)[1].lower(), "other")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contains_secret_pattern(text: str) -> bool:
    return any(p.search(text) for p in TOKEN_PATTERNS)


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    for value in secrets:
        if value and len(value) >= 4:
            text = text.replace(value, "***")
    for p in TOKEN_PATTERNS:
        text = p.sub("***", text)
    return text


def clean_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in detail.items():
        if SECRET_KEY_HINT.search(k):
            out[k] = "***"
        elif isinstance(v, str):
            out[k] = redact_text(v)[:200]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (list, dict)):
            out[k] = json.loads(redact_text(json.dumps(v, default=str))[:2000]) if len(json.dumps(v, default=str)) <= 2000 else "(omitted: too large)"
        else:
            out[k] = str(v)[:200]
    return out


def record_event(conn: sqlite3.Connection, now: str, project_id: str, actor: Optional[str], action: str, target_type: Optional[str] = None, target_id: Optional[str] = None,
                 outcome: str = "success", **detail: Any) -> None:
    """History/audit row: identifiers, counts, states. Never file content, never a secret value."""
    conn.execute("INSERT INTO dv_events (project_id, actor_user_id, action, target_type, target_id, outcome, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (project_id, actor, action, target_type, target_id, outcome, json.dumps(clean_detail(detail), sort_keys=True), now))


def has_dev_access(principal: Principal) -> bool:
    return principal.can(Perm.WORKSPACE_DEVELOPMENT) and principal.status == "active"


class DevService(Service7):
    """Base: the gate that every Development Workspace operation passes, evaluated from the current request's principal (recomputed from the database each request)."""

    def gate(self, principal: Principal) -> None:
        if not has_dev_access(principal):
            self.ctx.audit.record("dev.access.denied", "denied", actor=principal.user_id, reason="not_special")
            raise dev_error("SPECIAL_ACCESS_REQUIRED", "The Development Workspace needs Special access. Activate a code in Account > Special access, or ask the platform owner.")

    def open_project(self, principal: Principal, project_id: Any, *, writable: bool = False):
        self.gate(principal)
        row = self.project(principal, project_id, writable=writable)
        ws = self.conn.execute("SELECT * FROM dv_workspaces WHERE project_id = ?", (row["id"],)).fetchone()
        now = self.now()
        if ws is None:
            if writable:       # created on first write; reads of a project that was never opened just show an empty workspace
                self.conn.execute("INSERT OR IGNORE INTO dv_workspaces (id, project_id, owner_user_id, status, created_at, updated_at) VALUES (?,?,?,?,?,?)", (new_id(), row["id"], row["owner_user_id"], "active", now, now))
                record_event(self.conn, now, row["id"], principal.user_id, "workspace.created", "workspace", row["id"])
        elif ws["status"] == "suspended":
            # the gate above just proved the person is entitled again, so a workspace suspended by a revocation comes back by itself
            self.conn.execute("UPDATE dv_workspaces SET status = 'active', suspended_reason = NULL, updated_at = ? WHERE project_id = ?", (now, row["id"]))
            record_event(self.conn, now, row["id"], principal.user_id, "workspace.reactivated", "workspace", row["id"])
            ws = None
        if ws is not None and ws["status"] == "suspended" and writable:
            raise dev_error("WORKSPACE_SUSPENDED", "This development workspace is suspended: " + (ws["suspended_reason"] or "access was revoked") + ". Your files are preserved.", 409)
        return row

    def event(self, project_id: str, principal: Optional[Principal], action: str, target_type: Optional[str] = None, target_id: Optional[str] = None, outcome: str = "success", **detail: Any) -> None:
        record_event(self.conn, self.now(), project_id, principal.user_id if principal else None, action, target_type, target_id, outcome, **detail)

    def paths(self, project_id: str) -> Path:
        return project_dir(self.ctx.settings, project_id)
