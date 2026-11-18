"""Background execution for research jobs: a bounded thread pool, each job on its own database connection. The pool, the queue limits and the cancel flag are the same ideas as
the Phase 5 training runner; the work itself is in `ResearchService` so it can be tested without threads."""
from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from .jobs import recover_stale

logger = logging.getLogger("predictai.research")


class ResearchRunner:
    def __init__(self, db: Any, factory: Callable[[sqlite3.Connection], Any], workers: int, clock: Any) -> None:
        self.db, self.factory, self.clock = db, factory, clock
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="research-job")
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, job_id: str) -> None:
        with self._lock:
            fut = self._pool.submit(self._run, job_id)
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

    def _run(self, job_id: str) -> None:
        try:
            with self.db.session() as conn:
                self.factory(conn).research.run_job(job_id)
        except Exception as exc:  # noqa: BLE001  run_job records its own failures; this only guards the thread
            logger.error("Research worker error: %s", type(exc).__name__)

    def recover_stale(self) -> int:
        with self.db.session() as conn:
            svc = self.factory(conn)
            return recover_stale(svc.research.repo, self.clock, svc.ctx.audit, self.owns)
