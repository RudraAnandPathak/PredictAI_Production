"""Experiments and lineage. Every link in a lineage chain is a real record or is reported as missing; nothing is inferred or invented.

data (Data Workspace asset + version) -> transformations -> features -> commit -> environment -> experiment (job) -> evaluation (metrics) -> model version (Phase 7 registry, NOT duplicated) -> artifacts -> approval -> rollback.
Approval and rollback here are RECORDS. Nothing is deployed anywhere (the registry's own rollback endpoint remains the control)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..identity.authentication import ensure_fresh_auth
from .common import DevService, clean, dev_error, invalid, new_id, not_found, record_event

TASKS = ("binary_classification", "multiclass_classification", "regression", "forecasting", "other")
MAX_METRICS = 100


def _json_obj(value: Any, field: str, limit: int = 20000) -> Any:
    try:
        raw = json.dumps(value)
    except (TypeError, ValueError):
        raise invalid(field, "Give plain JSON values.")
    if len(raw) > limit:
        raise invalid(field, "That is too large.")
    return value


def read_metrics(out_dir: Path) -> Dict[str, float]:
    """metrics.json written by the job: a flat object of finite numbers. Anything else is ignored rather than guessed."""
    f = out_dir / "metrics.json"
    try:
        if not f.is_file() or f.stat().st_size > 100_000:
            return {}
        doc = json.loads(f.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    return {str(k)[:60]: float(v) for k, v in list(doc.items())[:MAX_METRICS] if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}


def on_job_done(conn, now: str, job, out_dir: Path) -> None:
    metrics = read_metrics(out_dir)
    conn.execute("UPDATE dv_experiments SET status = ?, metrics = ?, commit_sha = ?, tree_sha256 = ?, env_id = ?, env_version = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                 ("evaluated" if metrics else "trained", json.dumps(metrics), job["commit_sha"], job["tree_sha256"], job["env_id"], job["env_version"], now, job["experiment_id"], job["project_id"]))
    record_event(conn, now, job["project_id"], None, "experiment.finished", "experiment", job["experiment_id"], "success", metrics=len(metrics))


def on_job_failed(conn, now: str, job, cls: Optional[str]) -> None:
    conn.execute("UPDATE dv_experiments SET status = 'failed', updated_at = ? WHERE id = ? AND project_id = ?", (now, job["experiment_id"], job["project_id"]))
    record_event(conn, now, job["project_id"], None, "experiment.failed", "experiment", job["experiment_id"], "failure", error_class=cls)


def present(r) -> Dict[str, Any]:
    d = {k: r[k] for k in ("id", "project_id", "name", "objective", "task_type", "asset_id", "dataset_version_id", "commit_sha", "tree_sha256", "env_id", "env_version", "split_strategy", "seed", "status",
                           "job_id", "limitations", "reg_version_id", "approval_state", "approved_at", "rollback_reg_version_id", "created_at", "updated_at")}
    for k in ("params", "feature_set", "preprocessing", "metrics"):
        d[k] = json.loads(r[k])
    return d


class ExperimentService(DevService):
    def _row(self, project_id: str, exp_id: Any):
        r = self.conn.execute("SELECT * FROM dv_experiments WHERE id = ? AND project_id = ?", (exp_id if isinstance(exp_id, str) else "", project_id)).fetchone()
        if r is None:
            raise not_found("experiment")
        return r

    def create(self, principal, project_id: Any, *, name: Any = None, objective: Any = None, task_type: Any = "other", asset_id: Any = None, dataset_version_id: Any = None, env_id: Any = None,
               params: Any = None, feature_set: Any = None, preprocessing: Any = None, split_strategy: Any = None, seed: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_write:user", principal)
        name = clean(name, "name", maximum=120)
        if task_type not in TASKS:
            raise invalid("task_type", "Choose one of: " + ", ".join(TASKS) + ".")
        if asset_id is not None:
            a = self.conn.execute("SELECT id FROM assets WHERE id = ? AND project_id = ? AND owner_user_id = ?", (asset_id if isinstance(asset_id, str) else "", project["id"], project["owner_user_id"])).fetchone()
            if a is None:
                raise not_found("dataset")
        if dataset_version_id is not None:
            if asset_id is None or self.conn.execute("SELECT 1 FROM asset_versions WHERE id = ? AND asset_id = ?", (dataset_version_id if isinstance(dataset_version_id, str) else "", asset_id)).fetchone() is None:
                raise not_found("dataset version")
        elif asset_id is not None:
            cur = self.conn.execute("SELECT current_version_id FROM assets WHERE id = ?", (asset_id,)).fetchone()
            dataset_version_id = cur["current_version_id"] if cur else None       # pinned now, so later edits to the dataset do not rewrite this experiment's inputs
        if env_id is not None and self.conn.execute("SELECT 1 FROM dv_envs WHERE id = ? AND project_id = ? AND deleted_at IS NULL", (env_id if isinstance(env_id, str) else "", project["id"])).fetchone() is None:
            raise not_found("environment")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 31):
            raise invalid("seed", "Use a whole number from 0 to 2147483647.")
        feats = _json_obj(feature_set or [], "feature_set")
        if not isinstance(feats, list) or len(feats) > 500 or not all(isinstance(f, str) and len(f) <= 100 for f in feats):
            raise invalid("feature_set", "Give a list of column names.")
        eid, now = new_id(), self.now()
        self.conn.execute("INSERT INTO dv_experiments (id, project_id, owner_user_id, name, objective, task_type, asset_id, dataset_version_id, env_id, params, feature_set, preprocessing, split_strategy, seed, created_at, updated_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (eid, project["id"], project["owner_user_id"], name, clean(objective, "objective", maximum=1000, required=False) if objective else None, task_type, asset_id, dataset_version_id, env_id,
                           json.dumps(_json_obj(params or {}, "params")), json.dumps(feats), json.dumps(_json_obj(preprocessing or {}, "preprocessing")), clean(split_strategy, "split_strategy", maximum=200, required=False) if split_strategy else None, seed, now, now))
        self.event(project["id"], principal, "experiment.created", "experiment", eid, name=name)
        return present(self._row(project["id"], eid))

    def list(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        return {"items": [present(r) for r in self.conn.execute("SELECT * FROM dv_experiments WHERE project_id = ? ORDER BY created_at DESC LIMIT 200", (project["id"],)).fetchall()]}

    def get(self, principal, project_id: Any, exp_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        return present(self._row(project["id"], exp_id))

    def run(self, principal, project_id: Any, exp_id: Any, *, entry: Any = None, priority: Any = 100, secret_refs: Any = None, limits: Any = None) -> Dict[str, Any]:
        from .engine import JobService
        project = self.open_project(principal, project_id, writable=True)
        exp = self._row(project["id"], exp_id)
        if exp["status"] == "running":
            raise dev_error("EXPERIMENT_RUNNING", "This experiment is already running.", 409)
        job = JobService(self.ctx, self.ws).submit(principal, project["id"], kind="script", entry=entry, env_id=exp["env_id"], priority=priority, secret_refs=secret_refs, limits=limits, experiment_id=exp["id"])
        self.conn.execute("UPDATE dv_experiments SET status = 'running', job_id = ?, updated_at = ? WHERE id = ?", (job["id"], self.now(), exp["id"]))
        self.event(project["id"], principal, "experiment.run_started", "experiment", exp["id"], job_id=job["id"])
        return {"experiment": present(self._row(project["id"], exp["id"])), "job": job}

    def link_model(self, principal, project_id: Any, exp_id: Any, reg_version_id: Any, limitations: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        exp = self._row(project["id"], exp_id)
        v = self.conn.execute("SELECT v.id FROM reg_versions v JOIN reg_models m ON m.id = v.model_id WHERE v.id = ? AND m.project_id = ? AND m.owner_user_id = ?",
                              (reg_version_id if isinstance(reg_version_id, str) else "", project["id"], project["owner_user_id"])).fetchone()
        if v is None:
            raise not_found("model version")
        self.conn.execute("UPDATE dv_experiments SET reg_version_id = ?, limitations = COALESCE(?, limitations), approval_state = 'none', approved_by = NULL, approved_at = NULL, updated_at = ? WHERE id = ?",
                          (v["id"], clean(limitations, "limitations", maximum=2000, required=False) if limitations else None, self.now(), exp["id"]))
        self.event(project["id"], principal, "experiment.model_linked", "experiment", exp["id"], reg_version_id=v["id"])
        return present(self._row(project["id"], exp["id"]))

    def approve(self, principal, project_id: Any, exp_id: Any, reason: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        exp = self._row(project["id"], exp_id)
        ensure_fresh_auth(self.ctx, principal)
        reason = clean(reason, "reason", maximum=500, minimum=5)
        missing = [m for m, ok in (("a finished run with metrics", exp["status"] == "evaluated"), ("a linked registry model version", exp["reg_version_id"]), ("written limitations", (exp["limitations"] or "").strip())) if not ok]
        if missing:
            raise dev_error("APPROVAL_INCOMPLETE", "Before approving, this experiment needs " + ", ".join(missing) + ".", 409)
        now = self.now()
        self.conn.execute("UPDATE dv_experiments SET approval_state = 'approved', approved_by = ?, approved_at = ?, updated_at = ? WHERE id = ?", (principal.user_id, now, now, exp["id"]))
        self.event(project["id"], principal, "experiment.approved", "experiment", exp["id"], reason=reason, deployed=False)
        return present(self._row(project["id"], exp["id"]))

    def revoke_approval(self, principal, project_id: Any, exp_id: Any, reason: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        exp = self._row(project["id"], exp_id)
        reason = clean(reason, "reason", maximum=500, minimum=5)
        if exp["approval_state"] != "approved":
            raise dev_error("NOT_APPROVED", "This experiment is not approved.", 409)
        self.conn.execute("UPDATE dv_experiments SET approval_state = 'revoked', updated_at = ? WHERE id = ?", (self.now(), exp["id"]))
        self.event(project["id"], principal, "experiment.approval_revoked", "experiment", exp["id"], reason=reason)
        return present(self._row(project["id"], exp["id"]))

    def set_rollback(self, principal, project_id: Any, exp_id: Any, reg_version_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        exp = self._row(project["id"], exp_id)
        if not exp["reg_version_id"]:
            raise dev_error("NO_MODEL_LINKED", "Link a registry model version first.", 409)
        cur = self.conn.execute("SELECT model_id, version FROM reg_versions WHERE id = ?", (exp["reg_version_id"],)).fetchone()
        tgt = self.conn.execute("SELECT id, model_id, version FROM reg_versions WHERE id = ?", (reg_version_id if isinstance(reg_version_id, str) else "",)).fetchone()
        if cur is None or tgt is None or tgt["model_id"] != cur["model_id"] or tgt["version"] >= cur["version"]:
            raise invalid("reg_version_id", "Choose an EARLIER version of the same registry model.")
        self.conn.execute("UPDATE dv_experiments SET rollback_reg_version_id = ?, updated_at = ? WHERE id = ?", (tgt["id"], self.now(), exp["id"]))
        self.event(project["id"], principal, "experiment.rollback_target_set", "experiment", exp["id"], target=tgt["id"], note="recorded only; nothing is deployed")
        return present(self._row(project["id"], exp["id"]))

    def compare(self, principal, project_id: Any, ids: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        if not isinstance(ids, list) or not 2 <= len(ids) <= 5:
            raise invalid("ids", "Choose 2 to 5 experiments.")
        rows = [present(self._row(project["id"], i)) for i in ids]
        keys = sorted({k for r in rows for k in r["metrics"]})
        return {"experiments": [{"id": r["id"], "name": r["name"], "status": r["status"], "commit_sha": r["commit_sha"], "env": [r["env_id"], r["env_version"]], "seed": r["seed"], "params": r["params"]} for r in rows],
                "metrics": {k: [r["metrics"].get(k) for r in rows] for k in keys}}

    def lineage(self, principal, project_id: Any, exp_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        e = self._row(project["id"], exp_id)
        links: List[Dict[str, Any]] = []

        def add(stage: str, record: Optional[Dict[str, Any]], note: Optional[str] = None):
            links.append({"stage": stage, "present": record is not None, "record": record, "note": note if record is None else None})
        ver = self.conn.execute("SELECT v.id, v.version_number, v.kind, v.checksum_sha256, v.transformation_id, v.asset_id, a.name FROM asset_versions v JOIN assets a ON a.id = v.asset_id WHERE v.id = ?", (e["dataset_version_id"],)).fetchone() if e["dataset_version_id"] else None
        add("data", {"asset_id": ver["asset_id"], "name": ver["name"], "version_id": ver["id"], "version": ver["version_number"], "sha256": ver["checksum_sha256"]} if ver else None, "No dataset was attached.")
        tr, chain = None, []
        if ver and ver["transformation_id"]:
            t = self.conn.execute("SELECT id, kind, status, explanation FROM transformations WHERE id = ?", (ver["transformation_id"],)).fetchone()
            tr = {"id": t["id"], "kind": t["kind"], "status": t["status"], "explanation": t["explanation"]} if t else None
        add("transformations", tr, "The dataset version is the original upload (no transformation).")
        feats = json.loads(e["feature_set"])
        add("features", {"columns": feats, "preprocessing": json.loads(e["preprocessing"])} if feats else None, "No feature list was recorded.")
        add("commit", {"commit_sha": e["commit_sha"], "tree_sha256": e["tree_sha256"]} if e["tree_sha256"] else None, "The experiment has not run yet, so no code snapshot is recorded.")
        env = self.conn.execute("SELECT v.version, v.manifest_sha256, v.status FROM dv_env_versions v WHERE v.env_id = ? AND v.version = ?", (e["env_id"], e["env_version"])).fetchone() if e["env_id"] and e["env_version"] else None
        add("environment", {"env_id": e["env_id"], "version": env["version"], "manifest_sha256": env["manifest_sha256"]} if env else None, "The run used the default interpreter (no project environment).")
        job = self.conn.execute("SELECT id, status, exit_code, wall_ms, isolation, finished_at FROM dv_jobs WHERE id = ?", (e["job_id"],)).fetchone() if e["job_id"] else None
        add("experiment", {"id": e["id"], "name": e["name"], "seed": e["seed"], "split_strategy": e["split_strategy"], "params": json.loads(e["params"]), "job": dict(job) if job else None})
        m = json.loads(e["metrics"])
        add("evaluation", {"metrics": m, "limitations": e["limitations"]} if m else None, "No metrics.json was produced.")
        rv = self.conn.execute("SELECT v.id, v.version, v.status, v.artifact_sha256, m.name FROM reg_versions v JOIN reg_models m ON m.id = v.model_id WHERE v.id = ?", (e["reg_version_id"],)).fetchone() if e["reg_version_id"] else None
        add("model_version", dict(rv) if rv else None, "No registry model version is linked.")
        arts = self.conn.execute("SELECT id, name, version, sha256, bytes FROM dv_artifacts WHERE experiment_id = ? AND project_id = ? AND deleted_at IS NULL ORDER BY name", (e["id"], project["id"])).fetchall()
        add("artifacts", {"items": [dict(a) for a in arts]} if arts else None, "The run produced no artifacts.")
        add("approval", {"state": e["approval_state"], "approved_at": e["approved_at"], "deployed": False} if e["approval_state"] != "none" else None, "Not approved.")
        rb = self.conn.execute("SELECT id, version FROM reg_versions WHERE id = ?", (e["rollback_reg_version_id"],)).fetchone() if e["rollback_reg_version_id"] else None
        add("rollback", dict(rb) if rb else None, "No rollback target recorded.")
        return {"experiment_id": e["id"], "links": links, "complete": all(l["present"] for l in links if l["stage"] not in ("transformations",)), "deployment": "Nothing is deployed from the Development Workspace."}
