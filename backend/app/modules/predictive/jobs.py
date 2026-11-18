"""Background execution of training jobs.

A job is a row in `ml_jobs` plus a task in a bounded thread pool. The thread opens its OWN database connection, runs the modelling task in the sandboxed
subprocess (see `tasks.py`), mirrors progress and a heartbeat into the row, and, when the task finishes, stores the model files and the model row.

Guarantees (each is tested):
  * at most `workers` jobs run at once and each person has at most `max_active_jobs` queued or running (checked at submission);
  * cancel is a flag in the database that the thread polls; the subprocess' process group is killed within about a second;
  * a job whose process disappeared (server restart, crash) is marked failed with JOB_INTERRUPTED once its heartbeat is stale, never left 'running' forever;
  * a job whose dataset or project is deleted while it runs is cancelled and leaves no model behind;
  * the thread only reads what the job row names, and every row it reads is filtered by the job's owner, so one person's job can never touch another's data."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Dict, Optional

from ...core.clock import Clock, to_iso
from ...core.config import Settings
from ...db.database import Database, transaction
from ..identity.audit import AuditLog
from ..identity.context import RequestMeta
from ..identity.repository import Repository as IdentityRepository
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from ..workspace import keys
from ..workspace.repository import WorkspaceRepository
from . import tasks
from .constants import JobStatus, MEvent
from .errors import Cancelled, MlError
from .repository import PredictiveRepository

logger = logging.getLogger("predictai.ml")

RUNNING_STALE_S = 60                 # a running job whose heartbeat is older than this, and which no thread of this process owns, has been lost
QUEUED_STALE_S = 30 * 60
HEARTBEAT_EVERY_S = 1.0


class JobRunner:
    def __init__(self, db: Database, storage: StorageBackend, settings: Settings, clock: Clock, *, workers: Optional[int] = None) -> None:
        self.db, self.storage, self.settings, self.clock = db, storage, settings, clock
        self._pool = ThreadPoolExecutor(max_workers=workers or settings.ml_workers, thread_name_prefix="ml-job")
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- scheduling
    def submit(self, job_id: str) -> None:
        with self._lock:
            fut = self._pool.submit(self._run_guarded, job_id)
            self._futures[job_id] = fut
            fut.add_done_callback(lambda _f, j=job_id: self._forget(j))

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def owns(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._futures

    def wait(self, job_id: str, timeout: float = 120.0) -> None:
        """Test helper: block until the job's thread has finished."""
        with self._lock:
            fut = self._futures.get(job_id)
        if fut is not None:
            fut.result(timeout=timeout)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- recovery
    def recover_stale(self) -> int:
        """Marks jobs that no live thread is running as failed. Safe to call often (one indexed query)."""
        now = self.clock.now()
        n = 0
        with self.db.session() as conn:
            repo = PredictiveRepository(conn)
            for job in repo.stale_jobs(running_before=to_iso(now - timedelta(seconds=RUNNING_STALE_S)), queued_before=to_iso(now - timedelta(seconds=QUEUED_STALE_S))):
                if self.owns(job["id"]):
                    continue
                if repo.finish_job(job["id"], status=JobStatus.FAILED, now=to_iso(now), duration_ms=None, error_code="JOB_INTERRUPTED",
                                   error_message="The server stopped while this training was running. Nothing was saved; you can start it again.",
                                   progress=json.dumps({"phase": "failed", "message": "Interrupted", "fraction": 0})):
                    n += 1
                    self._audit(conn, MEvent.JOB_FAILED, job, "failure", {"code": "JOB_INTERRUPTED"})
        return n

    # ---------------------------------------------------------------- the job
    def _run_guarded(self, job_id: str) -> None:
        try:
            self._run(job_id)
        except Exception as exc:  # noqa: BLE001  a bug must end the job visibly, not leave it 'running'
            # Library errors can quote a cell value ("could not convert 'abc' to float"), so the message is never logged: only the type and where it happened.
            frame = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
            logger.error("Unexpected %s in training job at %s:%s", type(exc).__name__, frame.filename.rsplit("/", 1)[-1] if frame else "?", frame.lineno if frame else "?")
            try:
                with self.db.session() as conn:
                    PredictiveRepository(conn).finish_job(job_id, status=JobStatus.FAILED, now=self.clock.iso(), duration_ms=None, error_code="ML_ERROR",
                                                          error_message="The training could not be completed.", progress=json.dumps({"phase": "failed", "message": "Failed", "fraction": 0}))
            except Exception:  # noqa: BLE001
                logger.exception("Could not record the failure of a training job")

    def _audit(self, conn, event: str, job, outcome: str = "success", meta: Optional[Dict[str, Any]] = None) -> None:
        try:
            AuditLog(IdentityRepository(conn), self.clock, RequestMeta()).record(event, outcome, actor=job["owner_user_id"], target=job["id"],
                                                                                 metadata={"asset_id": job["asset_id"], "project_id": job["project_id"], **(meta or {})})
        except Exception:  # noqa: BLE001  the audit write must never hide the job's own outcome
            logger.warning("Audit write failed for a training job", exc_info=True)

    def _run(self, job_id: str) -> None:
        started = time.monotonic()
        with self.db.session() as conn:
            repo, wrepo = PredictiveRepository(conn), WorkspaceRepository(conn)
            job = repo.job_internal(job_id)
            if job is None:
                return
            if job["cancel_requested"] and job["status"] == JobStatus.QUEUED:
                repo.finish_job(job_id, status=JobStatus.CANCELLED, now=self.clock.iso(), duration_ms=0, progress=json.dumps({"phase": "cancelled", "message": "Cancelled before it started", "fraction": 0}))
                self._audit(conn, MEvent.JOB_CANCELLED, job)
                return
            if not repo.claim_job(job_id, self.clock.iso()):
                return
            self._audit(conn, MEvent.JOB_STARTED, job)
            cfg = json.loads(job["config"])
            owner = job["owner_user_id"]
            asset = wrepo.asset(owner, job["asset_id"])
            version = wrepo.version(owner, job["version_id"])
            extraction = wrepo.extraction_for_version(owner, job["version_id"])
            if asset is None or version is None or extraction is None or not version["canonical_key"]:
                repo.finish_job(job_id, status=JobStatus.CANCELLED, now=self.clock.iso(), duration_ms=0, progress=json.dumps({"phase": "cancelled", "message": "The dataset is no longer available", "fraction": 0}))
                self._audit(conn, MEvent.JOB_CANCELLED, job, meta={"reason": "dataset_gone"})
                return
            declared = list(json.loads(extraction["summary"] or "{}").get("columns", []))
            extended = bool(job["extended"])
            last_beat = {"t": 0.0}

            def is_cancelled() -> bool:
                state = repo.cancel_state(job_id)
                return state is None or state

            def on_progress(d: Dict[str, Any]) -> None:
                now = time.monotonic()
                repo.set_progress(job_id, json.dumps(d), self.clock.iso())
                last_beat["t"] = now

            def beat() -> None:
                now = time.monotonic()
                if now - last_beat["t"] >= HEARTBEAT_EVERY_S:
                    last_beat["t"] = now
                    repo.heartbeat(job_id, self.clock.iso())

            dataset = {"asset_id": asset["id"], "name": asset["name"], "version_id": version["id"], "version_number": version["version_number"], "checksum_sha256": version["checksum_sha256"],
                       "rows": version["row_count"], "columns": len(declared)}
            try:
                out = tasks.run_task("train", {"declared": declared, "rows_total": int(version["row_count"] or 0), "config": cfg, "dataset": dataset},
                                     {"table.csv": lambda: self.storage.open(version["canonical_key"])}, extended=extended, isolation=self.settings.ml_isolation,
                                     budget_s=float(cfg.get("time_budget_s") or tasks.limits_for(extended)["time_budget_s"]), on_progress=on_progress, is_cancelled=is_cancelled,
                                     heartbeat=beat, collect=("model.joblib", "background.csv"))
                if is_cancelled():
                    raise Cancelled()
                model_id = self._store(conn, repo, job, cfg, out, dataset, started)
            except Cancelled:
                repo.finish_job(job_id, status=JobStatus.CANCELLED, now=self.clock.iso(), duration_ms=int((time.monotonic() - started) * 1000),
                                progress=json.dumps({"phase": "cancelled", "message": "Cancelled", "fraction": 0}))
                self._audit(conn, MEvent.JOB_CANCELLED, job)
                return
            except MlError as e:
                repo.finish_job(job_id, status=JobStatus.FAILED, now=self.clock.iso(), duration_ms=int((time.monotonic() - started) * 1000), error_code=e.code, error_message=e.message,
                                progress=json.dumps({"phase": "failed", "message": e.message[:200], "fraction": 0}))
                self._audit(conn, MEvent.JOB_FAILED, job, "failure", {"code": e.code})
                return
            except StorageError:
                repo.finish_job(job_id, status=JobStatus.FAILED, now=self.clock.iso(), duration_ms=int((time.monotonic() - started) * 1000), error_code="STORAGE_ERROR",
                                error_message="The dataset or the model could not be read or saved.", progress=json.dumps({"phase": "failed", "message": "Storage error", "fraction": 0}))
                self._audit(conn, MEvent.JOB_FAILED, job, "failure", {"code": "STORAGE_ERROR"})
                return
            repo.finish_job(job_id, status=JobStatus.COMPLETED, now=self.clock.iso(), duration_ms=int((time.monotonic() - started) * 1000), model_id=model_id,
                            progress=json.dumps({"phase": "completed", "message": "Done", "fraction": 1.0}))
            self._audit(conn, MEvent.JOB_COMPLETED, job, meta={"model_id": model_id, "duration_ms": int((time.monotonic() - started) * 1000)})

    def _store(self, conn, repo: PredictiveRepository, job, cfg: Dict[str, Any], out: tasks.TaskOutput, dataset: Dict[str, Any], started: float) -> str:
        """Writes the model files, then the model row. Files first: a row never points at a file that is not there."""
        meta, report = out.result["meta"], out.result["report"]
        model_id = new_id()
        pid, aid = job["project_id"], job["asset_id"]
        blob, background = out.files["model.joblib"], out.files.get("background.csv")
        report["isolation"] = out.isolation
        report["model_id"] = model_id
        mk, rk, bk = keys.model_key(pid, aid, model_id), keys.model_report_key(pid, aid, model_id), keys.model_background_key(pid, aid, model_id)
        self.storage.put(mk, [blob], overwrite=True)
        self.storage.put(rk, [json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")], overwrite=True)
        if background:
            self.storage.put(bk, [background], overwrite=True)
        feats = report["features"]
        now = self.clock.iso()
        try:
            with transaction(conn):
                version_no = repo.next_model_version(aid, cfg["target"])
                repo.create_model(
                    id=model_id, project_id=pid, asset_id=aid, version_id=job["version_id"], owner_user_id=job["owner_user_id"], job_id=job["id"],
                    name=(job["label"] or f"{cfg['target']} model v{version_no}")[:120], model_version=version_no, target=cfg["target"], problem_type=meta["problem"],
                    algorithm=meta["algorithm"], tuned=1 if meta["tuned"] else 0, primary_metric=meta["primary_metric"], cv_score=meta["cv_primary"], test_score=meta["test_primary"],
                    seed=meta["seed"], ml_version=meta["ml_version"], config=job["config"], feature_config=json.dumps({"used": feats["used"], "dropped": feats["dropped"], "leakage": feats["leakage"]}),
                    preprocessing_config=json.dumps(feats["settings"]), hyperparameters=json.dumps(meta["hyperparameters"]),
                    metrics=json.dumps({"primary_metric": meta["primary_metric"], "primary_label": meta["primary_label"], "evaluation": report["evaluation"]["metrics"], "basis": report["evaluation"]["basis"]}),
                    input_schema=json.dumps(meta["input_schema"]), dataset_snapshot=json.dumps(dataset), model_key=mk, model_sha256=hashlib.sha256(blob).hexdigest(),
                    model_bytes=len(blob), report_key=rk, background_key=bk if background else None, duration_ms=int((time.monotonic() - started) * 1000), trained_at=now, created_at=now)
        except Exception:
            self.storage.delete_prefix(keys.model_prefix(pid, aid, model_id))
            raise
        return model_id
