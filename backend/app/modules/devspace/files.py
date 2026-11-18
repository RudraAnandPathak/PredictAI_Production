"""Project working tree: real files on disk (so Git, environments, notebooks and jobs all see the same thing), an index with versions and checksums in the database, and a
content-addressed object store so every saved version can be restored. Only this module writes the tree on behalf of a person."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...db import transaction
from .common import MAX_FILE_BYTES, get_object_at, put_object_at, DevService, conflict, contains_secret_pattern, dev_error, invalid, kind_of, new_id, not_found, quota, safe_path, sha256_bytes

SKIP_DIRS = {".git", "__pycache__", ".ipynb_checkpoints", "node_modules"}


class FileService(DevService):
    # ------------------------------------------------------------------ locations
    def tree(self, project_id: str) -> Path:
        d = self.paths(project_id) / "tree"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def objects(self, project_id: str) -> Path:
        d = self.paths(project_id) / "objects"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def put_object(self, project_id: str, data: bytes) -> str:
        return put_object_at(self.ctx.settings, project_id, data)

    def get_object(self, project_id: str, sha: str) -> bytes:
        return get_object_at(self.ctx.settings, project_id, sha)

    def resolve(self, project_id: str, rel: str) -> Path:
        """Absolute path inside the tree for a validated relative path; refuses anything that escapes it (symlinks included)."""
        root = self.tree(project_id).resolve()
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            raise invalid("path", "That path is outside the project.")
        return target

    # ------------------------------------------------------------------ quota
    def used_bytes(self, project_id: str) -> int:
        return int(self.conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM dv_files WHERE project_id = ? AND deleted_at IS NULL", (project_id,)).fetchone()[0])

    def storage_limit(self, project_id: str, owner_id: str) -> int:
        from .compute import effective_limit
        return effective_limit(self.conn, self.ctx.settings, owner_id, project_id, "project_storage_mb") * 1024 * 1024

    # ------------------------------------------------------------------ operations
    def list(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        rows = self.conn.execute("SELECT * FROM dv_files WHERE project_id = ? AND deleted_at IS NULL ORDER BY path", (project["id"],)).fetchall()
        used, limit = self.used_bytes(project["id"]), self.storage_limit(project["id"], project["owner_user_id"])
        return {"items": [self._present(r) for r in rows], "storage": {"used_bytes": used, "limit_bytes": limit, "remaining_bytes": max(0, limit - used)}}

    def read(self, principal, project_id: Any, path: Any, version: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        rel = safe_path(path, allow_blocked=True)
        row = self.conn.execute("SELECT * FROM dv_files WHERE project_id = ? AND path = ? AND deleted_at IS NULL", (project["id"], rel)).fetchone()
        if row is None:
            raise not_found("file")
        sha = row["sha256"]
        v = row["version"]
        if version is not None:
            vr = self.conn.execute("SELECT * FROM dv_file_versions WHERE file_id = ? AND version = ?", (row["id"], version if isinstance(version, int) else -1)).fetchone()
            if vr is None:
                raise not_found("version")
            sha, v = vr["sha256"], vr["version"]
        data = self.get_object(project["id"], sha)
        return {**self._present(row), "version": v, "sha256": sha, "content": data.decode("utf-8", "replace")}

    def write(self, principal, project_id: Any, path: Any, content: Any, expected_version: Any = None, *, system: bool = False) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_write:user", principal)
        rel = safe_path(path)
        if not isinstance(content, str):
            raise invalid("content", "File content must be text.")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise quota("FILE_TOO_LARGE", f"A project file can be up to {MAX_FILE_BYTES // 1024} KB. Keep large data in the Data Workspace and read it from the data folder.")
        if contains_secret_pattern(content):
            raise invalid("content", "This looks like it contains a credential (a private key or access token). Remove it and use a secret reference instead.")
        return self._store(project, principal.user_id, rel, data, expected_version)

    def _store(self, project, actor: str, rel: str, data: bytes, expected_version: Optional[int]) -> Dict[str, Any]:
        pid = project["id"]
        now = self.now()
        sha = self.put_object(pid, data)
        with transaction(self.conn):
            row = self.conn.execute("SELECT * FROM dv_files WHERE project_id = ? AND path = ? AND deleted_at IS NULL", (pid, rel)).fetchone()
            if row is not None and expected_version is not None and row["version"] != expected_version:
                raise conflict("EDIT_CONFLICT", f"This file changed (now version {row['version']}). Reload it before saving so no work is overwritten.")
            old = row["bytes"] if row is not None else 0
            if self.used_bytes(pid) - old + len(data) > self.storage_limit(pid, project["owner_user_id"]):
                self.event(pid, None, "file.quota_denied", "file", rel, "denied", bytes=len(data))
                raise quota("STORAGE_QUOTA_EXCEEDED", "This write would exceed the project's storage quota. Delete files or ask the owner to raise the limit.")
            if row is None:
                fid, version = new_id(), 1
                self.conn.execute("INSERT INTO dv_files (id, project_id, owner_user_id, path, kind, version, sha256, bytes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                  (fid, pid, project["owner_user_id"], rel, kind_of(rel), 1, sha, len(data), now, now))
            else:
                fid, version = row["id"], row["version"] + (0 if row["sha256"] == sha else 1)
                if row["sha256"] != sha:
                    self.conn.execute("UPDATE dv_files SET version = ?, sha256 = ?, bytes = ?, updated_at = ? WHERE id = ?", (version, sha, len(data), now, fid))
            if row is None or row["sha256"] != sha:
                self.conn.execute("INSERT INTO dv_file_versions (file_id, version, sha256, bytes, author_user_id, created_at) VALUES (?,?,?,?,?,?)", (fid, version, sha, len(data), actor, now))
        target = self.resolve(pid, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        self.conn.execute("INSERT INTO dv_events (project_id, actor_user_id, action, target_type, target_id, outcome, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (pid, actor, "file.saved", "file", rel, "success", f'{{"version": {version}, "bytes": {len(data)}}}', now))
        return self._present(self.conn.execute("SELECT * FROM dv_files WHERE id = ?", (fid,)).fetchone())

    def restore(self, principal, project_id: Any, path: Any, version: Any) -> Dict[str, Any]:
        old = self.read(principal, project_id, path, version)
        return self.write(principal, project_id, path, old["content"])

    def delete(self, principal, project_id: Any, path: Any, confirm: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        rel = safe_path(path, allow_blocked=True)
        if confirm != rel:
            raise dev_error("CONFIRMATION_REQUIRED", "Deleting a file needs confirmation: send the exact path as 'confirm'. Earlier versions stay restorable.", 409)
        row = self.conn.execute("SELECT * FROM dv_files WHERE project_id = ? AND path = ? AND deleted_at IS NULL AND owner_user_id = ?", (project["id"], rel, principal.user_id)).fetchone()
        if row is None:
            raise not_found("file")
        self.conn.execute("UPDATE dv_files SET deleted_at = ?, updated_at = ? WHERE id = ?", (self.now(), self.now(), row["id"]))
        try:
            self.resolve(project["id"], rel).unlink()
        except FileNotFoundError:
            pass
        self.event(project["id"], principal, "file.deleted", "file", rel)
        return {"deleted": True, "path": rel, "restorable": True}

    def history(self, principal, project_id: Any, path: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        rel = safe_path(path, allow_blocked=True)
        row = self.conn.execute("SELECT * FROM dv_files WHERE project_id = ? AND path = ? ORDER BY (deleted_at IS NULL) DESC, updated_at DESC", (project["id"], rel)).fetchone()
        if row is None:
            raise not_found("file")
        vs = self.conn.execute("SELECT version, sha256, bytes, author_user_id, created_at FROM dv_file_versions WHERE file_id = ? ORDER BY version DESC", (row["id"],)).fetchall()
        return {"path": rel, "deleted": row["deleted_at"] is not None, "versions": [dict(v) for v in vs]}

    def search(self, principal, project_id: Any, query: Any, limit: int = 50) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        if not isinstance(query, str) or not 1 <= len(query) <= 100:
            raise invalid("query", "Enter 1 to 100 characters to search for.")
        hits: List[Dict[str, Any]] = []
        for row in self.conn.execute("SELECT * FROM dv_files WHERE project_id = ? AND deleted_at IS NULL AND bytes <= ? ORDER BY path", (project["id"], MAX_FILE_BYTES)).fetchall():
            try:
                text = self.resolve(project["id"], row["path"]).read_text("utf-8", "replace")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    hits.append({"path": row["path"], "line": n, "text": line.strip()[:200]})
                    if len(hits) >= limit:
                        return {"items": hits, "truncated": True}
        return {"items": hits, "truncated": False}

    # ------------------------------------------------------------------ reconciliation with the tree (after git, jobs or notebooks changed files)
    def rescan(self, project_id: str, actor: Optional[str] = None) -> Dict[str, int]:
        project = self.conn.execute("SELECT id, owner_user_id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project is None:
            return {"added": 0, "changed": 0, "removed": 0}
        root = self.tree(project_id)
        seen, counts = set(), {"added": 0, "changed": 0, "removed": 0}
        now = self.now()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d))]
            for name in filenames:
                full = Path(dirpath) / name
                rel = full.relative_to(root).as_posix()
                try:
                    safe_path(rel, allow_blocked=False)
                    if full.is_symlink() or full.stat().st_size > MAX_FILE_BYTES:
                        continue
                    data = full.read_bytes()
                except Exception:  # noqa: BLE001 - names we do not index (blocked names, odd characters) are left alone
                    continue
                seen.add(rel)
                sha = self.put_object(project_id, data)
                row = self.conn.execute("SELECT * FROM dv_files WHERE project_id = ? AND path = ? AND deleted_at IS NULL", (project_id, rel)).fetchone()
                if row is None:
                    fid = new_id()
                    self.conn.execute("INSERT INTO dv_files (id, project_id, owner_user_id, path, kind, version, sha256, bytes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                      (fid, project_id, project["owner_user_id"], rel, kind_of(rel), 1, sha, len(data), now, now))
                    self.conn.execute("INSERT INTO dv_file_versions (file_id, version, sha256, bytes, author_user_id, created_at) VALUES (?,?,?,?,?,?)", (fid, 1, sha, len(data), actor or project["owner_user_id"], now))
                    counts["added"] += 1
                elif row["sha256"] != sha:
                    v = row["version"] + 1
                    self.conn.execute("UPDATE dv_files SET version = ?, sha256 = ?, bytes = ?, updated_at = ? WHERE id = ?", (v, sha, len(data), now, row["id"]))
                    self.conn.execute("INSERT INTO dv_file_versions (file_id, version, sha256, bytes, author_user_id, created_at) VALUES (?,?,?,?,?,?)", (row["id"], v, sha, len(data), actor or project["owner_user_id"], now))
                    counts["changed"] += 1
        for row in self.conn.execute("SELECT id, path FROM dv_files WHERE project_id = ? AND deleted_at IS NULL", (project_id,)).fetchall():
            if row["path"] not in seen:
                self.conn.execute("UPDATE dv_files SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
                counts["removed"] += 1
        return counts

    def tree_hash(self, project_id: str) -> str:
        rows = self.conn.execute("SELECT path, sha256 FROM dv_files WHERE project_id = ? AND deleted_at IS NULL ORDER BY path", (project_id,)).fetchall()
        h = hashlib.sha256()
        for r in rows:
            h.update(f"{r['path']}\0{r['sha256']}\n".encode())
        return h.hexdigest()

    @staticmethod
    def _present(r) -> Dict[str, Any]:
        return {"id": r["id"], "path": r["path"], "kind": r["kind"], "version": r["version"], "sha256": r["sha256"], "bytes": r["bytes"], "updated_at": r["updated_at"]}
