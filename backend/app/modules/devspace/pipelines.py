"""Pipelines: a fixed set of stage types run in order against ONE pinned snapshot (tree hash, commit, environment version) captured when the run is created.

Stages that execute user code are ordinary jobs (same isolation and limits as any job). Stages that only check records run inside the worker's tick. A run never deploys anything: after a fully green run it can
be marked 'pending approval' and approved by the project owner, but the deploy step is disabled in this release and says so. Feature flag: PREDICTAI_DEV_PIPELINES_ENABLED (runs are refused when off)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..identity.authentication import ensure_fresh_auth
from . import notebooks
from .common import BLOCKED_NAMES, DevService, clean, contains_secret_pattern, dev_error, invalid, new_id, not_found, project_dir, record_event
from .compute import effective_limit
from .engine import _head_commit, _tree_hash, entitled

JOB_STAGES = {"lint", "unit_tests", "integration_tests", "data_contracts"}
INTERNAL_STAGES = {"prepare_env", "validate_deps", "eval_gate", "security_checks", "export_validation", "final_record"}
STAGE_TYPES = JOB_STAGES | INTERNAL_STAGES
TRIGGERS = ("manual", "commit", "pull_request")
MAX_STAGES = 12
TERMINAL_RUN = ("succeeded", "failed", "cancelled", "blocked")
DEFAULT_STAGES = [{"name": "Prepare environment", "type": "prepare_env"}, {"name": "Validate dependencies", "type": "validate_deps"}, {"name": "Syntax check", "type": "lint"},
                  {"name": "Unit tests", "type": "unit_tests"}, {"name": "Security checks", "type": "security_checks"}, {"name": "Export validation", "type": "export_validation"},
                  {"name": "Final record", "type": "final_record"}]


def check_definition(stages: Any) -> List[Dict[str, Any]]:
    if not isinstance(stages, list) or not 1 <= len(stages) <= MAX_STAGES:
        raise invalid("stages", f"Give 1 to {MAX_STAGES} stages.")
    out = []
    for i, st in enumerate(stages):
        if not isinstance(st, dict) or st.get("type") not in STAGE_TYPES:
            raise invalid("stages", f"Stage {i + 1} needs a type from: {', '.join(sorted(STAGE_TYPES))}.")
        name = str(st.get("name") or st["type"].replace("_", " ").title())[:60]
        item: Dict[str, Any] = {"name": name, "type": st["type"]}
        for k in ("path", "entry", "metric", "experiment_id"):
            if st.get(k) is not None:
                if not isinstance(st[k], str) or len(st[k]) > 200 or "\x00" in st[k] or st[k].startswith("/") or ".." in st[k].split("/"):
                    raise invalid("stages", f"Stage {i + 1}: '{k}' must be a short relative value.")
                item[k] = st[k]
        if st.get("min") is not None:
            if isinstance(st["min"], bool) or not isinstance(st["min"], (int, float)):
                raise invalid("stages", f"Stage {i + 1}: 'min' must be a number.")
            item["min"] = st["min"]
        if st["type"] == "eval_gate" and not (item.get("metric") and "min" in item and item.get("experiment_id")):
            raise invalid("stages", f"Stage {i + 1}: an eval_gate needs experiment_id, metric and min.")
        if st["type"] == "final_record" and i != len(stages) - 1:
            raise invalid("stages", "final_record must be the last stage.")
        out.append(item)
    return out


def present_pipeline(r) -> Dict[str, Any]:
    return {"id": r["id"], "name": r["name"], "stages": json.loads(r["definition"])["stages"], "triggers": json.loads(r["triggers"]), "created_at": r["created_at"]}


def present_run(conn, r, detail: bool = False) -> Dict[str, Any]:
    d = {k: r[k] for k in ("id", "pipeline_id", "trigger", "commit_sha", "branch", "tree_sha256", "env_id", "env_version", "status", "approval_state", "approved_at", "rollback_target", "created_at", "finished_at")}
    if detail:
        d["record"] = json.loads(r["record"])
        d["stages"] = [{"position": s["position"], "name": s["name"], "type": s["type"], "status": s["status"], "job_id": s["job_id"], "result": json.loads(s["result"]), "started_at": s["started_at"], "finished_at": s["finished_at"]}
                       for s in conn.execute("SELECT * FROM dv_pipeline_stages WHERE run_id = ? ORDER BY position", (r["id"],)).fetchall()]
    return d


def create_run(conn, settings, clock, project_id: str, owner_id: str, pipeline, trigger: str) -> str:
    """Pins the snapshot. Later edits to files or a rebuilt environment do not change what this run tests."""
    now, rid = clock.iso(), new_id()
    env = conn.execute("SELECT id, current_version FROM dv_envs WHERE project_id = ? AND deleted_at IS NULL AND current_version > 0 ORDER BY is_default DESC, created_at LIMIT 1", (project_id,)).fetchone()
    tree = project_dir(settings, project_id) / "tree"
    conn.execute("INSERT INTO dv_pipeline_runs (id, pipeline_id, project_id, owner_user_id, trigger, commit_sha, tree_sha256, env_id, env_version, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (rid, pipeline["id"], project_id, owner_id, trigger, _head_commit(tree), _tree_hash(conn, project_id), env["id"] if env else None, env["current_version"] if env else None, "queued", now))
    for i, st in enumerate(json.loads(pipeline["definition"])["stages"]):
        conn.execute("INSERT INTO dv_pipeline_stages (id, run_id, position, name, type, status) VALUES (?,?,?,?,?,'pending')", (new_id(), rid, i, st["name"], st["type"]))
    record_event(conn, now, project_id, owner_id, "pipeline.run_created", "pipeline_run", rid, pipeline=pipeline["id"], trigger=trigger)
    return rid


def trigger_from_webhook(ctx, ws, repo, trigger: str) -> List[str]:
    if not ctx.settings.dev_pipelines_enabled or not entitled(ctx.conn, repo["owner_user_id"]):
        return []
    out = []
    for p in ctx.conn.execute("SELECT * FROM dv_pipelines WHERE project_id = ? AND deleted_at IS NULL", (repo["project_id"],)).fetchall():
        if trigger in json.loads(p["triggers"]):
            out.append(create_run(ctx.conn, ctx.settings, ctx.clock, repo["project_id"], repo["owner_user_id"], p, trigger))
    return out


class PipelineService(DevService):
    def _flag(self) -> None:
        if not self.ctx.settings.dev_pipelines_enabled:
            raise dev_error("PIPELINES_DISABLED", "Pipelines are switched off on this installation (feature flag PREDICTAI_DEV_PIPELINES_ENABLED). Definitions can be saved; runs cannot start.", 409)

    def _pipe(self, project_id: str, pid: Any):
        r = self.conn.execute("SELECT * FROM dv_pipelines WHERE id = ? AND project_id = ? AND deleted_at IS NULL", (pid if isinstance(pid, str) else "", project_id)).fetchone()
        if r is None:
            raise not_found("pipeline")
        return r

    def _run(self, project_id: str, rid: Any):
        r = self.conn.execute("SELECT * FROM dv_pipeline_runs WHERE id = ? AND project_id = ?", (rid if isinstance(rid, str) else "", project_id)).fetchone()
        if r is None:
            raise not_found("pipeline run")
        return r

    def create(self, principal, project_id: Any, *, name: Any = None, stages: Any = None, triggers: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_write:user", principal)
        name = clean(name, "name", maximum=60)
        defn = check_definition(stages if stages is not None else DEFAULT_STAGES)
        trig = triggers or ["manual"]
        if not isinstance(trig, list) or not trig or any(t not in TRIGGERS for t in trig):
            raise invalid("triggers", "Choose from manual, commit, pull_request.")
        if self.conn.execute("SELECT 1 FROM dv_pipelines WHERE project_id = ? AND name = ? COLLATE NOCASE AND deleted_at IS NULL", (project["id"], name)).fetchone():
            raise dev_error("CONFLICT", "A pipeline with that name already exists.", 409)
        pid = new_id()
        self.conn.execute("INSERT INTO dv_pipelines (id, project_id, owner_user_id, name, definition, triggers, created_at) VALUES (?,?,?,?,?,?,?)",
                          (pid, project["id"], project["owner_user_id"], name, json.dumps({"stages": defn}), json.dumps(sorted(set(trig))), self.now()))
        self.event(project["id"], principal, "pipeline.created", "pipeline", pid, name=name, stages=len(defn))
        return present_pipeline(self._pipe(project["id"], pid))

    def list(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        rows = self.conn.execute("SELECT * FROM dv_pipelines WHERE project_id = ? AND deleted_at IS NULL ORDER BY name", (project["id"],)).fetchall()
        return {"items": [present_pipeline(r) for r in rows], "enabled": self.ctx.settings.dev_pipelines_enabled, "deploy_enabled": False, "stage_types": sorted(STAGE_TYPES)}

    def delete(self, principal, project_id: Any, pid: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        p = self._pipe(project["id"], pid)
        self.conn.execute("UPDATE dv_pipelines SET deleted_at = ? WHERE id = ?", (self.now(), p["id"]))
        self.event(project["id"], principal, "pipeline.deleted", "pipeline", p["id"])
        return {"deleted": True}

    def run(self, principal, project_id: Any, pid: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_submit:user", principal)
        self._flag()
        if self.ctx.settings.dev_execution == "off":
            raise dev_error("EXECUTION_DISABLED", "Code execution is switched off, so pipeline stages cannot run.", 403)
        p = self._pipe(project["id"], pid)
        if self.conn.execute("SELECT 1 FROM dv_pipeline_runs WHERE pipeline_id = ? AND status IN ('queued','running')", (p["id"],)).fetchone():
            raise dev_error("RUN_IN_PROGRESS", "This pipeline already has a run in progress.", 409)
        rid = create_run(self.conn, self.ctx.settings, self.clock, project["id"], principal.user_id, p, "manual")
        return present_run(self.conn, self._run(project["id"], rid), True)

    def runs(self, principal, project_id: Any, pid: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        sql, args = "SELECT * FROM dv_pipeline_runs WHERE project_id = ?", [project["id"]]
        if pid is not None:
            sql, args = sql + " AND pipeline_id = ?", args + [pid if isinstance(pid, str) else ""]
        return {"items": [present_run(self.conn, r) for r in self.conn.execute(sql + " ORDER BY created_at DESC LIMIT 100", args).fetchall()]}

    def get_run(self, principal, project_id: Any, rid: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        return present_run(self.conn, self._run(project["id"], rid), True)

    def cancel_run(self, principal, project_id: Any, rid: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        r = self._run(project["id"], rid)
        if r["status"] in TERMINAL_RUN:
            raise dev_error("RUN_FINISHED", "This run already finished.", 409)
        finish(self.conn, self.now(), r, "cancelled", "cancelled by you")
        self.event(project["id"], principal, "pipeline.run_cancelled", "pipeline_run", r["id"])
        return present_run(self.conn, self._run(project["id"], rid), True)

    def approve(self, principal, project_id: Any, rid: Any, reason: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self._flag()
        r = self._run(project["id"], rid)
        ensure_fresh_auth(self.ctx, principal)
        reason = clean(reason, "reason", maximum=500, minimum=5)
        if r["approval_state"] != "pending":
            raise dev_error("NOT_AWAITING_APPROVAL", "Only a fully successful run that is awaiting approval can be approved.", 409)
        self.conn.execute("UPDATE dv_pipeline_runs SET approval_state = 'approved', approved_by = ?, approved_at = ? WHERE id = ?", (principal.user_id, self.now(), r["id"]))
        self.event(project["id"], principal, "pipeline.run_approved", "pipeline_run", r["id"], reason=reason, deployed=False)
        return present_run(self.conn, self._run(project["id"], rid), True)

    def reject(self, principal, project_id: Any, rid: Any, reason: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        r = self._run(project["id"], rid)
        reason = clean(reason, "reason", maximum=500, minimum=5)
        if r["approval_state"] != "pending":
            raise dev_error("NOT_AWAITING_APPROVAL", "This run is not awaiting approval.", 409)
        self.conn.execute("UPDATE dv_pipeline_runs SET approval_state = 'rejected' WHERE id = ?", (r["id"],))
        self.event(project["id"], principal, "pipeline.run_rejected", "pipeline_run", r["id"], reason=reason)
        return present_run(self.conn, self._run(project["id"], rid), True)

    def deploy(self, principal, project_id: Any, rid: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self._run(project["id"], rid)
        self.event(project["id"], principal, "pipeline.deploy_refused", "pipeline_run", rid if isinstance(rid, str) else None, "denied", reason="deployment_disabled")
        raise dev_error("DEPLOY_DISABLED", "Deployment is disabled in this release: there is no deployment target, and nothing deploys automatically. Approval is a recorded decision only.", 501)


# ---------------------------------------------------------------------------------------------------------------------- advancing runs (worker)
def finish(conn, now: str, run, status: str, why: Optional[str] = None) -> None:
    conn.execute("UPDATE dv_pipeline_stages SET status = 'skipped', finished_at = ? WHERE run_id = ? AND status = 'pending'", (now, run["id"]))
    for s in conn.execute("SELECT job_id FROM dv_pipeline_stages WHERE run_id = ? AND status = 'running' AND job_id IS NOT NULL", (run["id"],)).fetchall():
        conn.execute("UPDATE dv_jobs SET cancel_requested = 1, status = CASE WHEN status = 'queued' THEN 'cancelled' WHEN status IN ('starting','running') THEN 'cancelling' ELSE status END WHERE id = ?", (s["job_id"],))
    conn.execute("UPDATE dv_pipeline_stages SET status = 'failed', finished_at = ? WHERE run_id = ? AND status = 'running'", (now, run["id"]))
    conn.execute("UPDATE dv_pipeline_runs SET status = ?, finished_at = ?, approval_state = CASE WHEN ? = 'succeeded' THEN approval_state ELSE 'none' END WHERE id = ? AND status NOT IN ('succeeded','failed','cancelled','blocked')",
                 (status, now, status, run["id"]))
    record_event(conn, now, run["project_id"], None, "pipeline.run_" + status, "pipeline_run", run["id"], "success" if status == "succeeded" else "failure", why=why)


def advance_all(conn, settings, clock) -> int:
    n = 0
    for r in conn.execute("SELECT * FROM dv_pipeline_runs WHERE status IN ('queued','running') ORDER BY created_at LIMIT 50").fetchall():
        try:
            advance(conn, settings, clock, r)
            n += 1
        except Exception as e:  # noqa: BLE001 - one broken run must not stall the others; it is failed with a reason
            finish(conn, clock.iso(), r, "failed", "internal error: " + type(e).__name__)
    return n


def advance(conn, settings, clock, run) -> None:
    now = clock.iso()
    if not entitled(conn, run["owner_user_id"]):
        finish(conn, now, run, "blocked", "special access ended")
        return
    if run["status"] == "queued":
        conn.execute("UPDATE dv_pipeline_runs SET status = 'running' WHERE id = ?", (run["id"],))
    stages = conn.execute("SELECT * FROM dv_pipeline_stages WHERE run_id = ? ORDER BY position", (run["id"],)).fetchall()
    cfgs = json.loads(conn.execute("SELECT definition FROM dv_pipelines WHERE id = ?", (run["pipeline_id"],)).fetchone()["definition"])["stages"]
    for st, cfg in zip(stages, cfgs):
        if st["status"] in ("succeeded", "skipped"):
            continue
        if st["status"] == "failed":
            return
        if st["status"] == "pending":
            conn.execute("UPDATE dv_pipeline_stages SET status = 'running', started_at = ? WHERE id = ?", (now, st["id"]))
            if st["type"] in JOB_STAGES:
                err = _enqueue(conn, settings, clock, run, st, cfg)
                if err:
                    return _stage_done(conn, now, run, st, False, {"reason": err})
                return
            ok, result = _internal(conn, settings, clock, run, st, cfg)
            _stage_done(conn, now, run, st, ok, result)
            if not ok:
                return
            continue
        # running job stage
        job = conn.execute("SELECT * FROM dv_jobs WHERE id = ?", (st["job_id"],)).fetchone()
        if job is None or job["status"] in ("queued", "starting", "running", "cancelling"):
            return
        ok = job["status"] == "succeeded"
        result = {"job_id": job["id"], "job_status": job["status"], "exit_code": job["exit_code"], "error_class": job["error_class"]}
        if ok and job["tree_sha256"] != run["tree_sha256"]:
            ok, result["reason"] = False, "The files changed after this run was created, so the result would not describe the pinned snapshot. Start a new run."
        _stage_done(conn, now, run, st, ok, result)
        if not ok:
            return
    fresh = conn.execute("SELECT status FROM dv_pipeline_runs WHERE id = ?", (run["id"],)).fetchone()
    if fresh["status"] == "running":
        finish(conn, now, run, "succeeded")


def _stage_done(conn, now: str, run, st, ok: bool, result: Dict[str, Any]) -> None:
    conn.execute("UPDATE dv_pipeline_stages SET status = ?, result = ?, finished_at = ? WHERE id = ?", ("succeeded" if ok else "failed", json.dumps(result, default=str)[:20000], now, st["id"]))
    record_event(conn, now, run["project_id"], None, "pipeline.stage_" + ("succeeded" if ok else "failed"), "pipeline_run", run["id"], "success" if ok else "failure", stage=st["name"], type=st["type"])
    if not ok:
        finish(conn, now, run, "failed", f"stage '{st['name']}' failed")


def _enqueue(conn, settings, clock, run, st, cfg) -> Optional[str]:
    t = st["type"]
    lock = ""
    if run["env_id"]:
        row = conn.execute("SELECT lockfile FROM dv_env_versions WHERE env_id = ? AND version = ?", (run["env_id"], run["env_version"])).fetchone()
        lock = (row["lockfile"] or "") if row else ""
    kind, entry, argv = "command", None, []
    if t == "lint":
        argv = ["-m", "compileall", "-q", "."]
    elif t in ("unit_tests", "integration_tests"):
        path = cfg.get("path") or ("tests" if t == "unit_tests" else "integration_tests")
        if not conn.execute("SELECT 1 FROM dv_files WHERE project_id = ? AND path LIKE ? AND deleted_at IS NULL", (run["project_id"], path.rstrip("/") + "/%")).fetchone():
            return f"No test files found under '{path}/'. A test stage with no tests fails rather than passing silently."
        argv = ["-m", "pytest", "-q", path] if re.search(r"(?im)^pytest==", lock) else ["-m", "unittest", "discover", "-s", path]
    else:   # data_contracts: the project's own check script; its exit code is the verdict
        entry = cfg.get("entry") or "contracts/check.py"
        if not conn.execute("SELECT 1 FROM dv_files WHERE project_id = ? AND path = ? AND deleted_at IS NULL", (run["project_id"], entry)).fetchone():
            return f"The contract script '{entry}' does not exist."
        kind = "script"
    e = lambda k: effective_limit(conn, settings, run["owner_user_id"], run["project_id"], k)  # noqa: E731
    limits = {"wall_seconds": e("job_wall_seconds"), "cpu_seconds": e("job_cpu_seconds"), "memory_mb": e("job_memory_mb"), "output_kb": e("job_output_kb"), "max_procs": e("job_max_procs"), "storage_mb": e("job_storage_mb")}
    jid = new_id()
    conn.execute("INSERT INTO dv_jobs (id, project_id, owner_user_id, kind, status, priority, entry, argv, env_id, env_version, limits, network, secret_refs, attempts, max_attempts, pipeline_run_id, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,'none','[]',0,1,?,?)", (jid, run["project_id"], run["owner_user_id"], kind, "queued", 80, entry, json.dumps(argv), run["env_id"], run["env_version"], json.dumps(limits), run["id"], clock.iso()))
    conn.execute("UPDATE dv_pipeline_stages SET job_id = ? WHERE id = ?", (jid, st["id"]))
    return None


def _internal(conn, settings, clock, run, st, cfg) -> Tuple[bool, Dict[str, Any]]:
    t, pid = st["type"], run["project_id"]
    if t == "prepare_env":
        if not run["env_id"]:
            return True, {"environment": None, "note": "No project environment; jobs use the worker's interpreter."}
        v = conn.execute("SELECT status, manifest_sha256 FROM dv_env_versions WHERE env_id = ? AND version = ?", (run["env_id"], run["env_version"])).fetchone()
        return (v is not None and v["status"] == "ready"), {"env_id": run["env_id"], "version": run["env_version"], "manifest_sha256": v["manifest_sha256"] if v else None}
    if t == "validate_deps":
        if not run["env_id"]:
            return True, {"note": "No environment to validate."}
        v = conn.execute("SELECT * FROM dv_env_versions WHERE env_id = ? AND version = ?", (run["env_id"], run["env_version"])).fetchone()
        rep = json.loads(v["report"]) if v else {}
        vuln = (rep.get("vulnerability_check") or {}).get("status", "unavailable")
        problems = list(rep.get("conflicts", [])) + (["known vulnerabilities reported"] if vuln == "findings" else [])
        return not problems, {"conflicts": rep.get("conflicts", []), "vulnerability_check": vuln, "note": "The vulnerability check did not run (pip-audit unavailable); this is NOT a clean result." if vuln == "unavailable" else None, "problems": problems}
    if t == "eval_gate":
        ex = conn.execute("SELECT metrics, status FROM dv_experiments WHERE id = ? AND project_id = ?", (cfg["experiment_id"], pid)).fetchone()
        val = json.loads(ex["metrics"]).get(cfg["metric"]) if ex else None
        ok = val is not None and val >= cfg["min"]
        return ok, {"metric": cfg["metric"], "value": val, "minimum": cfg["min"], "reason": None if ok else ("metric not found" if val is None else "below the minimum")}
    if t == "security_checks":
        tree, hits = project_dir(settings, pid) / "tree", []
        for dp, dn, fn in os.walk(tree):
            dn[:] = [d for d in dn if d != ".git"]
            for f in fn:
                full = Path(dp) / f
                rel = full.relative_to(tree).as_posix()
                if BLOCKED_NAMES.search(rel):
                    hits.append(rel + " (credential-type file)")
                elif full.is_file() and not full.is_symlink() and full.stat().st_size <= 1024 * 1024 and contains_secret_pattern(full.read_text(errors="ignore")):
                    hits.append(rel + " (looks like a credential)")
        cfgfile = tree / ".git" / "config"
        if cfgfile.is_file() and re.search(r"://[^/\s@]+@", cfgfile.read_text(errors="ignore")):
            hits.append(".git/config (credentials in a remote URL)")
        return not hits, {"findings": hits[:50], "scope": "pattern scan of project files and git config; not a full security audit"}
    if t == "export_validation":
        tree, bad = project_dir(settings, pid) / "tree", []
        for r in conn.execute("SELECT path FROM dv_files WHERE project_id = ? AND path LIKE '%.ipynb' AND deleted_at IS NULL", (pid,)).fetchall():
            try:
                probs = notebooks.validate(json.loads((tree / r["path"]).read_text()))
            except (OSError, ValueError):
                probs = ["unreadable"]
            if probs:
                bad.append({"path": r["path"], "problem": probs[0]})
        return not bad, {"notebooks_checked": conn.execute("SELECT COUNT(*) FROM dv_files WHERE project_id = ? AND path LIKE '%.ipynb' AND deleted_at IS NULL", (pid,)).fetchone()[0], "invalid": bad}
    # final_record
    stages = conn.execute("SELECT name, type, status, result FROM dv_pipeline_stages WHERE run_id = ? AND position < ? ORDER BY position", (run["id"], st["position"])).fetchall()
    env = conn.execute("SELECT manifest_sha256 FROM dv_env_versions WHERE env_id = ? AND version = ?", (run["env_id"], run["env_version"])).fetchone() if run["env_id"] else None
    prev = conn.execute("SELECT id FROM dv_pipeline_runs WHERE pipeline_id = ? AND status = 'succeeded' AND id != ? ORDER BY created_at DESC LIMIT 1", (run["pipeline_id"], run["id"])).fetchone()
    record = {"run_id": run["id"], "pipeline_id": run["pipeline_id"], "trigger": run["trigger"], "commit_sha": run["commit_sha"], "tree_sha256": run["tree_sha256"],
              "environment": {"id": run["env_id"], "version": run["env_version"], "manifest_sha256": env["manifest_sha256"] if env else None}, "created_at": run["created_at"], "finished_at": clock.iso(),
              "stages": [{"name": s["name"], "type": s["type"], "status": s["status"]} for s in stages], "deployed": False}
    awaiting = settings.dev_pipelines_enabled and all(s["status"] == "succeeded" for s in stages)
    conn.execute("UPDATE dv_pipeline_runs SET record = ?, rollback_target = ?, approval_state = ? WHERE id = ?", (json.dumps(record), prev["id"] if prev else None, "pending" if awaiting else "none", run["id"]))
    return True, {"recorded": True, "rollback_target": prev["id"] if prev else None, "awaiting_approval": bool(awaiting)}
