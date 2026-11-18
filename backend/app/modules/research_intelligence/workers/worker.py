"""The Research Intelligence worker: claims queued jobs and runs the ingestion pipeline. It is the only place documents are parsed. The web server never parses a file: it validates,
stores and queues. Without a worker, sources stay `queued` and the interface says so. One tick processes at most one job."""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Callable, Optional

from ....core.clock import Clock
from ....core.config import Settings
from ....db import Database
from ...identity.container import build_services
from ...identity.context import RequestMeta
from ...providers import AdapterFactory, BreakerBoard, ProviderService
from ..services.jobs import JobService
from ..services.pipeline import Pipeline


class Worker:
    def __init__(self, db: Database, settings: Settings, clock: Clock, hasher: Any, sender: Any, *, fetcher: Any = None, extractor: Optional[Callable[..., Any]] = None,
                 resolver: Any = None, poll: float = 1.0, worker_id: Optional[str] = None, router_factory_override: Optional[Callable[[Any], Any]] = None) -> None:
        self.db, self.settings, self.clock, self.hasher, self.sender = db, settings, clock, hasher, sender
        self.fetcher, self.extractor, self.resolver, self.poll = fetcher, extractor, resolver, poll
        self.worker_id = worker_id or f"{os.uname().nodename}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.board = BreakerBoard(clock)
        self.router_factory_override = router_factory_override

    def _pipeline(self, conn) -> Pipeline:
        ctx = build_services(settings=self.settings, conn=conn, clock=self.clock, hasher=self.hasher, sender=self.sender, meta=RequestMeta(request_id=f"ri-worker-{self.worker_id}")).ctx
        if self.router_factory_override is not None:
            rf = self.router_factory_override(ctx)
        else:
            rf = ProviderService(ctx, self.board, factory=AdapterFactory(resolver=self.resolver), resolver=self.resolver).router
        kwargs = {"extractor": self.extractor} if self.extractor else {}
        return Pipeline(ctx, fetcher=self.fetcher, router_factory=rf, **kwargs)

    def tick(self) -> Optional[str]:
        """Claim and run one job. Returns its outcome, or None when the queue is empty."""
        conn = self.db.connect()
        try:
            pipe = self._pipeline(conn)
            job = pipe.jobs.claim(self.worker_id)
            if job is None:
                return None
            return pipe.run(job)
        finally:
            conn.close()

    def run_forever(self, should_stop: Callable[[], bool]) -> None:
        while not should_stop():
            if self.tick() is None:
                time.sleep(self.poll)

    def close(self) -> None:
        pass
