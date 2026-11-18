"""Model monitoring for registered versions: input drift against the training data, and performance from outcomes the owner records.

Nothing here retrains, deploys or changes a model, and nothing is invented: a check with too little data says so (`insufficient_data`, `insufficient_outcomes`) instead of reporting a figure.
Concept drift is only ever reported from recorded outcomes. Only the model's owner can run checks or record outcomes (they read the owner's datasets and predictions)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.errors import AppError
from ..analytics.frame import load_snapshot
from ..collab.access import AccessResolver
from ..identity.authorization import Principal
from ..p7 import Service7, clean, conflict, invalid, jdump, jload, new_id, not_found, page
from . import drift as D

SAMPLE_ROWS = 20_000
MAX_ROWS = 500_000
MAX_CELLS = 20_000_000
EVENT_CHECK = "p7.monitoring.checked"
EVENT_OUTCOME = "p7.monitoring.outcome"


class MonitoringService(Service7):
    def __init__(self, ctx: Any, ws: Any, access: AccessResolver, registry: Any) -> None:
        super().__init__(ctx, ws)
        self.access, self.registry, self.storage = access, registry, ws.storage

    # ------------------------------------------------------------------ helpers
    def _owned_version(self, principal: Principal, version_id: Any):
        v = self.registry.repo.version(version_id)
        if v is None:
            raise not_found("model version")
        acc = self.access.resolve(principal, "model", v["model_id"], "view")
        if not acc.is_owner:
            raise not_found("model version")                       # a collaborator can read the registry, but monitoring reads the owner's own data
        return v

    def _frame(self, principal: Principal, asset_id: str, version_id: Optional[str]):
        asset, version, declared, types = self.ws.analytics.open_dataset(principal, asset_id, version_id)
        with self.storage.open(version["canonical_key"]) as stream:
            snap = load_snapshot(stream, declared, rows_total=int(version["row_count"] or 0), max_rows=MAX_ROWS, max_cells=MAX_CELLS, sample_rows=SAMPLE_ROWS, seed=0)
        return asset, version, snap.df

    # ------------------------------------------------------------------ drift
    def check_drift(self, principal: Principal, version_id: str, asset_id: str, dataset_version_id: Optional[str] = None) -> Dict[str, Any]:
        self.limit("monitor:user", principal)
        v = self._owned_version(principal, version_id)
        meta = jload(v["metadata"], {})
        lineage = meta.get("lineage") or {}
        ref_asset, ref_version = lineage.get("asset_id"), lineage.get("dataset_version_id")
        schema = meta.get("input_schema") or []
        roles = {s["name"]: s.get("role", "other") for s in schema if isinstance(s, dict) and s.get("name")}
        if not ref_asset or not roles:
            raise conflict("NO_REFERENCE", "This version has no recorded training dataset or input list, so there is nothing to compare with.")
        try:
            _, _, ref_df = self._frame(principal, ref_asset, ref_version)
        except AppError as e:
            raise conflict("REFERENCE_UNAVAILABLE", "The dataset this model was trained on is no longer available, so drift cannot be measured against it.") from e
        asset, version, cur_df = self._frame(principal, asset_id, dataset_version_id)
        if asset["project_id"] != meta.get("lineage", {}).get("project_id"):
            raise not_found("file")                                # data from another project is never compared
        result = D.compare(ref_df, cur_df, roles)
        result["reference"] = {"asset_id": ref_asset, "version_id": ref_version, "rows_used": int(len(ref_df))}
        result["current"] = {"asset_id": asset["id"], "name": asset["name"], "version_id": version["id"], "rows_used": int(len(cur_df))}
        now, cid = self.now(), new_id()
        with self.ctx.conn:
            self.conn.execute("INSERT INTO mon_checks (id, model_id, version_id, owner_user_id, reference_version_id, current_asset_id, current_version_id, status, result, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (cid, v["model_id"], v["id"], principal.user_id, ref_version, asset["id"], version["id"], result["status"], jdump(result), now))
        self.audit(EVENT_CHECK, principal, v["model_id"], version=v["version"], status=result["status"], drifted=len(result["drifted_columns"]))
        return {"id": cid, "version_id": v["id"], "created_at": now, **result}

    def checks(self, principal: Principal, version_id: str, *, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        v = self._owned_version(principal, version_id)
        lim, off = page(limit, offset)
        rows = self.conn.execute("SELECT id, status, created_at, current_asset_id, result FROM mon_checks WHERE version_id = ? AND owner_user_id = ? ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                                 (v["id"], principal.user_id, lim, off)).fetchall()
        items = [{"id": r["id"], "status": r["status"], "created_at": r["created_at"], "current_asset_id": r["current_asset_id"],
                  "drifted_columns": (jload(r["result"], {}) or {}).get("drifted_columns", [])} for r in rows]
        return {"version_id": v["id"], "items": items, "limit": lim, "offset": off}

    def get_check(self, principal: Principal, check_id: str) -> Dict[str, Any]:
        r = self.conn.execute("SELECT * FROM mon_checks WHERE id = ? AND owner_user_id = ?", (check_id, principal.user_id)).fetchone() if isinstance(check_id, str) else None
        if r is None:
            raise not_found("drift check")
        return {"id": r["id"], "version_id": r["version_id"], "created_at": r["created_at"], **jload(r["result"], {})}

    # ------------------------------------------------------------------ outcomes
    def record_outcome(self, principal: Principal, version_id: str, prediction_id: str, actual: Any) -> Dict[str, Any]:
        self.limit("monitor:user", principal)
        v = self._owned_version(principal, version_id)
        pred = self.conn.execute("SELECT * FROM ml_predictions WHERE id = ? AND owner_user_id = ? AND model_id = ? AND kind = 'single'",
                                 (prediction_id, principal.user_id, v["source_model_id"])).fetchone() if isinstance(prediction_id, str) else None
        if pred is None:
            raise not_found("prediction")
        problem = (jload(v["metadata"], {}) or {}).get("problem_type", "")
        text = actual if isinstance(actual, str) else (str(actual) if isinstance(actual, (int, float)) and not isinstance(actual, bool) else None)
        text = clean(text, "actual", maximum=120)
        if problem == "regression":
            try:
                float(text)
            except ValueError:
                raise invalid("actual", "Enter the real outcome as a number.") from None
        now, oid = self.now(), new_id()
        with self.ctx.conn:
            self.conn.execute("INSERT INTO mon_outcomes (id, version_id, prediction_id, owner_user_id, actual, recorded_at) VALUES (?,?,?,?,?,?) "
                              "ON CONFLICT(prediction_id) DO UPDATE SET actual = excluded.actual, recorded_at = excluded.recorded_at", (oid, v["id"], prediction_id, principal.user_id, text, now))
        self.audit(EVENT_OUTCOME, principal, v["model_id"], version=v["version"])
        return {"prediction_id": prediction_id, "actual": text, "recorded_at": now}

    def performance(self, principal: Principal, version_id: str) -> Dict[str, Any]:
        v = self._owned_version(principal, version_id)
        meta, ev = jload(v["metadata"], {}) or {}, jload(v["evaluation"], {}) or {}
        problem = meta.get("problem_type", "")
        kind = "regression" if problem == "regression" else "classification"
        rows = self.conn.execute("SELECT o.actual, p.result FROM mon_outcomes o JOIN ml_predictions p ON p.id = o.prediction_id WHERE o.version_id = ? AND o.owner_user_id = ? ORDER BY o.recorded_at, o.id",
                                 (v["id"], principal.user_id)).fetchall()
        actual: List[Any] = []
        predicted: List[Any] = []
        for r in rows:
            pr = (jload(r["result"], {}) or {}).get("prediction") or {}
            val = pr.get("value") if kind == "regression" else pr.get("label")
            if val is not None:
                actual.append(r["actual"]); predicted.append(val)
        base_metrics = ((ev.get("test") or {}).get("metrics")) or {}
        baseline = {"accuracy": base_metrics.get("accuracy"), "mae": base_metrics.get("mae")}
        out = D.performance(kind, actual, predicted, baseline)
        out.update({"version_id": v["id"], "family": kind,
                    "basis": "Outcomes the owner recorded against stored single predictions. Batch predictions have no recorded outcomes. The baseline is the hold-out score stored at registration."})
        return out

    def summary(self, principal: Principal, version_id: str) -> Dict[str, Any]:
        v = self._owned_version(principal, version_id)
        last = self.conn.execute("SELECT id, status, created_at FROM mon_checks WHERE version_id = ? AND owner_user_id = ? ORDER BY created_at DESC, id LIMIT 1", (v["id"], principal.user_id)).fetchone()
        perf = self.performance(principal, version_id)
        return {"version_id": v["id"], "last_drift_check": dict(last) if last else None, "performance": {k: perf[k] for k in ("status", "n", "note")},
                "limits": ["Monitoring runs when you ask for it; there is no background watcher.", "Input drift is not evidence of wrong predictions.", "PredictAI does not retrain or replace a model automatically."]}
