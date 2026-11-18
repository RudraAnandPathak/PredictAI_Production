"""All SQL for the predictive tables (migration 0004). Every read that returns user data is filtered by owner; the few internal reads that are not
(the job runner claiming its own job) are named `*_internal`."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

Row = sqlite3.Row


class PredictiveRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Row]:
        return self.conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: Sequence[Any] = ()) -> List[Row]:
        return self.conn.execute(sql, params).fetchall()

    # ---------------------------------------------------------------- jobs
    def create_job(self, *, id: str, project_id: str, asset_id: str, version_id: str, owner: str, config: str, config_hash: str, extended: bool, label: Optional[str], now: str) -> None:
        self.conn.execute(
            "INSERT INTO ml_jobs (id, project_id, asset_id, version_id, owner_user_id, kind, status, config, config_hash, extended, label, progress, heartbeat_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'train', 'queued', ?, ?, ?, ?, ?, ?, ?)",
            (id, project_id, asset_id, version_id, owner, config, config_hash, 1 if extended else 0, label, '{"phase": "queued", "message": "Waiting to start", "fraction": 0}', now, now))

    def job(self, owner: str, job_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM ml_jobs WHERE id = ? AND owner_user_id = ?", (job_id, owner))

    def job_internal(self, job_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM ml_jobs WHERE id = ?", (job_id,))

    def jobs_for_asset(self, owner: str, asset_id: str, limit: int) -> List[Row]:
        return self._all("SELECT * FROM ml_jobs WHERE asset_id = ? AND owner_user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (asset_id, owner, limit))

    def active_jobs(self, owner: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM ml_jobs WHERE owner_user_id = ? AND status IN ('queued', 'running')", (owner,))["n"]

    def claim_job(self, job_id: str, now: str) -> bool:
        """queued -> running, exactly once, even if two threads race for the same job."""
        return self.conn.execute("UPDATE ml_jobs SET status = 'running', started_at = ?, heartbeat_at = ? WHERE id = ? AND status = 'queued' AND cancel_requested = 0", (now, now, job_id)).rowcount == 1

    def set_progress(self, job_id: str, progress: str, now: str) -> None:
        self.conn.execute("UPDATE ml_jobs SET progress = ?, heartbeat_at = ? WHERE id = ? AND status = 'running'", (progress, now, job_id))

    def heartbeat(self, job_id: str, now: str) -> None:
        self.conn.execute("UPDATE ml_jobs SET heartbeat_at = ? WHERE id = ? AND status = 'running'", (now, job_id))

    def request_cancel(self, owner: str, job_id: str) -> bool:
        return self.conn.execute("UPDATE ml_jobs SET cancel_requested = 1 WHERE id = ? AND owner_user_id = ? AND status IN ('queued', 'running')", (job_id, owner)).rowcount == 1

    def cancel_state(self, job_id: str) -> Optional[bool]:
        """True if cancellation was requested, False if not, None if the job no longer exists (its dataset or project was deleted)."""
        row = self._one("SELECT cancel_requested FROM ml_jobs WHERE id = ?", (job_id,))
        return None if row is None else bool(row["cancel_requested"])

    def finish_job(self, job_id: str, *, status: str, now: str, duration_ms: Optional[int], error_code: Optional[str] = None, error_message: Optional[str] = None,
                   model_id: Optional[str] = None, progress: Optional[str] = None) -> bool:
        sets = "status = ?, finished_at = ?, duration_ms = ?, error_code = ?, error_message = ?, model_id = ?"
        params: List[Any] = [status, now, duration_ms, error_code, (error_message or None) and error_message[:500], model_id]
        if progress is not None:
            sets += ", progress = ?"
            params.append(progress)
        params.append(job_id)
        return self.conn.execute(f"UPDATE ml_jobs SET {sets} WHERE id = ? AND status IN ('queued', 'running')", params).rowcount == 1

    def stale_jobs(self, *, running_before: str, queued_before: str) -> List[Row]:
        return self._all("SELECT * FROM ml_jobs WHERE (status = 'running' AND COALESCE(heartbeat_at, created_at) < ?) OR (status = 'queued' AND created_at < ?)", (running_before, queued_before))

    # ---------------------------------------------------------------- models
    def create_model(self, **f: Any) -> None:
        cols = ("id", "project_id", "asset_id", "version_id", "owner_user_id", "job_id", "name", "model_version", "target", "problem_type", "algorithm", "tuned", "primary_metric",
                "cv_score", "test_score", "seed", "ml_version", "config", "feature_config", "preprocessing_config", "hyperparameters", "metrics", "input_schema", "dataset_snapshot",
                "model_key", "model_sha256", "model_bytes", "report_key", "background_key", "duration_ms", "trained_at", "created_at")
        self.conn.execute(f"INSERT INTO ml_models ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [f[c] for c in cols])

    def model(self, owner: str, model_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM ml_models WHERE id = ? AND owner_user_id = ?", (model_id, owner))

    def models_for_asset(self, owner: str, asset_id: str, *, target: Optional[str] = None) -> List[Row]:
        if target:
            return self._all("SELECT * FROM ml_models WHERE asset_id = ? AND owner_user_id = ? AND target = ? ORDER BY created_at DESC, rowid DESC", (asset_id, owner, target))
        return self._all("SELECT * FROM ml_models WHERE asset_id = ? AND owner_user_id = ? ORDER BY created_at DESC, rowid DESC", (asset_id, owner))

    # a model is visible only while its dataset and project are: the same rule as AssetService.get_owned, so a listing never shows what a lookup would refuse
    _VISIBLE = ("FROM ml_models m JOIN assets a ON a.id = m.asset_id AND a.status != 'deleting' "
                "JOIN projects p ON p.id = m.project_id AND p.status IN ('active', 'archived') WHERE m.owner_user_id = ?")

    def models_for_owner(self, owner: str, *, limit: int, offset: int) -> Tuple[List[Row], int]:
        total = self._one(f"SELECT COUNT(*) AS n {self._VISIBLE}", (owner,))["n"]
        rows = self._all(f"SELECT m.* {self._VISIBLE} ORDER BY m.created_at DESC, m.rowid DESC LIMIT ? OFFSET ?", (owner, limit, offset))
        return rows, total

    def count_models(self, owner: str) -> int:
        return self._one("SELECT COUNT(*) AS n FROM ml_models WHERE owner_user_id = ?", (owner,))["n"]

    def next_model_version(self, asset_id: str, target: str) -> int:
        row = self._one("SELECT COALESCE(MAX(model_version), 0) + 1 AS v FROM ml_models WHERE asset_id = ? AND target = ?", (asset_id, target))
        return int(row["v"])

    def rename_model(self, model_id: str, name: str) -> None:
        self.conn.execute("UPDATE ml_models SET name = ? WHERE id = ?", (name, model_id))

    def delete_model(self, model_id: str) -> None:
        self.conn.execute("DELETE FROM ml_predictions WHERE model_id = ?", (model_id,))
        self.conn.execute("DELETE FROM ml_models WHERE id = ?", (model_id,))

    # ---------------------------------------------------------------- predictions
    def create_prediction(self, *, id: str, project_id: str, asset_id: str, model_id: str, owner: str, kind: str, source_asset_id: Optional[str], source_version_id: Optional[str],
                          inputs: Optional[str], result: str, output_key: Optional[str], output_bytes: Optional[int], rows: Optional[int], now: str) -> None:
        self.conn.execute(
            "INSERT INTO ml_predictions (id, project_id, asset_id, model_id, owner_user_id, kind, source_asset_id, source_version_id, inputs, result, output_key, output_bytes, rows, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (id, project_id, asset_id, model_id, owner, kind, source_asset_id, source_version_id, inputs, result, output_key, output_bytes, rows, now))

    def prediction(self, owner: str, prediction_id: str) -> Optional[Row]:
        return self._one("SELECT * FROM ml_predictions WHERE id = ? AND owner_user_id = ?", (prediction_id, owner))

    def predictions_for_model(self, owner: str, model_id: str, *, kind: Optional[str], limit: int, offset: int) -> Tuple[List[Row], int]:
        where, params = "model_id = ? AND owner_user_id = ?", [model_id, owner]
        if kind:
            where += " AND kind = ?"
            params.append(kind)
        total = self._one(f"SELECT COUNT(*) AS n FROM ml_predictions WHERE {where}", params)["n"]
        rows = self._all(f"SELECT * FROM ml_predictions WHERE {where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?", params + [limit, offset])
        return rows, total

    def trim_single_predictions(self, model_id: str, keep: int) -> List[str]:
        """Keeps the newest `keep` single predictions of a model; returns nothing to delete from storage because singles have no stored file."""
        self.conn.execute("DELETE FROM ml_predictions WHERE model_id = ? AND kind = 'single' AND id NOT IN (SELECT id FROM ml_predictions WHERE model_id = ? AND kind = 'single' "
                          "ORDER BY created_at DESC, rowid DESC LIMIT ?)", (model_id, model_id, keep))
        return []

    # ---------------------------------------------------------------- purge (called by workspace deletion, before the asset rows go)
    def purge_asset(self, asset_id: str) -> Dict[str, int]:
        counts = {}
        for table in ("ml_predictions", "ml_models", "ml_jobs"):
            counts[table] = self.conn.execute(f"DELETE FROM {table} WHERE asset_id = ?", (asset_id,)).rowcount
        return counts
