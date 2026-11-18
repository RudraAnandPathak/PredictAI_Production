"""Per-project reproducible Python environments.

A version is immutable: changing requirements or rebuilding creates a new one. Packages come from wheels only (--only-binary=:all:), so nothing runs setup scripts while installing, and only from the
index URLs the owner allowed (PREDICTAI_DEV_PIP_INDEX_URLS) or from a local wheelhouse (PREDICTAI_DEV_WHEELHOUSE, which also lets a build work with no internet at all).
Builds run as env_build jobs in the worker, never in the API process. NOTE: a build needs network access to the package index, so it is NOT run inside the no-network isolation; it is only
available in `local_trusted`/`docker` execution modes and is documented as a trusted-operator action (see docs/phase9)."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import DevService, contains_secret_pattern, dev_error, env_python_path, invalid, new_id, not_found, project_dir, quota, record_event, redact_text
from .compute import effective_limit

MAX_PACKAGES = 100
MAX_ENVS = 5
REQ_LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,80})(?P<extras>\[[A-Za-z0-9,._-]{1,60}\])?\s*(?P<spec>(?:(?:==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9.*+!_-]{1,40}\s*,?\s*){0,3})$")
PY = ".".join(map(str, sys.version_info[:2]))


def parse_requirements(text: Any) -> List[Tuple[str, str]]:
    """Validated (name, spec) pairs. Anything that could fetch from somewhere else or run code is rejected with a reason: URLs, VCS, local paths, -r/-e/--index-url options, hashes of unknown form."""
    if not isinstance(text, str) or len(text) > 20000:
        raise invalid("requirements", "Give the requirements as text (at most 20,000 characters).")
    out, seen = [], set()
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-") or "://" in line or "@" in line or line.startswith((".", "/", "~")) or "git+" in line or ";" in line:
            raise invalid("requirements", f"Line {no}: only plain package names with optional version limits are allowed (no URLs, files, options or markers).")
        m = REQ_LINE.match(line)
        if not m:
            raise invalid("requirements", f"Line {no}: could not read '{line[:60]}'. Use the form name==1.2.3 or name>=1.0,<2.")
        key = re.sub(r"[-_.]+", "-", m["name"]).lower()
        if key in seen:
            raise invalid("requirements", f"Line {no}: {m['name']} is listed twice.")
        seen.add(key)
        out.append((m["name"] + (m["extras"] or ""), re.sub(r"\s+", "", m["spec"]).rstrip(",")))
    if len(out) > MAX_PACKAGES:
        raise invalid("requirements", f"At most {MAX_PACKAGES} packages per environment.")
    return out


def canonical(pairs: List[Tuple[str, str]]) -> str:
    return "".join(f"{n}{s}\n" for n, s in sorted(pairs, key=lambda p: p[0].lower()))


def index_urls(settings) -> List[str]:
    return [u.strip() for u in settings.dev_pip_index_urls.split(",") if u.strip().startswith("https://")]


def wheelhouse() -> Optional[str]:
    w = os.environ.get("PREDICTAI_DEV_WHEELHOUSE", "").strip()
    return w if w and Path(w).is_dir() else None


def present(row, ver=None) -> Dict[str, Any]:
    d = {"id": row["id"], "name": row["name"], "python_version": row["python_version"], "platform": row["platform"], "current_version": row["current_version"], "is_default": bool(row["is_default"]),
         "created_at": row["created_at"]}
    if ver is not None:
        d["latest"] = version_dict(ver)
    return d


def version_dict(v, full: bool = False) -> Dict[str, Any]:
    d = {"version": v["version"], "status": v["status"], "manifest_sha256": v["manifest_sha256"], "error": v["error"], "created_at": v["created_at"], "report": json.loads(v["report"])}
    if full:
        d["requirements"] = v["requirements"]
        d["lockfile"] = v["lockfile"]
        d["env_refs"] = json.loads(v["env_refs"])
    return d


class EnvService(DevService):
    def _env(self, project_id: str, env_id: Any):
        row = self.conn.execute("SELECT * FROM dv_envs WHERE id = ? AND project_id = ? AND deleted_at IS NULL", (env_id if isinstance(env_id, str) else "", project_id)).fetchone()
        if row is None:
            raise not_found("environment")
        return row

    def _latest(self, env_id: str):
        return self.conn.execute("SELECT * FROM dv_env_versions WHERE env_id = ? ORDER BY version DESC LIMIT 1", (env_id,)).fetchone()

    def _build_allowed(self) -> None:
        if self.ctx.settings.dev_execution == "off":
            raise dev_error("EXECUTION_DISABLED", "Code execution, and so environment building, is switched off on this installation.", 403)
        if not wheelhouse() and not index_urls(self.ctx.settings):
            raise dev_error("NO_PACKAGE_SOURCE", "No approved package source is configured (PREDICTAI_DEV_PIP_INDEX_URLS or PREDICTAI_DEV_WHEELHOUSE).", 409)

    def _new_version(self, principal, project, env, text: str, refs: List[str]) -> Dict[str, Any]:
        pairs = parse_requirements(text)
        manifest = canonical(pairs)
        n = int(self.conn.execute("SELECT COALESCE(MAX(version),0) FROM dv_env_versions WHERE env_id = ?", (env["id"],)).fetchone()[0]) + 1
        now = self.now()
        self.conn.execute("INSERT INTO dv_env_versions (id, env_id, version, requirements, manifest_sha256, env_refs, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (new_id(), env["id"], n, manifest, hashlib.sha256(manifest.encode()).hexdigest(), json.dumps(refs), "building", principal.user_id, now))
        self.limit("dev_env_build:user", principal)
        from .engine import JobService
        js = JobService(self.ctx, self.ws)
        js.create_job(project, principal.user_id, kind="env_build", entry=None, argv=[str(n)], env_id=env["id"], network="package_index", priority=50,
                      limits=js._limits(project, {"wall_seconds": 600}, "env_build") | {"wall_seconds": 600, "memory_mb": max(js._limits(project, None, "env_build")["memory_mb"], 1024)}, actor=principal.user_id)
        self.event(project["id"], principal, "env.build_queued", "env", env["id"], version=n, packages=len(pairs))
        return {"version": n, "status": "building", "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest()}

    def create(self, principal, project_id: Any, *, name: Any = None, requirements: Any = "", python_version: Any = None, env_refs: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_write:user", principal)
        from .common import clean
        name = clean(name, "name", maximum=60)
        if python_version not in (None, PY):
            raise invalid("python_version", f"Only Python {PY} (the worker's interpreter) is available here.")
        self._build_allowed()
        if int(self.conn.execute("SELECT COUNT(*) FROM dv_envs WHERE project_id = ? AND deleted_at IS NULL", (project["id"],)).fetchone()[0]) >= MAX_ENVS:
            raise quota("ENV_LIMIT", f"A project can have {MAX_ENVS} environments. Remove one first.")
        if self.conn.execute("SELECT 1 FROM dv_envs WHERE project_id = ? AND name = ? COLLATE NOCASE AND deleted_at IS NULL", (project["id"], name)).fetchone():
            raise dev_error("CONFLICT", "An environment with that name already exists.", 409)
        refs = self._refs(project["id"], env_refs)
        eid, now = new_id(), self.now()
        first = int(self.conn.execute("SELECT COUNT(*) FROM dv_envs WHERE project_id = ? AND deleted_at IS NULL", (project["id"],)).fetchone()[0]) == 0
        self.conn.execute("INSERT INTO dv_envs (id, project_id, owner_user_id, name, python_version, platform, current_version, is_default, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (eid, project["id"], project["owner_user_id"], name, PY, sys.platform, 0, int(first), now))
        env = self._env(project["id"], eid)
        self._new_version(principal, project, env, requirements, refs)
        self.event(project["id"], principal, "env.created", "env", eid, name=name)
        return self.get(principal, project["id"], eid)

    def _refs(self, project_id: str, refs: Any) -> List[str]:
        refs = refs or []
        if not isinstance(refs, list) or len(refs) > 10:
            raise invalid("env_refs", "Give up to 10 secret names.")
        for r in refs:
            if self.conn.execute("SELECT 1 FROM dv_secrets WHERE project_id = ? AND name = ? AND revoked_at IS NULL", (project_id, r if isinstance(r, str) else "")).fetchone() is None:
                raise not_found("secret")
        return list(refs)

    def list(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        rows = self.conn.execute("SELECT * FROM dv_envs WHERE project_id = ? AND deleted_at IS NULL ORDER BY name", (project["id"],)).fetchall()
        return {"items": [present(r, self._latest(r["id"])) for r in rows], "package_sources": {"indexes": index_urls(self.ctx.settings), "wheelhouse": bool(wheelhouse())}}

    def get(self, principal, project_id: Any, env_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        env = self._env(project["id"], env_id)
        vers = self.conn.execute("SELECT * FROM dv_env_versions WHERE env_id = ? ORDER BY version DESC", (env["id"],)).fetchall()
        d = present(env)
        d["versions"] = [version_dict(v, full=(i == 0)) for i, v in enumerate(vers)]
        return d

    def set_requirements(self, principal, project_id: Any, env_id: Any, requirements: Any, env_refs: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        env = self._env(project["id"], env_id)
        self._build_allowed()
        last = self._latest(env["id"])
        refs = self._refs(project["id"], env_refs) if env_refs is not None else json.loads(last["env_refs"])
        if last["status"] == "building":
            raise dev_error("BUILD_IN_PROGRESS", "A build is already running for this environment.", 409)
        self._new_version(principal, project, env, requirements, refs)
        return self.get(principal, project["id"], env["id"])

    def add_package(self, principal, project_id: Any, env_id: Any, package: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        env = self._env(project["id"], env_id)
        if not isinstance(package, str):
            raise invalid("package", "Give a package such as pandas==2.2.2.")
        base = self._base_requirements(env)
        pairs = {re.sub(r"[-_.]+", "-", n.split("[")[0]).lower(): (n, s) for n, s in parse_requirements(base)}
        new = parse_requirements(package)
        if len(new) != 1:
            raise invalid("package", "Add one package at a time.")
        pairs[re.sub(r"[-_.]+", "-", new[0][0].split("[")[0]).lower()] = new[0]
        return self.set_requirements(principal, project["id"], env["id"], canonical(list(pairs.values())))

    def remove_package(self, principal, project_id: Any, env_id: Any, package: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        env = self._env(project["id"], env_id)
        key = re.sub(r"[-_.]+", "-", str(package).split("[")[0]).lower()
        pairs = [(n, s) for n, s in parse_requirements(self._base_requirements(env)) if re.sub(r"[-_.]+", "-", n.split("[")[0]).lower() != key]
        if len(pairs) == len(parse_requirements(self._base_requirements(env))):
            raise not_found("package")
        return self.set_requirements(principal, project["id"], env["id"], canonical(pairs))

    def _base_requirements(self, env) -> str:
        return self._latest(env["id"])["requirements"]

    def rebuild(self, principal, project_id: Any, env_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        env = self._env(project["id"], env_id)
        self._build_allowed()
        last = self._latest(env["id"])
        if last["status"] == "building":
            raise dev_error("BUILD_IN_PROGRESS", "A build is already running for this environment.", 409)
        self._new_version(principal, project, env, last["requirements"], json.loads(last["env_refs"]))
        return self.get(principal, project["id"], env["id"])

    def compare(self, principal, project_id: Any, env_id: Any, a: Any, b: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        env = self._env(project["id"], env_id)
        rows = {}
        for k, v in (("a", a), ("b", b)):
            if isinstance(v, bool) or not isinstance(v, int):
                raise invalid(k, "Give a version number.")
            r = self.conn.execute("SELECT * FROM dv_env_versions WHERE env_id = ? AND version = ?", (env["id"], v)).fetchone()
            if r is None:
                raise not_found("version")
            rows[k] = r

        def pkgs(r) -> Dict[str, str]:
            src = r["lockfile"] if r["lockfile"] else r["requirements"]
            out = {}
            for line in (src or "").splitlines():
                m = re.match(r"^([A-Za-z0-9._-]+)\s*(==.*|[<>~!].*)?$", line.strip())
                if m:
                    out[re.sub(r"[-_.]+", "-", m[1]).lower()] = (m[2] or "").lstrip("=")
            return out
        pa, pb = pkgs(rows["a"]), pkgs(rows["b"])
        return {"a": a, "b": b, "basis": "lockfile" if rows["a"]["lockfile"] and rows["b"]["lockfile"] else "requirements",
                "added": sorted(set(pb) - set(pa)), "removed": sorted(set(pa) - set(pb)),
                "changed": [{"package": k, "from": pa[k], "to": pb[k]} for k in sorted(set(pa) & set(pb)) if pa[k] != pb[k]],
                "manifest_diff": "".join(difflib.unified_diff(rows["a"]["requirements"].splitlines(True), rows["b"]["requirements"].splitlines(True), f"v{a}", f"v{b}"))[:8000]}

    def delete(self, principal, project_id: Any, env_id: Any, confirm: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        env = self._env(project["id"], env_id)
        if confirm != env["name"]:
            raise invalid("confirm", "Type the environment's name to confirm.")
        if self.conn.execute("SELECT 1 FROM dv_jobs WHERE env_id = ? AND status IN ('queued','starting','running')", (env["id"],)).fetchone():
            raise dev_error("ENV_IN_USE", "A job is using this environment. Cancel it first.", 409)
        self.conn.execute("UPDATE dv_envs SET deleted_at = ? WHERE id = ?", (self.now(), env["id"]))
        shutil.rmtree(project_dir(self.ctx.settings, project["id"]) / "envs" / env["id"], ignore_errors=True)
        self.event(project["id"], principal, "env.deleted", "env", env["id"])
        return {"deleted": True}


# ---------------------------------------------------------------------------------------------------------------------- worker side
def _run(cmd: List[str], env: Dict[str, str], cwd: Path, timeout: int, log: List[str], secrets: List[str]) -> int:
    try:
        p = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, start_new_session=True)
    except subprocess.TimeoutExpired:
        log.append("timed out")
        return 124
    except OSError as e:
        log.append(f"could not run: {type(e).__name__}")
        return 127
    log.append(redact_text((p.stdout + p.stderr).decode("utf-8", "replace")[-6000:], secrets))
    return p.returncode


def run_build(worker, job) -> None:
    """Build one environment version. Records the lockfile, conflicts and vulnerability-check outcome; the previous ready version keeps working if this one fails."""
    from .engine import transition
    conn, s = worker.conn, worker.settings
    now = worker.now()
    pid, eid = job["project_id"], job["env_id"]
    n = int(json.loads(job["argv"])[0])
    ver = conn.execute("SELECT * FROM dv_env_versions WHERE env_id = ? AND version = ?", (eid, n)).fetchone()
    limits = json.loads(job["limits"])
    transition(conn, job["id"], "running", frm="starting", heartbeat_at=now)
    log: List[str] = []
    report: Dict[str, Any] = {"python": PY, "platform": sys.platform}
    base = project_dir(s, pid) / "envs" / eid / f"v{n}"
    status, error = "failed", None
    try:
        if not worker.entitled(job["owner_user_id"]):
            raise RuntimeError("Special access ended; the build was not started.")
        shutil.rmtree(base, ignore_errors=True)
        (base / "home").mkdir(parents=True)
        pairs = parse_requirements(ver["requirements"])
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(base / "home"), "TMPDIR": str(base / "home"), "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "PYTHONNOUSERSITE": "1"}
        venv = base / "venv"
        vcmd = [sys.executable, "-m", "venv"] + ([] if pairs else ["--without-pip"]) + [str(venv)]
        if _run(vcmd, env, base, 120, log, []) != 0:
            raise RuntimeError("Could not create the virtual environment.")
        py = str(env_python_path(s, pid, eid, n))
        lock, conflicts = "", []
        if pairs:
            req = base / "requirements.txt"
            req.write_text(ver["requirements"])
            wh = wheelhouse()
            src = ["--no-index", "--find-links", wh] if wh else ["--index-url", index_urls(s)[0]] + sum((["--extra-index-url", u] for u in index_urls(s)[1:]), [])
            code = _run([py, "-m", "pip", "install", "--only-binary=:all:", "--no-input", "--no-cache-dir", *src, "-r", str(req)], env, base, int(limits["wall_seconds"]) - 30, log, [])
            if code != 0:
                raise RuntimeError("Installing the packages failed. Check the names and versions, and that wheels exist for this platform. Details are in the build log.")
            frz = subprocess.run([py, "-m", "pip", "freeze", "--exclude-editable"], capture_output=True, text=True, env=env, timeout=60)
            lock = "\n".join(sorted(l for l in frz.stdout.splitlines() if "==" in l)) + "\n"
            chk = subprocess.run([py, "-m", "pip", "check"], capture_output=True, text=True, env=env, timeout=60)
            conflicts = [l for l in chk.stdout.splitlines() if l.strip() and "No broken requirements" not in l]
            report["vulnerability_check"] = _vulnerability_check(py, req, env, wh)
        else:
            report["vulnerability_check"] = {"status": "not_applicable", "detail": "No packages."}
        report["conflicts"] = conflicts
        report["packages_installed"] = len([l for l in lock.splitlines() if l])
        report["source"] = "wheelhouse" if wheelhouse() else "index"
        conn.execute("UPDATE dv_env_versions SET lockfile = ?, status = 'ready', report = ? WHERE id = ?", (lock, json.dumps(report), ver["id"]))
        conn.execute("UPDATE dv_envs SET current_version = ? WHERE id = ?", (n, eid))
        status = "ready"
    except Exception as e:  # noqa: BLE001 - a failed build is a recorded outcome, never a worker crash
        error = str(e)[:500] if isinstance(e, RuntimeError) else "Build failed (" + type(e).__name__ + ")."
        conn.execute("UPDATE dv_env_versions SET status = 'failed', error = ?, report = ? WHERE id = ?", (error, json.dumps(report), ver["id"]))
        shutil.rmtree(base, ignore_errors=True)
    text = "\n".join(log)[-8000:]
    conn.execute("INSERT OR REPLACE INTO dv_job_output (job_id, stream, text, truncated) VALUES (?, 'stdout', ?, 0)", (job["id"], text))
    transition(conn, job["id"], "succeeded" if status == "ready" else "failed", finished_at=worker.now(), exit_code=0 if status == "ready" else 1, error_class=None if status == "ready" else "build_failed")
    record_event(conn, worker.now(), pid, None, "env.build_" + status, "env", eid, "success" if status == "ready" else "failure", version=n, error=error)


def _vulnerability_check(py: str, req: Path, env: Dict[str, str], wh: Optional[str]) -> Dict[str, Any]:
    """Uses pip-audit when the OPERATOR has it installed; it queries a public advisory service, so it is skipped offline. Never reported as 'clean' when it did not run."""
    probe = subprocess.run([sys.executable, "-m", "pip_audit", "--version"], capture_output=True, text=True, timeout=30, env={**env, "PATH": os.environ.get("PATH", "")})
    if probe.returncode != 0:
        return {"status": "unavailable", "detail": "pip-audit is not installed on the worker, so no vulnerability check was run. Install it (free, open source) to enable this."}
    if wh:
        return {"status": "unavailable", "detail": "Offline (wheelhouse) mode: the advisory service cannot be queried."}
    r = subprocess.run([sys.executable, "-m", "pip_audit", "-r", str(req), "--format", "json", "--no-deps", "--disable-pip"], capture_output=True, text=True, timeout=180, env={**env, "PATH": os.environ.get("PATH", "")})
    try:
        data = json.loads(r.stdout or "{}")
        vulns = [{"package": d.get("name"), "version": d.get("version"), "ids": [v.get("id") for v in d.get("vulns", [])]} for d in data.get("dependencies", []) if d.get("vulns")]
        return {"status": "findings" if vulns else "clean", "findings": vulns[:50]}
    except ValueError:
        return {"status": "unavailable", "detail": "pip-audit did not return a usable result."}
