"""A read-only view of the models already trained in Phase 5, arranged the way a model registry is: what each model is, which dataset version it learned from, how it was evaluated, the
other versions, and how to read its explanation.

This is NOT the Model Registry requirement (Phase 7). It cannot deploy, promote, roll back or retire a model, and it keeps no experiment records beyond Phase 5's own training history.
Deployment state and rollback are reported as not supported, with the reason, so nobody mistakes a trained model for one that is live."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..identity.authorization import Principal

NOT_SUPPORTED = {"deployment": {"state": "not supported", "reason": "PredictAI does not deploy models. A trained model can be used for predictions inside this product only."},
                 "rollback": {"supported": False, "reason": "There is no deployment to roll back from. Older versions stay available to open, compare and predict with."},
                 "phase7": "Deployment state, promotion, rollback and experiment records are Model Registry requirements for Phase 7 (OUT-02)."}


class ModelRegistryView:
    def __init__(self, predictive: Any) -> None:
        self.predictive = predictive

    def capabilities(self) -> Dict[str, Any]:
        return {"read": ["metadata", "versions", "dataset lineage", "evaluation results", "explanations"], **NOT_SUPPORTED}

    @staticmethod
    def _entry(m: Dict[str, Any]) -> Dict[str, Any]:
        ds = m.get("dataset") or {}
        return {"id": m["id"], "name": m["name"], "model_version": m["model_version"], "target": m["target"], "problem_type": m["problem_type"], "algorithm": m["algorithm_label"],
                "trained_at": m["trained_at"], "status": m["status"], "seed": m["seed"],
                "lineage": {"project_id": m["project_id"], "asset_id": m["asset_id"], "dataset_version_id": m["version_id"], "dataset_checksum_sha256": ds.get("checksum_sha256"),
                            "dataset_name": ds.get("name"), "training_job_id": m["job_id"], "features_used": m["features"]["used"], "features_dropped": m["features"]["dropped"]},
                "evaluation": {"metric": m["primary_label"], "lower_is_better": m["lower_is_better"], "cross_validation": m["cv_score"], "held_out_test": m["test_score"],
                               "note": "Scores describe this model on this dataset's held-out rows. They are not a promise about future data."},
                **NOT_SUPPORTED}

    def models(self, principal: Principal, asset_id: Optional[str] = None, *, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        got = self.predictive.list_models(principal, asset_id, limit=limit, offset=offset)
        return {"items": [self._entry(m) for m in got["items"]], "total": got["total"]}

    def model(self, principal: Principal, model_id: str) -> Dict[str, Any]:
        m = self.predictive.get_model(principal, model_id)
        out = self._entry(m)
        out["versions"] = m["versions"]
        out["training_history"] = [{"id": j["id"], "status": j["status"], "created_at": j.get("created_at"), "finished_at": j.get("finished_at")} for j in m["training_history"]]
        out["explanation"] = {"available": True, "where": "The model report holds the explanation of the ranking, evaluation, importance and overfitting checks.", "report_endpoint": f"/models/{m['id']}/report"}
        return out
