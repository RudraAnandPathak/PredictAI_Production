#!/usr/bin/env python3
"""The Development Workspace worker: the only process that runs user code.

    python scripts/run_dev_worker.py            # run until stopped (Ctrl-C / SIGTERM stops after the current job is cleaned up)
    python scripts/run_dev_worker.py --once     # run queued work until the queue is empty, then exit

The web server never executes code. Without this worker, jobs stay `queued` and the interface says so. One worker runs one job at a time; start more only if you raised the concurrency limits."""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.clock import Clock  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import Database, open_database  # noqa: E402,F401
from app.modules.devspace import isolation  # noqa: E402
from app.modules.devspace.engine import Worker  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="drain the queue and exit")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between checks when idle")
    args = ap.parse_args()
    settings = get_settings()
    if settings.dev_execution == "off":
        print("PREDICTAI_DEV_EXECUTION is 'off': the worker will only block queued jobs. Set it to local_trusted or docker to run code.", file=sys.stderr)
    print("Isolation:", isolation.describe(settings))
    db = open_database(settings)                     # Phase 11: SQLite or Turso, from PREDICTAI_DATABASE_BACKEND
    db.ensure_ready(auto_migrate=settings.auto_migrate)
    worker = Worker(db, settings, Clock(), poll=args.poll)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    try:
        if args.once:
            while not stop["now"] and worker.tick() is not None:
                pass
        else:
            worker.run_forever(lambda: stop["now"])
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
