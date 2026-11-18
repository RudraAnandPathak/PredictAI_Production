"""Model Registry: registers Phase 5 models as versioned, checksummed, lifecycle-managed records. It does not train anything and does not deploy anything: a lifecycle state is a record
of a decision inside PredictAI, never evidence of a production deployment (`deployment_note` says so on every response)."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

from ...core.errors import AppError
from ..collab.access import AccessResolver
from ..identity.authorization import Principal, build_principal
from ..p7 import E, Service7, choice, clean, conflict, invalid, jdump, jload, new_id, not_found, page
from ..storage import StorageError
from .repository import RegistryRepository

STATES = ("draft", "registered", "evaluated", "staging", "active", "archived", "failed")
TRANSITIONS = {
    "draft": {"registered", "failed"},
    "registered": {"evaluated", "failed", "archived"},
    "evaluated": {"staging", "archived", "failed", "registered"},
    "staging": {"active", "evaluated", "archived"},
    "active": {"staging", "archived"},
    "archived": {"evaluated", "registered"},
    "failed": {"registered", "archived"},
}
NEEDS_CONFIRM = {("staging", "active"), ("active", "archived"), ("active", "staging")}
PREDICTABLE = {"registered", "evaluated", "staging", "active"}
DEPLOYMENT_NOTE = ("A lifecycle state records a decision made inside PredictAI. PredictAI does not deploy models, so 'active' does not mean a model is serving anywhere outside this application.")
CLASSIFICATION_METRICS = ("accuracy", "precision", "recall", "f1", "roc_auc")
REGRESSION_METRICS = ("mae", "mse", "rmse", "r2")
FAMILY = {"binary_classification": "classification", "multiclass_classification": "classification", "regression": "regression"}


class RegistryService(Service7):
    def __init__(self, ctx: Any, ws: Any, access: AccessResolver) -> None:
        super().__init__(ctx, ws)
        self.repo = RegistryRepository(ctx.conn)
        self.access = access
        self.storage = ws.storage

    # ------------------------------------------------------------------ presentation
    def _version(self, row, *, full: bool = False) -> Dict[str, Any]:
        meta = jload(row["metadata"], {})
        out = {"id": row["id"], "model_id": row["model_id"], "version": row["version"], "status": row["status"], "source_model_id": row["source_model_id"], "algorithm": meta.get("algorithm"),
               "target": meta.get("target"), "created_at": row["created_at"], "artifact": {"sha256": row["artifact_sha256"], "bytes": row["artifact_bytes"], "last_verified_at": row["last_verified_at"],
                                                                                         "last_verified_ok": None if row["last_verified_ok"] is None else bool(row["last_verified_ok"])},
               "evaluation_summary": {"validation": (jload(row["evaluation"], {}).get("validation") or {}).get("score"), "holdout": (jload(row["evaluation"], {}).get("test") or {}).get("primary_score"),
                                      "metric": meta.get("primary_metric")}}
        if full:
            out.update(metadata=meta, evaluation=jload(row["evaluation"], {}), allowed_transitions=sorted(TRANSITIONS[row["status"]]))
        return out

    def _model(self, row, versions: Sequence[Any], *, full: bool = False) -> Dict[str, Any]:
        out = {"id": row["id"], "project_id": row["project_id"], "name": row["name"], "description": row["description"], "model_type": row["model_type"], "active_version_id": row["active_version_id"],
               "created_at": row["created_at"], "updated_at": row["updated_at"], "version_count": len(versions), "deployment_note": DEPLOYMENT_NOTE,
               "latest": self._version(versions[-1]) if versions else None}
        if full:
            out["versions"] = [self._version(v) for v in versions]
        return out

    # ------------------------------------------------------------------ registration
    def register(self, principal: Principal, source_model_id: str, *, name: Any = None, description: Any = None, model_id: Any = None, note: Any = None) -> Dict[str, Any]:
        self.limit("p7_register:user", principal)
        src = self.ws.predictive.get_model(principal, source_model_id)               # owner-checked; a deleted dataset or project hides its models
        self.project(principal, src["project_id"], writable=True)
        row = self.ws.predictive.repo.model(principal.user_id, source_model_id)
        note = clean(note, "note", maximum=500, required=False)
        if model_id is not None:
            reg = self.access.resolve(principal, "model", model_id, "edit")
            if not reg.is_owner:
                raise not_found("model")
            mrow = self.repo.model(model_id)
            if FAMILY.get(mrow["model_type"]) != FAMILY.get(src["problem_type"]) or mrow["model_type"] != src["problem_type"]:
                raise conflict("MODEL_TYPE_MISMATCH", f"This registered model holds {mrow['model_type'].replace('_', ' ')} models; the selected model is {src['problem_type'].replace('_', ' ')}.")
            if mrow["project_id"] != src["project_id"]:
                raise conflict("PROJECT_MISMATCH", "A new version must come from the same project as the registered model.")
            if self.repo.version_for_source(mrow["id"], source_model_id):
                raise conflict("ALREADY_REGISTERED", "This trained model is already a version of that registered model.")
        else:
            nm = clean(name or src["name"], "name", maximum=120)
            if self.repo.model_by_name(principal.user_id, src["project_id"], nm):
                raise conflict("NAME_TAKEN", "A registered model with that name already exists in this project. Register as a new version of it, or choose another name.")
            mrow = None
        try:
            artifact = self.storage.read_bytes(row["model_key"])
        except StorageError:
            raise conflict("ARTIFACT_MISSING", "The trained model file could not be read, so it cannot be registered.") from None
        digest = hashlib.sha256(artifact).hexdigest()
        if digest != row["model_sha256"]:
            raise conflict("ARTIFACT_CORRUPT", "The trained model file does not match its recorded checksum, so it was not registered.")
        now = self.now()
        with self.ctx.conn:
            if mrow is None:
                mid = new_id()
                self.repo.insert_model(id=mid, project_id=src["project_id"], owner_user_id=principal.user_id, name=nm, description=clean(description, "description", maximum=1000, required=False),
                                       model_type=src["problem_type"], active_version_id=None, created_at=now, updated_at=now)
            else:
                mid = mrow["id"]
            number = self.repo.next_version(mid)
            vid = new_id()
            key = f"registry/{src['project_id']}/{mid}/v{number}.model"
            stored = self.storage.put(key, [artifact], overwrite=False)               # the registry keeps its own copy: deleting the Phase 5 model cannot damage a registered version
            meta, evaluation = self._snapshot(principal, src, row)
            self.repo.insert_version(id=vid, model_id=mid, version=number, source_model_id=source_model_id, status="registered", metadata=jdump(meta), evaluation=jdump(evaluation),
                                     artifact_key=key, artifact_sha256=stored.sha256, artifact_bytes=stored.size, last_verified_at=now, last_verified_ok=1, created_by=principal.user_id, created_at=now)
            self.repo.touch_model(mid, now)
            self.repo.add_event(mid, vid, principal.user_id, "registered", jdump({"version": number, "source_model_id": source_model_id, "note": note}), now)
        self.audit(E.MODEL_REGISTERED, principal, mid, version=number)
        return self.get(principal, mid)

    def _snapshot(self, principal: Principal, src: Dict[str, Any], row) -> tuple:
        report: Dict[str, Any] = {}
        try:
            report = json.loads(self.storage.read_bytes(row["report_key"]).decode("utf-8"))
        except (StorageError, ValueError):
            pass
        ev = report.get("evaluation") or {}
        feats = src.get("feature_config") or {}
        meta = {"name": src["name"], "problem_type": src["problem_type"], "algorithm": src["algorithm"], "algorithm_label": src["algorithm_label"], "target": src["target"],
                "features": {"used": src["features"]["used"], "dropped": [{"name": d.get("name"), "reason": d.get("reason")} for d in feats.get("dropped", [])]},
                "preprocessing": src.get("preprocessing_config"), "hyperparameters": src.get("hyperparameters"), "training_configuration": src.get("config"), "input_schema": src.get("input_schema"),
                "primary_metric": src["primary_metric"], "seed": src["seed"], "ml_version": src["ml_version"], "trained_at": src["trained_at"], "tuned": src["tuned"],
                "experiment": {"training_job_id": src["job_id"], "phase5_model_id": src["id"], "phase5_model_version": src["model_version"]},
                "lineage": {"project_id": src["project_id"], "asset_id": src["asset_id"], "dataset_version_id": src["version_id"], "dataset": src.get("dataset"),
                            "features_used": src["features"]["used"]},
                "explanation": {"importance_available": bool(report.get("importance")), "note": "Open the model's report for importance and partial-dependence explanations."}}
        basis = ev.get("basis") or (src.get("metrics") or {}).get("basis")
        test_metrics = (src.get("metrics") or {}).get("evaluation") or ev.get("metrics") or {}
        best_cv = ((report.get("best") or {}).get("cv") or {})
        evaluation = {
            "validation": {"kind": "cross-validation on the training rows", "score": src["cv_score"], "metric": src["primary_metric"], "folds": best_cv.get("folds"), "detail": best_cv.get("metrics")},
            "test": {"kind": "independent hold-out rows never used for fitting or model selection" if basis == "holdout" else f"evaluation basis: {basis}", "basis": basis, "primary_score": src["test_score"],
                     "metrics": test_metrics, "n": ev.get("n"), "calibration": ev.get("calibration"), "residuals": {k: ev.get(k) for k in ("residuals", "typical_error", "target_range") if ev.get(k)}},
            "leakage_checks": {"target_in_features": src["target"] in src["features"]["used"], "holdout_used_for_test_scores": basis == "holdout",
                               "columns_dropped": len(feats.get("dropped", [])), "note": "Hold-out rows are set aside before fitting and tuning; cross-validation uses the training rows only."},
            "limits": ["Scores describe this model on this dataset's held-out rows, not future data.", *list(report.get("limitations") or [])[:5]]}
        return meta, evaluation

    # ------------------------------------------------------------------ reading
    def list(self, principal: Principal, project_id: Optional[str] = None, *, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        lim, off = page(limit, offset)
        if project_id:
            self.project(principal, project_id)
        rows, total = self.repo.models(principal.user_id, project_id, lim, off)
        return {"items": [self._model(r, self.repo.versions(r["id"])) for r in rows], "total": total, "limit": lim, "offset": off,
                "shared_with_me": [s for s in self.access.shared_with(principal, "model")], "deployment_note": DEPLOYMENT_NOTE}

    def get(self, principal: Principal, model_id: str) -> Dict[str, Any]:
        acc = self.access.resolve(principal, "model", model_id, "view")
        row = self.repo.model(model_id)
        out = self._model(row, self.repo.versions(model_id), full=True)
        out["your_access"] = acc.level
        return out

    def get_version(self, principal: Principal, version_id: str) -> Dict[str, Any]:
        v = self.repo.version(version_id)
        if v is None:
            raise not_found("model version")
        acc = self.access.resolve(principal, "model", v["model_id"], "view")
        out = self._version(v, full=True)
        out["your_access"] = acc.level
        out["deployment_note"] = DEPLOYMENT_NOTE
        return out

    def history(self, principal: Principal, model_id: str, *, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        self.access.resolve(principal, "model", model_id, "view")
        lim, off = page(limit, offset, maximum=200, default=50)
        return {"items": [{"id": r["id"], "version_id": r["version_id"], "event": r["event"], "detail": jload(r["detail"], {}), "at": r["created_at"], "actor": r["actor_user_id"]}
                          for r in self.repo.events(model_id, lim, off)], "limit": lim, "offset": off}

    # ------------------------------------------------------------------ integrity
    def verify(self, principal: Principal, version_id: str) -> Dict[str, Any]:
        v = self.repo.version(version_id)
        if v is None:
            raise not_found("model version")
        self.access.resolve(principal, "model", v["model_id"], "view")
        ok, why = self._check_artifact(v)
        now = self.now()
        with self.ctx.conn:
            self.repo.set_verified(v["id"], now, ok)
            self.repo.add_event(v["model_id"], v["id"], principal.user_id, "verified", jdump({"ok": ok, "reason": why}), now)
        return {"version_id": v["id"], "ok": ok, "checked_at": now, "expected_sha256": v["artifact_sha256"], "reason": why}

    def _check_artifact(self, v) -> tuple:
        try:
            data = self.storage.read_bytes(v["artifact_key"])
        except StorageError:
            return False, "The stored artifact is missing."
        if hashlib.sha256(data).hexdigest() != v["artifact_sha256"]:
            return False, "The stored artifact does not match its checksum."
        return True, None

    # ------------------------------------------------------------------ lifecycle
    def transition(self, principal: Principal, version_id: str, to: Any, *, confirm: Any = False, reason: Any = None) -> Dict[str, Any]:
        v = self.repo.version(version_id)
        if v is None:
            raise not_found("model version")
        acc = self.access.resolve(principal, "model", v["model_id"], "edit")
        if not acc.is_owner:
            raise not_found("model")                                                   # lifecycle decisions belong to the model's owner
        self.project(principal, acc.project_id, writable=True)
        to = choice(to, "to", STATES)
        frm = v["status"]
        if to not in TRANSITIONS[frm]:
            raise conflict("INVALID_TRANSITION", f"A model version cannot go from '{frm}' to '{to}'. From '{frm}' it can go to: {', '.join(sorted(TRANSITIONS[frm])) or 'nowhere'}.")
        reason = clean(reason, "reason", maximum=300, required=False)
        if (frm, to) in NEEDS_CONFIRM and confirm is not True:
            raise AppError("CONFIRMATION_REQUIRED", f"Moving a version from '{frm}' to '{to}' needs explicit confirmation. Send confirm: true. {DEPLOYMENT_NOTE}", 409)
        ev = jload(v["evaluation"], {})
        if to == "evaluated" and (ev.get("test") or {}).get("primary_score") is None:
            raise conflict("EVALUATION_MISSING", "This version has no hold-out evaluation, so it cannot be marked evaluated.")
        if to == "active":
            ok, why = self._check_artifact(v)
            if not ok:
                raise conflict("ARTIFACT_INTEGRITY", f"{why} The version was not activated.")
        mrow = self.repo.model(v["model_id"])
        now = self.now()
        with self.ctx.conn:
            if to == "active":
                prior = mrow["active_version_id"]
                if prior and prior != v["id"]:
                    self.repo.set_status(prior, "archived")
                    self.repo.add_event(v["model_id"], prior, principal.user_id, "transition", jdump({"from": "active", "to": "archived", "because": "another version was activated"}), now)
                self.repo.touch_model(v["model_id"], now, v["id"])
            elif frm == "active":
                self.repo.touch_model(v["model_id"], now, None)
            else:
                self.repo.touch_model(v["model_id"], now)
            self.repo.set_status(v["id"], to)
            self.repo.add_event(v["model_id"], v["id"], principal.user_id, "transition", jdump({"from": frm, "to": to, "reason": reason}), now)
        self.audit(E.MODEL_TRANSITION, principal, v["model_id"], version=v["version"], frm=frm, to=to)
        return self.get_version(principal, version_id)

    def rollback(self, principal: Principal, model_id: str, to_version_id: str, *, confirm: Any = False, reason: Any = None) -> Dict[str, Any]:
        acc = self.access.resolve(principal, "model", model_id, "edit")
        if not acc.is_owner:
            raise not_found("model")
        self.project(principal, acc.project_id, writable=True)
        target = self.repo.version(to_version_id)
        if target is None or target["model_id"] != model_id:
            raise not_found("model version")
        mrow = self.repo.model(model_id)
        if mrow["active_version_id"] is None:
            raise conflict("NOTHING_ACTIVE", "No version is active, so there is nothing to roll back from. Activate a version instead.")
        if mrow["active_version_id"] == target["id"]:
            raise conflict("ALREADY_ACTIVE", "That version is already active.")
        if target["status"] in ("failed", "draft"):
            raise conflict("INVALID_TRANSITION", f"A version in state '{target['status']}' cannot be rolled back to.")
        if confirm is not True:
            raise AppError("CONFIRMATION_REQUIRED", f"Rolling back to version {target['version']} needs explicit confirmation. Send confirm: true. {DEPLOYMENT_NOTE}", 409)
        ok, why = self._check_artifact(target)
        if not ok:
            raise conflict("ARTIFACT_INTEGRITY", f"{why} The rollback was not made.")
        reason = clean(reason, "reason", maximum=300, required=False)
        now = self.now()
        with self.ctx.conn:
            current = mrow["active_version_id"]
            self.repo.set_status(current, "archived")
            self.repo.set_status(target["id"], "active")
            self.repo.touch_model(model_id, now, target["id"])
            self.repo.add_event(model_id, target["id"], principal.user_id, "rollback", jdump({"from_version_id": current, "to_version_id": target["id"], "reason": reason}), now)
        self.audit(E.MODEL_ROLLBACK, principal, model_id, to_version=target["version"])
        return self.get(principal, model_id)

    # ------------------------------------------------------------------ comparison
    def compare(self, principal: Principal, version_ids: Any) -> Dict[str, Any]:
        if not isinstance(version_ids, (list, tuple)) or not 2 <= len(version_ids) <= 6 or len(set(version_ids)) != len(version_ids):
            raise invalid("version_ids", "Choose two to six different versions.")
        versions = []
        for vid in version_ids:
            v = self.repo.version(vid)
            if v is None:
                raise not_found("model version")
            self.access.resolve(principal, "model", v["model_id"], "view")
            m = self.repo.model(v["model_id"])
            versions.append((v, m, jload(v["metadata"], {}), jload(v["evaluation"], {})))
        fams = {FAMILY.get(m["model_type"], m["model_type"]) for _, m, _, _ in versions}
        types = {m["model_type"] for _, m, _, _ in versions}
        if len(fams) > 1 or len(types) > 1:
            raise conflict("INCOMPARABLE", "These models solve different kinds of problems (" + ", ".join(sorted(types)).replace("_", " ") + "), so their scores cannot be compared.")
        family = next(iter(fams))
        wanted = CLASSIFICATION_METRICS if family == "classification" else REGRESSION_METRICS if family == "regression" else ()
        rows = []
        for v, m, meta, ev in versions:
            tm = (ev.get("test") or {}).get("metrics") or {}
            rows.append({"version_id": v["id"], "model": m["name"], "version": v["version"], "status": v["status"], "algorithm": meta.get("algorithm_label"), "target": meta.get("target"),
                         "dataset_version_id": (meta.get("lineage") or {}).get("dataset_version_id"), "seed": meta.get("seed"),
                         "holdout": {k: tm.get(k) for k in wanted}, "validation": {"metric": meta.get("primary_metric"), "cross_validation_score": (ev.get("validation") or {}).get("score"),
                                                                                    "folds": (ev.get("validation") or {}).get("folds")},
                         "holdout_primary": (ev.get("test") or {}).get("primary_score"),
                         "calibration": ((ev.get("test") or {}).get("calibration") or {}).get("verdict") if family == "classification" else None,
                         "residuals": (ev.get("test") or {}).get("residuals") if family == "regression" else None, "leakage_checks": ev.get("leakage_checks")})
        warnings: List[str] = []
        if len({(r["dataset_version_id"], r["seed"]) for r in rows}) > 1:
            warnings.append("These models were trained on different dataset versions or different random splits, so their hold-out rows differ. Hold-out scores are not strictly comparable.")
        if len({r["target"] for r in rows}) > 1:
            warnings.append("The models predict different targets.")
        if any((r["leakage_checks"] or {}).get("target_in_features") for r in rows):
            warnings.append("A model lists its own target among its inputs: its scores cannot be trusted.")
        return {"family": family, "metrics_shown": list(wanted), "rows": rows, "warnings": warnings,
                "reading": ["'validation' scores come from cross-validation on training rows; 'holdout' scores come from rows kept aside and never used to fit or choose the model.",
                            "A higher hold-out score on one dataset does not show a model is better on other data."]}

    # ------------------------------------------------------------------ predictions
    def predict(self, principal: Principal, version_id: str, values: Any) -> Dict[str, Any]:
        v = self.repo.version(version_id)
        if v is None:
            raise not_found("model version")
        acc = self.access.resolve(principal, "model", v["model_id"], "edit")             # a viewer may look; running a model needs editor rights
        if v["status"] not in PREDICTABLE:
            raise conflict("VERSION_NOT_USABLE", f"A version in state '{v['status']}' cannot be used for predictions.")
        ok, why = self._check_artifact(v)
        if not ok:
            raise conflict("ARTIFACT_INTEGRITY", f"{why} No prediction was made.")
        owner_row = self.conn.execute("SELECT * FROM users WHERE id = ? AND status = 'active'", (acc.owner_id,)).fetchone()
        if owner_row is None:
            raise not_found("model")
        owner = principal if acc.is_owner else build_principal(self.ctx.repo, owner_row)    # predictions are stored under the model's owner; the actor is recorded below
        if self.ws.predictive.repo.model(owner.user_id, v["source_model_id"]) is None:
            raise AppError("SOURCE_MODEL_REMOVED", "The Phase 5 model behind this version was deleted. The registered artifact is kept and verified, but predictions need the original model to be retrained and registered again.", 409)
        out = self.ws.predictive.predict(owner, v["source_model_id"], values)
        now = self.now()
        with self.ctx.conn:
            self.repo.add_event(v["model_id"], v["id"], principal.user_id, "prediction", jdump({"prediction_id": out.get("id"), "source_model_id": v["source_model_id"]}), now)
        out["registry"] = {"version_id": v["id"], "model_id": v["model_id"], "version": v["version"], "artifact_sha256": v["artifact_sha256"], "status": v["status"],
                           "lineage": "This prediction used exactly this registered version and the inputs stored with it."}
        return out

    def predictions(self, principal: Principal, version_id: str, *, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        v = self.repo.version(version_id)
        if v is None:
            raise not_found("model version")
        self.access.resolve(principal, "model", v["model_id"], "view")
        lim, off = page(limit, offset)
        rows = self.conn.execute("SELECT id, kind, result, created_at, rows FROM ml_predictions WHERE model_id = ? ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (v["source_model_id"], lim, off)).fetchall()
        return {"version_id": v["id"], "items": [{"id": r["id"], "kind": r["kind"], "created_at": r["created_at"], "rows": r["rows"], "summary": _short(jload(r["result"], {}))} for r in rows], "limit": lim, "offset": off}


def _short(result: Any) -> Any:
    if isinstance(result, dict):
        return {k: result[k] for k in list(result)[:6] if not isinstance(result[k], (dict, list))}
    return None
