"""JupyterLab and browser-IDE sessions: launch, status, idle timeout, recovery and stop.

HONEST SCOPE: these servers run the user's code with the operating-system privileges of the account that starts them, with no job isolation and no resource limits. They are therefore offered ONLY in
`local_trusted` mode (a single trusted person on their own machine, bound to 127.0.0.1). In any other mode the status is `unavailable` with the reason. Neither JupyterLab nor code-server is bundled; if the
command is not installed the status is `unavailable` too. Never report a session as running unless the process exists and the port answers."""
from __future__ import annotations

import hashlib
import os
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...core.clock import from_iso
from .common import DevService, dev_error, invalid, new_id, not_found, project_dir, quota, record_event

KINDS = ("notebook", "ide")
_CHILDREN: Dict[int, subprocess.Popen] = {}


def resolve_command(settings, kind: str) -> Optional[List[str]]:
    raw = settings.dev_jupyter_command if kind == "notebook" else settings.dev_ide_command
    if raw:
        parts = shlex.split(raw)
        return parts if parts and (shutil.which(parts[0]) or os.path.isfile(parts[0])) else None
    for cand in (("jupyter-lab",) if kind == "notebook" else ("code-server",)):
        if shutil.which(cand):
            return [cand]
    return None


def availability(settings, kind: str) -> Dict[str, Any]:
    if settings.dev_execution != "local_trusted":
        return {"available": False, "reason": "Notebook and IDE servers run unsandboxed, so they are only offered in local_trusted mode (one trusted person on their own machine). This installation is in '%s' mode." % settings.dev_execution}
    if resolve_command(settings, kind) is None:
        name = "JupyterLab (jupyter-lab)" if kind == "notebook" else "code-server"
        return {"available": False, "reason": f"{name} is not installed on this machine. Install it, or set PREDICTAI_DEV_{'JUPYTER' if kind == 'notebook' else 'IDE'}_COMMAND. Scripts and notebooks can still be run as jobs."}
    return {"available": True, "reason": None}


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _answers(port: Optional[int]) -> bool:
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill(pid: Optional[int]) -> None:
    if not pid:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        for _ in range(20):
            if not _alive(pid):
                return
            time.sleep(0.05)


def stop_all_for_user(conn, now: str, user_id: str, reason: str) -> int:
    n = 0
    for r in conn.execute("SELECT * FROM dv_sessions WHERE owner_user_id = ? AND status IN ('starting','running','stopping')", (user_id,)).fetchall():
        _kill(r["pid"])
        conn.execute("UPDATE dv_sessions SET status = 'stopped', stopped_at = ?, reason = ? WHERE id = ?", (now, reason, r["id"]))
        record_event(conn, now, r["project_id"], None, "session.stopped", "session", r["id"], "success", reason=reason)
        n += 1
    return n


def sweep(conn, clock) -> int:
    """Called by the worker: ends idle sessions and marks sessions whose process died, so the screen never shows a ghost 'running'."""
    now, n = clock.iso(), 0
    for r in conn.execute("SELECT * FROM dv_sessions WHERE status IN ('starting','running')").fetchall():
        idle = (clock.now() - from_iso(r["last_activity_at"] or r["started_at"])).total_seconds()
        if not _alive(r["pid"]):
            conn.execute("UPDATE dv_sessions SET status = 'failed', stopped_at = ?, reason = 'The server process ended unexpectedly. Start it again; your files are untouched.' WHERE id = ?", (now, r["id"]))
            record_event(conn, now, r["project_id"], None, "session.lost", "session", r["id"], "failure")
        elif idle > r["idle_timeout_s"]:
            _kill(r["pid"])
            conn.execute("UPDATE dv_sessions SET status = 'stopped', stopped_at = ?, reason = 'Stopped after being idle.' WHERE id = ?", (now, r["id"]))
            record_event(conn, now, r["project_id"], None, "session.idle_stopped", "session", r["id"])
        elif r["status"] == "starting" and _answers(r["port"]):
            conn.execute("UPDATE dv_sessions SET status = 'running' WHERE id = ?", (r["id"],))
        else:
            continue
        n += 1
    return n


def present(r) -> Dict[str, Any]:
    d = {k: r[k] for k in ("id", "project_id", "kind", "status", "reason", "idle_timeout_s", "last_activity_at", "started_at", "stopped_at")}
    d["url"] = f"http://127.0.0.1:{r['port']}/" if r["status"] in ("starting", "running") and r["port"] else None
    return d


class SessionService(DevService):
    def status(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        sweep(self.conn, self.clock)
        rows = self.conn.execute("SELECT * FROM dv_sessions WHERE project_id = ? ORDER BY started_at DESC LIMIT 10", (project["id"],)).fetchall()
        return {"availability": {k: availability(self.ctx.settings, k) for k in KINDS}, "sessions": [present(r) for r in rows], "idle_minutes": self.ctx.settings.dev_idle_minutes}

    def start(self, principal, project_id: Any, kind: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_session:user", principal)
        if kind not in KINDS:
            raise invalid("kind", "Choose notebook or ide.")
        s = self.ctx.settings
        av = availability(s, kind)
        if not av["available"]:
            self.event(project["id"], principal, "session.unavailable", "session", None, "denied", kind=kind)
            raise dev_error("SESSION_UNAVAILABLE", av["reason"], 409)
        sweep(self.conn, self.clock)
        if self.conn.execute("SELECT 1 FROM dv_sessions WHERE project_id = ? AND kind = ? AND status IN ('starting','running')", (project["id"], kind)).fetchone():
            raise dev_error("SESSION_EXISTS", "A session of this kind is already running for this project. Stop it first.", 409)
        if int(self.conn.execute("SELECT COUNT(*) FROM dv_sessions WHERE owner_user_id = ? AND status IN ('starting','running')", (principal.user_id,)).fetchone()[0]) >= s.dev_max_sessions:
            raise quota("SESSION_LIMIT", f"You can run {s.dev_max_sessions} sessions at once. Stop one first.")
        tree = project_dir(s, project["id"]) / "tree"
        tree.mkdir(parents=True, exist_ok=True)
        port, token = _free_port(), secrets.token_urlsafe(24)
        cmd = resolve_command(s, kind)
        if any("{port}" in c for c in cmd):
            argv = [c.replace("{port}", str(port)).replace("{token}", token).replace("{root}", str(tree)) for c in cmd]
        elif kind == "notebook":
            argv = cmd + ["--no-browser", "--ip=127.0.0.1", f"--port={port}", f"--ServerApp.token={token}", f"--ServerApp.root_dir={tree}", "--ServerApp.allow_remote_access=False"]
        else:
            argv = cmd + ["--bind-addr", f"127.0.0.1:{port}", "--auth", "password", "--disable-telemetry", str(tree)]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(project_dir(s, project["id"]) / "home"), "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1"}
        if kind == "ide":
            env["PASSWORD"] = token                    # code-server reads its password from the environment, never from a flag that `ps` would show
        Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.Popen(argv, cwd=str(tree), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            raise dev_error("SESSION_START_FAILED", "The server could not be started: " + type(e).__name__, 500)
        _CHILDREN[proc.pid] = proc
        sid, now = new_id(), self.now()
        self.conn.execute("INSERT INTO dv_sessions (id, project_id, owner_user_id, kind, status, pid, port, token_hash, command, idle_timeout_s, last_activity_at, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (sid, project["id"], principal.user_id, kind, "starting", proc.pid, port, hashlib.sha256(token.encode()).hexdigest(), os.path.basename(cmd[0]), s.dev_idle_minutes * 60, now, now))
        self.event(project["id"], principal, "session.started", "session", sid, kind=kind)
        for _ in range(60):                                    # wait up to ~3s for the port; the UI keeps polling after that
            if _answers(port) or not _alive(proc.pid):
                break
            time.sleep(0.05)
        sweep(self.conn, self.clock)
        out = present(self.conn.execute("SELECT * FROM dv_sessions WHERE id = ?", (sid,)).fetchone())
        out["access_token"] = token                           # shown exactly once; only its hash is stored
        out["token_note"] = "Shown once. It only works from this computer (127.0.0.1)."
        return out

    def touch(self, principal, project_id: Any, session_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        r = self._row(project["id"], session_id, principal)
        self.conn.execute("UPDATE dv_sessions SET last_activity_at = ? WHERE id = ? AND status IN ('starting','running')", (self.now(), r["id"]))
        return present(self.conn.execute("SELECT * FROM dv_sessions WHERE id = ?", (r["id"],)).fetchone())

    def stop(self, principal, project_id: Any, session_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        r = self._row(project["id"], session_id, principal)
        if r["status"] in ("starting", "running"):
            _kill(r["pid"])
            self.conn.execute("UPDATE dv_sessions SET status = 'stopped', stopped_at = ?, reason = 'Stopped by you.' WHERE id = ?", (self.now(), r["id"]))
            self.event(project["id"], principal, "session.stopped", "session", r["id"])
        return present(self.conn.execute("SELECT * FROM dv_sessions WHERE id = ?", (r["id"],)).fetchone())

    def _row(self, project_id: str, session_id: Any, principal):
        r = self.conn.execute("SELECT * FROM dv_sessions WHERE id = ? AND project_id = ?", (session_id if isinstance(session_id, str) else "", project_id)).fetchone()
        if r is None or (r["owner_user_id"] != principal.user_id):
            raise not_found("session")
        return r
