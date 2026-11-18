"""Predictive-intelligence service: authorises, validates, starts and tracks training, serves models, predictions and comparisons. Framework-free.

Every entry point reaches data through `AnalyticsService._open` (which reuses the workspace's single ownership check, `AssetService.get_owned`) or through a model
row filtered by owner followed by the same asset check, so a missing, foreign, deleted or other-project resource is an identical 404. Numbers are computed only by
the modelling code that runs in the sandboxed worker; nothing here (or in the browser, or in an explanation) recalculates them."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ...core.errors import AppError
from ..analytics.service import AnalyticsService
from ..explanation import explain
from ..identity.authorization import Principal
from ..identity.constants import Perm
from ..identity.context import Context
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from ..workspace import keys
from ..workspace.assets import AssetService
from ..workspace.projects import ProjectService
from ..workspace.repository import WorkspaceRepository
from ..workspace.validation import clean_text
from . import problem as prob
from .catalog import CATALOG, default_space
from .config import (CAT_IMPUTE, CLASS_WEIGHT, CV_STRATEGIES, DEFAULT_CONFIG, ENCODING, HIGH_CARD, HPO_METHODS, LOG, NUM_IMPUTE, OUTLIERS, RARE_CLASSES, SCALING, parse_config)
from .constants import LIMITS, MAX_PREDICTION_HISTORY, ML_VERSION, MEvent, JobStatus, Problem
from .errors import Cancelled, MlError, conflict, config_invalid, not_found, quota, too_large, unsuitable
from .jobs import JobRunner
from .repository import PredictiveRepository
from .tasks import limits_for, run_task
from .train import shown

PREDICTION_CODES_422 = {"UNKNOWN_INPUT", "INPUT_INVALID", "NO_INPUT", "MISSING_COLUMNS", "BATCH_TOO_LARGE", "BATCH_EMPTY", "NO_REFERENCE_ROWS"}


def _j(value: Optional[str], default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def translate(error: MlError) -> AppError:
    """Modelling-code errors (which carry no data values) become API errors with the same code and a sensible HTTP status."""
    if error.code in ("ML_MEMORY", "RESULT_TOO_LARGE", "MODEL_INPUT_TOO_LARGE"):
        return AppError(error.code, error.message, 413)
    if error.code == "MODEL_UNREADABLE":
        return AppError(error.code, error.message, 409)
    if error.code in ("ML_ERROR", "INTERNAL_RANKING"):
        return AppError("ML_ERROR", "The model task could not be completed.", 500)
    return unsuitable(error.code, error.message)


class PredictiveService:
    def __init__(self, ctx: Context, wrepo: WorkspaceRepository, repo: PredictiveRepository, storage: StorageBackend, assets: AssetService, analytics: AnalyticsService,
                 projects: ProjectService, runner: Optional[JobRunner]) -> None:
        self.ctx, self.wrepo, self.repo, self.storage, self.assets, self.analytics, self.projects, self.runner = ctx, wrepo, repo, storage, assets, analytics, projects, runner

    # ------------------------------------------------------------------ helpers
    def _extended(self, principal: Principal) -> bool:
        return principal.can(Perm.COMPUTE_EXTENDED)

    def _limit(self, principal: Principal, name: str) -> Any:
        return LIMITS[name][1 if self._extended(principal) else 0]

    def _audit(self, principal: Principal, event: str, asset_id: str, project_id: str, target: Optional[str], meta: Dict[str, Any], outcome: str = "success") -> None:
        """IDs, counts and codes only: never a data value, a column value or an input the person typed."""
        self.ctx.audit.record(event, outcome, actor=principal.user_id, target=target, metadata={"asset_id": asset_id, "project_id": project_id, **meta})

    def _run(self, task: str, job: Mapping[str, Any], sources: Mapping[str, Any], principal: Principal, *, budget_s: float, collect: Sequence[str] = ()):
        try:
            return run_task(task, job, sources, extended=self._extended(principal), isolation=self.ctx.settings.ml_isolation, budget_s=budget_s, collect=collect)
        except MlError as e:
            raise translate(e) from None
        except Cancelled:
            raise conflict("ML_CANCELLED", "The task was cancelled.") from None
        except StorageError:
            raise not_found("data") from None

    # ------------------------------------------------------------------ catalogue (nothing about any dataset)
    def catalog(self, principal: Principal) -> Dict[str, Any]:
        i = 1 if self._extended(principal) else 0
        algos = []
        for a in CATALOG.values():
            tunable = {n: ({"kind": k, "choices": list(lo)} if k == "choice" else {"kind": k, "low": lo, "high": hi}) for n, (k, lo, hi) in a.params.items()}
            algos.append({"key": a.key, "label": a.label, "problems": list(a.kinds), "description": a.description, "cost": a.cost, "needs_scaling": a.scaling,
                          "native_importance": a.importance is not None, "supports_class_weight": a.class_weight, "max_rows": a.max_rows, "included_in_auto": a.auto, "tunable": tunable,
                          "default_grid": {n: list(v) for n, v in a.grid.items()}})
        return {
            "version": ML_VERSION, "algorithms": algos, "defaults": DEFAULT_CONFIG,
            "options": {"scaling": list(SCALING), "numeric_impute": list(NUM_IMPUTE), "categorical_impute": list(CAT_IMPUTE), "encoding": list(ENCODING), "high_cardinality": list(HIGH_CARD),
                        "outliers": list(OUTLIERS), "log_transform": list(LOG), "cv_strategy": list(CV_STRATEGIES), "hpo_method": list(HPO_METHODS), "class_weight": list(CLASS_WEIGHT),
                        "rare_classes": list(RARE_CLASSES), "problem_type": ["auto", *Problem.ALL]},
            "metrics": {"classification": [{"key": k, "label": prob.METRIC_LABEL[k]} for k in prob.CLASS_SCORERS], "regression": [{"key": k, "label": prob.METRIC_LABEL[k]} for k in prob.REG_SCORERS]},
            "limits": {k: (v[i] if isinstance(v, tuple) else v) for k, v in LIMITS.items()},
            "explainability": {"permutation_importance": True, "native_importance": True, "partial_dependence": True, "local_effects": True, "shap": False,
                               "shap_note": "SHAP is not part of this build; local explanations use one-column-at-a-time replacement and are labelled as such."},
        }

    # ------------------------------------------------------------------ dataset schema for the objective picker
    def schema(self, principal: Principal, asset_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        asset, version, declared, types = self.analytics._open(principal, asset_id, version_id)
        from ..analytics.plan import role_of
        cols = []
        for c in declared:
            role = role_of(c["type"])
            hint = {"numeric": "number: predict it as a number (regression), or as categories if it has few distinct values",
                    "categorical": "category: predict it as a class (classification)",
                    "datetime": "date: cannot be predicted directly"}[role]
            cols.append({"name": c["name"], "type": c["type"], "role": role, "missing": c.get("missing", 0), "can_be_target": role != "datetime", "hint": hint})
        models = self.repo.models_for_asset(principal.user_id, asset["id"])
        return {"asset_id": asset["id"], "name": asset["name"], "version_id": version["id"], "version_number": version["version_number"], "rows": version["row_count"], "columns": cols,
                "models": len(models), "limits": {k: (v[1 if self._extended(principal) else 0] if isinstance(v, tuple) else v) for k, v in LIMITS.items()}}

    # ------------------------------------------------------------------ preparation preview
    def _precheck(self, cfg: Mapping[str, Any], types: Mapping[str, str]) -> None:
        """Rejects configurations that cannot work from the declared column types alone, before any data is read."""
        from ..analytics.plan import role_of
        role = role_of(types[cfg["target"]])
        if role == "datetime":
            raise unsuitable("TARGET_UNSUPPORTED", "A date column cannot be predicted directly. Predict a number or a category instead.")
        if cfg["problem_type"] == Problem.REGRESSION and role != "numeric":
            raise unsuitable("PROBLEM_INVALID", "Regression predicts a number, but this target contains text. Choose classification, or a numeric target.")
        kinds = {"regression"} if cfg["problem_type"] == Problem.REGRESSION else {"classification"} if (cfg["problem_type"] in Problem.CLASSIFICATION or role == "categorical") else {"classification", "regression"}
        if cfg["algorithms"] != "auto" and not any(set(CATALOG[a].kinds) & kinds for a in cfg["algorithms"]):
            raise unsuitable("ALGORITHM_INCOMPATIBLE", "None of the chosen algorithms can predict this kind of target: " + ", ".join(CATALOG[a].label for a in cfg["algorithms"][:4]) + ".")
        if cfg["primary_metric"] != "auto":
            if cfg["problem_type"] != "auto":
                prob.validate_metric(cfg["primary_metric"], cfg["problem_type"])
            elif role == "categorical" and cfg["primary_metric"] not in prob.CLASS_SCORERS:      # a text or yes/no target can only be classified
                prob.validate_metric(cfg["primary_metric"], Problem.BINARY)
        if cfg["validation"]["cv_strategy"] == "stratified_kfold" and cfg["problem_type"] == Problem.REGRESSION:
            raise unsuitable("CV_INVALID", "Stratified folds only apply to classification.")

    def assess(self, principal: Principal, asset_id: str, config: Any, version_id: Optional[str] = None) -> Dict[str, Any]:
        self.ctx.limiter.hit("ml:user", principal.user_id)
        asset, version, declared, types = self.analytics._open(principal, asset_id, version_id)
        cfg = parse_config(config, types, extended=self._extended(principal))
        self._precheck(cfg, types)
        out = self._run("assess", {"declared": declared, "rows_total": int(version["row_count"] or 0), "config": cfg,
                                   "snapshot_info": {"asset_id": asset["id"], "name": asset["name"], "version_id": version["id"], "version_number": version["version_number"],
                                                     "checksum_sha256": version["checksum_sha256"]}},
                        {"table.csv": lambda: self.storage.open(version["canonical_key"])}, principal, budget_s=60)
        result = out.result["assessment"]
        result["config"] = cfg
        result["isolation"] = out.isolation
        result["explanation"] = explain("ml:assessment", result)
        self._audit(principal, MEvent.ASSESSED, asset["id"], asset["project_id"], None, {"verdict": result["suitability"]["verdict"], "rows": result["dataset"]["rows"], "problem": result["problem"].get("chosen")})
        return result

    # ------------------------------------------------------------------ training jobs
    def start_training(self, principal: Principal, asset_id: str, config: Any, version_id: Optional[str] = None, label: Optional[str] = None) -> Dict[str, Any]:
        self.ctx.limiter.hit("ml_train:user", principal.user_id)
        if self.runner is None:
            raise AppError("ML_UNAVAILABLE", "Model training is not available right now.", 503)
        asset, version, declared, types = self.analytics._open(principal, asset_id, version_id)
        self.assets.get_owned(principal, asset["id"], writable=True)
        cfg = parse_config(config, types, extended=self._extended(principal))
        self._precheck(cfg, types)
        self.runner.recover_stale()                                 # a job lost to a restart must not block a new one
        if self.repo.active_jobs(principal.user_id) >= self._limit(principal, "max_active_jobs"):
            raise quota("TRAINING_BUSY", f"You already have {self._limit(principal, 'max_active_jobs')} training job(s) running. Wait for it to finish or cancel it.")
        if self.repo.count_models(principal.user_id) >= self._limit(principal, "max_models"):
            raise quota("MODEL_LIMIT", f"You have reached the limit of {self._limit(principal, 'max_models')} saved models. Delete one you no longer need.")
        job_id = new_id()
        now = self.ctx.clock.iso()
        text = json.dumps(cfg, sort_keys=True)
        self.repo.create_job(id=job_id, project_id=asset["project_id"], asset_id=asset["id"], version_id=version["id"], owner=principal.user_id, config=text,
                             config_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(), extended=self._extended(principal), label=clean_text(label, "label", maximum=120, required=False), now=now)
        self._audit(principal, MEvent.JOB_QUEUED, asset["id"], asset["project_id"], job_id, {"problem": cfg["problem_type"], "algorithms": len(cfg["algorithms"]) if cfg["algorithms"] != "auto" else 0,
                                                                                                                         "hpo": cfg["hpo"]["method"]})
        self.runner.submit(job_id)
        return self.get_job(principal, job_id)

    def present_job(self, row) -> Dict[str, Any]:
        cfg = _j(row["config"], {})
        return {"id": row["id"], "status": row["status"], "asset_id": row["asset_id"], "version_id": row["version_id"], "label": row["label"], "progress": _j(row["progress"], {}),
                "created_at": row["created_at"], "started_at": row["started_at"], "finished_at": row["finished_at"], "duration_ms": row["duration_ms"], "model_id": row["model_id"],
                "cancel_requested": bool(row["cancel_requested"]), "error": ({"code": row["error_code"], "message": row["error_message"]} if row["error_code"] else None),
                "config": {"target": cfg.get("target"), "problem_type": cfg.get("problem_type"), "algorithms": cfg.get("algorithms"), "hpo": (cfg.get("hpo") or {}).get("method"),
                           "test_size": (cfg.get("validation") or {}).get("test_size"), "cv_folds": (cfg.get("validation") or {}).get("cv_folds"), "seed": cfg.get("seed"),
                           "primary_metric": cfg.get("primary_metric"), "time_budget_s": cfg.get("time_budget_s")}}

    def _job_row(self, principal: Principal, job_id: Any):
        row = self.repo.job(principal.user_id, job_id) if isinstance(job_id, str) and 0 < len(job_id) <= 64 else None
        if row is None:
            raise not_found("training job")
        self.assets.get_owned(principal, row["asset_id"])
        return row

    def get_job(self, principal: Principal, job_id: str) -> Dict[str, Any]:
        if self.runner is not None:
            self.runner.recover_stale()
        row = self._job_row(principal, job_id)
        out = self.present_job(row)
        if row["status"] == JobStatus.COMPLETED and row["model_id"]:
            m = self.repo.model(principal.user_id, row["model_id"])
            if m is not None:
                out["model"] = self.present_model(m)
        return out

    def list_jobs(self, principal: Principal, asset_id: str, limit: int = 30) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        if self.runner is not None:
            self.runner.recover_stale()
        rows = self.repo.jobs_for_asset(principal.user_id, asset["id"], max(1, min(int(limit), 100)))
        return {"items": [self.present_job(r) for r in rows]}

    def cancel_job(self, principal: Principal, job_id: str) -> Dict[str, Any]:
        row = self._job_row(principal, job_id)
        if row["status"] in JobStatus.FINAL:
            raise conflict("JOB_FINISHED", "This job has already finished.")
        self.repo.request_cancel(principal.user_id, row["id"])
        return self.get_job(principal, job_id)

    # ------------------------------------------------------------------ models
    def present_model(self, row, *, detail: bool = False) -> Dict[str, Any]:
        metric = row["primary_metric"]
        out = {"id": row["id"], "asset_id": row["asset_id"], "project_id": row["project_id"], "version_id": row["version_id"], "job_id": row["job_id"], "name": row["name"],
               "model_version": row["model_version"], "target": row["target"], "problem_type": row["problem_type"], "algorithm": row["algorithm"],
               "algorithm_label": CATALOG[row["algorithm"]].label if row["algorithm"] in CATALOG else row["algorithm"], "tuned": bool(row["tuned"]), "status": row["status"],
               "primary_metric": metric, "primary_label": prob.METRIC_LABEL.get(metric, metric), "lower_is_better": metric.startswith("neg_"),
               "cv_score": shown(metric, row["cv_score"]), "test_score": shown(metric, row["test_score"]), "seed": row["seed"], "ml_version": row["ml_version"],
               "trained_at": row["trained_at"], "duration_ms": row["duration_ms"], "model_bytes": row["model_bytes"], "dataset": _j(row["dataset_snapshot"], {}),
               "metrics": _j(row["metrics"], {})}
        feats = _j(row["feature_config"], {})
        out["features"] = {"used": [f["name"] for f in feats.get("used", [])], "dropped": len(feats.get("dropped", []))}
        if detail:
            out.update(config=_j(row["config"], {}), feature_config=feats, preprocessing_config=_j(row["preprocessing_config"], {}), hyperparameters=_j(row["hyperparameters"], {}),
                       input_schema=_j(row["input_schema"], []), model_sha256=row["model_sha256"])
        return out

    def _model_row(self, principal: Principal, model_id: Any, *, writable: bool = False):
        row = self.repo.model(principal.user_id, model_id) if isinstance(model_id, str) and 0 < len(model_id) <= 64 else None
        if row is None:
            raise not_found("model")
        self.assets.get_owned(principal, row["asset_id"], writable=writable)       # a deleted dataset or project hides its models too
        return row

    def list_models(self, principal: Principal, asset_id: Optional[str] = None, *, target: Optional[str] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        if asset_id:
            asset = self.assets.get_owned(principal, asset_id)
            rows = self.repo.models_for_asset(principal.user_id, asset["id"], target=target)
            return {"items": [self.present_model(r) for r in rows], "total": len(rows)}
        limit = max(1, min(int(limit), 100))
        rows, total = self.repo.models_for_owner(principal.user_id, limit=limit, offset=max(0, int(offset)))
        return {"items": [self.present_model(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def get_model(self, principal: Principal, model_id: str) -> Dict[str, Any]:
        row = self._model_row(principal, model_id)
        out = self.present_model(row, detail=True)
        siblings = [self.present_model(r) for r in self.repo.models_for_asset(principal.user_id, row["asset_id"], target=row["target"])]
        out["versions"] = [{"id": s["id"], "model_version": s["model_version"], "name": s["name"], "algorithm_label": s["algorithm_label"], "trained_at": s["trained_at"],
                            "test_score": s["test_score"], "cv_score": s["cv_score"]} for s in siblings]
        jobs = self.repo.jobs_for_asset(principal.user_id, row["asset_id"], 30)
        out["training_history"] = [self.present_job(j) for j in jobs if _j(j["config"], {}).get("target") == row["target"]]
        return out

    def rename_model(self, principal: Principal, model_id: str, name: str) -> Dict[str, Any]:
        row = self._model_row(principal, model_id, writable=True)
        clean = clean_text(name, "name", maximum=120, required=True)           # control and direction-override characters are removed, like every other name in the product
        self.repo.rename_model(row["id"], clean)
        return self.get_model(principal, model_id)

    def report(self, principal: Principal, model_id: str) -> Dict[str, Any]:
        row = self._model_row(principal, model_id)
        try:
            report = json.loads(self.storage.read_bytes(row["report_key"]).decode("utf-8"))
        except (StorageError, ValueError):
            raise not_found("model") from None
        kinds = ["problem", "features", "ranking", "baseline", "evaluation", "importance", "overfitting"]
        if report.get("hpo"):
            kinds.append("hpo")
        if (report.get("learning_curve") or {}).get("available"):
            kinds.append("learning_curve")
        report["explanations"] = {k: explain(f"ml:{k}", report) for k in kinds}
        for p in (report.get("importance") or {}).get("partial_dependence", []) or []:
            p["explanation"] = explain("ml:pdp", {**p, "target": row["target"]})
        self._audit(principal, MEvent.EXPLAINED, row["asset_id"], row["project_id"], row["id"], {"sections": len(report["explanations"])})
        return report

    def delete_model(self, principal: Principal, model_id: str) -> Dict[str, Any]:
        row = self._model_row(principal, model_id, writable=True)
        try:
            self.storage.delete_prefix(keys.model_prefix(row["project_id"], row["asset_id"], row["id"]))
        except StorageError:
            raise AppError("STORAGE_ERROR", "The model could not be deleted right now. Please try again.", 503) from None
        self.repo.delete_model(row["id"])
        self._audit(principal, MEvent.MODEL_DELETED, row["asset_id"], row["project_id"], row["id"], {"algorithm": row["algorithm"]})
        return {"deleted": True, "id": row["id"]}

    def compare_models(self, principal: Principal, ids: Sequence[str]) -> Dict[str, Any]:
        if not isinstance(ids, (list, tuple)) or not 2 <= len(ids) <= 6 or len(set(ids)) != len(ids):
            raise config_invalid("ids", "Choose 2 to 6 different models to compare.")
        rows = [self._model_row(principal, i) for i in ids]
        if len({(r["asset_id"], r["target"]) for r in rows}) != 1:
            raise unsuitable("NOT_COMPARABLE", "Only models that predict the same target from the same dataset can be compared.")
        reports = []
        for r in rows:
            try:
                reports.append(json.loads(self.storage.read_bytes(r["report_key"]).decode("utf-8")))
            except (StorageError, ValueError):
                raise not_found("model") from None
        items = []
        for r, rep in zip(rows, reports):
            m = self.present_model(r)
            best = next((c for c in rep["candidates"] if c["id"] == rep["best"]["id"]), {})
            items.append({"id": r["id"], "name": r["name"], "model_version": r["model_version"], "algorithm_label": m["algorithm_label"], "tuned": m["tuned"], "trained_at": r["trained_at"],
                          "dataset_version_id": r["version_id"], "primary_metric": r["primary_metric"], "primary_label": m["primary_label"], "lower_is_better": m["lower_is_better"],
                          "cv_score": m["cv_score"], "test_score": m["test_score"], "metrics": rep["evaluation"]["metrics"], "n_features": len(m["features"]["used"]),
                          "features": m["features"]["used"], "seed": r["seed"], "test_rows": rep["data"]["test_rows"], "train_rows": rep["data"]["train_rows"],
                          "overfitting": (best.get("overfitting") or {}).get("level"), "fit_s": (best.get("timing") or {}).get("fit_s"), "model_bytes": r["model_bytes"],
                          "hyperparameters": _j(r["hyperparameters"], {}), "ml_version": r["ml_version"]})
        same_metric = len({i["primary_metric"] for i in items}) == 1
        same_split = len({(i["seed"], i["test_rows"], i["dataset_version_id"]) for i in items}) == 1
        notes = []
        if not same_metric:
            notes.append("The models were ranked by different measures, so their primary scores are not directly comparable. Compare the shared metrics below.")
        if not same_split:
            notes.append("The models were trained on different data versions or different train/test splits, so small differences in their scores may reflect the split rather than the model.")
        shared = sorted(set.intersection(*[set(i["metrics"]) for i in items]))
        result = {"asset_id": rows[0]["asset_id"], "target": rows[0]["target"], "models": items, "shared_metrics": shared, "same_primary_metric": same_metric, "same_split": same_split, "notes": notes}
        result["explanation"] = explain("ml:model_compare", result)
        return result

    # ------------------------------------------------------------------ predictions
    def _model_bytes(self, row) -> bytes:
        try:
            data = self.storage.read_bytes(row["model_key"])
        except StorageError:
            raise not_found("model") from None
        if hashlib.sha256(data).hexdigest() != row["model_sha256"]:
            raise AppError("MODEL_INTEGRITY", "The saved model file does not match its record, so it was not opened. Train the model again.", 409)
        return data

    def _background(self, row) -> Optional[bytes]:
        if not row["background_key"]:
            return None
        try:
            return self.storage.read_bytes(row["background_key"])
        except StorageError:
            return None

    def predict(self, principal: Principal, model_id: str, values: Any, *, explain_local: bool = True, save: bool = True) -> Dict[str, Any]:
        self.ctx.limiter.hit("ml_predict:user", principal.user_id)
        row = self._model_row(principal, model_id, writable=save)
        if not isinstance(values, dict):
            raise config_invalid("values", "Give the inputs as name/value pairs.")
        sources: Dict[str, Any] = {"model.joblib": self._model_bytes(row)}
        bg = self._background(row)
        if bg is not None and explain_local:
            sources["background.csv"] = bg
        out = self._run("predict", {"values": values, "explain": bool(explain_local), "seed": row["seed"]}, sources, principal, budget_s=30)
        result = out.result["prediction"]
        result["explanation"] = explain("ml:prediction", {**result, "target": row["target"], "primary_label": prob.METRIC_LABEL.get(row["primary_metric"], row["primary_metric"])})
        pid = None
        if save:
            pid = new_id()
            stored = {k: v for k, v in result.items() if k != "explanation"}
            self.repo.create_prediction(id=pid, project_id=row["project_id"], asset_id=row["asset_id"], model_id=row["id"], owner=principal.user_id, kind="single", source_asset_id=None,
                                        source_version_id=None, inputs=json.dumps(result["inputs"]), result=json.dumps(stored), output_key=None, output_bytes=None, rows=1, now=self.ctx.clock.iso())
            self.repo.trim_single_predictions(row["id"], MAX_PREDICTION_HISTORY)
        result["prediction_id"] = pid
        self._audit(principal, MEvent.PREDICTED, row["asset_id"], row["project_id"], row["id"], {"issues": len(result.get("input_issues", [])), "explained": bool(explain_local)})
        return result

    MAX_SCENARIOS = 5

    def what_if(self, principal: Principal, model_id: str, values: Any, scenarios: Any) -> Dict[str, Any]:
        """Compare the model's answer for one set of inputs with its answer when some inputs are changed. Every number comes from the same prediction path as a normal prediction
        (nothing is saved, no local explanation is computed). It shows how the MODEL responds to a change, never what would happen in the real world."""
        row = self._model_row(principal, model_id)
        if not isinstance(values, dict) or not values:
            raise config_invalid("values", "Give the starting inputs as name/value pairs.")
        if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= self.MAX_SCENARIOS:
            raise config_invalid("scenarios", f"Give between 1 and {self.MAX_SCENARIOS} scenarios.")
        names = {s["name"] for s in _j(row["input_schema"], [])}
        parsed = []
        for i, sc in enumerate(scenarios):
            changes = sc.get("changes") if isinstance(sc, dict) else None
            if not isinstance(changes, dict) or not changes or len(changes) > 20:
                raise config_invalid("scenarios", f"Scenario {i + 1} needs a list of changed inputs.")
            unknown = [c for c in changes if c not in names]
            if unknown:
                raise AppError("UNKNOWN_INPUT", f"Scenario {i + 1} changes inputs this model does not use: {', '.join(str(u) for u in unknown[:5])}.", 422)
            label = clean_text(sc.get("name"), "name", maximum=80, required=False) or f"Scenario {i + 1}"
            parsed.append((label, changes))
        base = self.predict(principal, model_id, values, explain_local=False, save=False)
        classification = "label" in base["prediction"]
        out_rows = []
        for label, changes in parsed:
            res = self.predict(principal, model_id, {**values, **changes}, explain_local=False, save=False)
            item: Dict[str, Any] = {"name": label, "changes": changes, "prediction": res["prediction"], "input_issues": res.get("input_issues", [])}
            if classification:
                item["label_changed"] = res["prediction"]["label"] != base["prediction"]["label"]
                b, r = base.get("positive"), res.get("positive")
                if b and r and b.get("class") == r.get("class"):
                    item["positive_class"] = b["class"]
                    item["probability_change"] = round(r["probability"] - b["probability"], 5)
            else:
                item["change"] = round(res["prediction"]["value"] - base["prediction"]["value"], 6)
            out_rows.append(item)
        return {"model_id": row["id"], "target": row["target"], "inputs": base.get("inputs", values), "baseline": {"prediction": base["prediction"], "positive": base.get("positive"), "input_issues": base.get("input_issues", [])},
                "scenarios": out_rows,
                "limits": ["This shows how the model's answer changes when inputs change. It does not show what would happen in the real world: a model learns patterns, not causes.",
                           "Changes far outside the training data are less reliable; check the input notes for each scenario.",
                           "Scenarios are not saved."]}

    def predict_batch(self, principal: Principal, model_id: str, dataset_asset_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        self.ctx.limiter.hit("ml_predict:user", principal.user_id)
        row = self._model_row(principal, model_id, writable=True)
        asset, version, declared, types = self.analytics._open(principal, dataset_asset_id, version_id)
        if asset["project_id"] != row["project_id"]:
            raise not_found("file")                                         # a model is only ever applied to data of its own project
        max_rows = self._limit(principal, "max_batch_rows")
        if int(version["row_count"] or 0) > max_rows:
            raise too_large(f"This dataset has {int(version['row_count']):,} rows; your limit for one batch is {max_rows:,}.")
        needed = [s["name"] for s in _j(row["input_schema"], [])]
        absent = [n for n in needed if n not in types]
        if absent:
            raise unsuitable("MISSING_COLUMNS", f"The dataset does not have the columns this model needs: {', '.join(absent[:8])}{'...' if len(absent) > 8 else ''}.")
        out = self._run("predict_batch", {"declared": declared, "rows_total": int(version["row_count"] or 0), "max_rows": max_rows},
                        {"table.csv": lambda: self.storage.open(version["canonical_key"]), "model.joblib": self._model_bytes(row)}, principal, budget_s=120, collect=("predictions.csv",))
        csv_bytes = out.files["predictions.csv"]
        pid = new_id()
        key = keys.prediction_key(row["project_id"], row["asset_id"], row["id"], pid)
        self.storage.put(key, [csv_bytes], overwrite=True)
        summary = out.result["summary"]
        summary["source"] = {"asset_id": asset["id"], "name": asset["name"], "version_id": version["id"], "version_number": version["version_number"]}
        self.repo.create_prediction(id=pid, project_id=row["project_id"], asset_id=row["asset_id"], model_id=row["id"], owner=principal.user_id, kind="batch", source_asset_id=asset["id"],
                                    source_version_id=version["id"], inputs=None, result=json.dumps(summary), output_key=key, output_bytes=len(csv_bytes), rows=summary["rows"], now=self.ctx.clock.iso())
        self._audit(principal, MEvent.BATCH_PREDICTED, row["asset_id"], row["project_id"], row["id"], {"rows": summary["rows"], "flagged": summary["rows_with_warnings"]})
        return self._present_prediction(self.repo.prediction(principal.user_id, pid), row)

    def _present_prediction(self, row, model) -> Dict[str, Any]:
        """The explanation is computed from the stored result each time it is read (it is never stored), so it always matches the stored figures."""
        result = _j(row["result"], {})
        out = {"id": row["id"], "model_id": row["model_id"], "kind": row["kind"], "created_at": row["created_at"], "rows": row["rows"], "source_asset_id": row["source_asset_id"],
               "downloadable": bool(row["output_key"]), "output_bytes": row["output_bytes"]}
        if row["kind"] == "single":
            out["inputs"] = _j(row["inputs"], {})
            out["result"] = {**result, "explanation": explain("ml:prediction", {**result, "target": model["target"]})}
        else:
            out["summary"] = {**result, "explanation": explain("ml:batch", {**result, "target": model["target"]})}
        return out

    def list_predictions(self, principal: Principal, model_id: str, *, kind: Optional[str] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        row = self._model_row(principal, model_id)
        if kind not in (None, "single", "batch"):
            raise config_invalid("kind", "Use 'single' or 'batch'.")
        limit = max(1, min(int(limit), 100))
        rows, total = self.repo.predictions_for_model(principal.user_id, row["id"], kind=kind, limit=limit, offset=max(0, int(offset)))
        return {"items": [self._present_prediction(r, row) for r in rows], "total": total, "limit": limit, "offset": offset}

    def _prediction_row(self, principal: Principal, prediction_id: Any):
        row = self.repo.prediction(principal.user_id, prediction_id) if isinstance(prediction_id, str) and 0 < len(prediction_id) <= 64 else None
        if row is None:
            raise not_found("prediction")
        self._model_row(principal, row["model_id"])
        return row

    def get_prediction(self, principal: Principal, prediction_id: str) -> Dict[str, Any]:
        row = self._prediction_row(principal, prediction_id)
        return self._present_prediction(row, self.repo.model(principal.user_id, row["model_id"]))

    def download_prediction(self, principal: Principal, prediction_id: str) -> Tuple[str, str, bytes]:
        self.ctx.limiter.hit("ml_export:user", principal.user_id)
        row = self._prediction_row(principal, prediction_id)
        if not row["output_key"]:
            raise not_found("prediction file")
        try:
            data = self.storage.read_bytes(row["output_key"])
        except StorageError:
            raise not_found("prediction file") from None
        m = self.repo.model(principal.user_id, row["model_id"])
        self._audit(principal, MEvent.PREDICTION_DOWNLOADED, row["asset_id"], row["project_id"], row["id"], {"bytes": len(data)})
        return f"predictions-{row['id'][:8]}.csv", "text/csv; charset=utf-8", data

    def partial_dependence(self, principal: Principal, model_id: str, feature: str) -> Dict[str, Any]:
        self.ctx.limiter.hit("ml:user", principal.user_id)
        row = self._model_row(principal, model_id)
        if not isinstance(feature, str) or len(feature) > 300:
            raise config_invalid("feature", "Choose one of the model's input columns.")
        bg = self._background(row)
        if bg is None:
            raise unsuitable("NO_REFERENCE_ROWS", "No reference rows are stored with this model, so this chart cannot be drawn.")
        out = self._run("pdp", {"feature": feature, "seed": row["seed"]}, {"model.joblib": self._model_bytes(row), "background.csv": bg}, principal, budget_s=60)
        res = out.result["partial_dependence"]
        res["explanation"] = explain("ml:pdp", {**res, "target": row["target"]})
        return res
