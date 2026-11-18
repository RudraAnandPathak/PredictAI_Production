#!/usr/bin/env python3
"""The Research Intelligence worker: reads, indexes and embeds the sources people add.

    python scripts/run_research_worker.py            # run until stopped (Ctrl-C / SIGTERM stops after the current job)
    python scripts/run_research_worker.py --once     # run queued work until the queue is empty, then exit

The web server never parses documents. Without this worker, new sources stay `queued` and the interface says so. Resource limits for parsing come from PREDICTAI_RI_* settings."""
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.clock import Clock  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import Database, open_database  # noqa: E402,F401
from app.modules.identity.email import build_email_sender  # noqa: E402
from app.modules.identity.passwords import PasswordHasher  # noqa: E402
from app.modules.research_intelligence.workers.worker import Worker  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="drain the queue and exit")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between checks when idle")
    args = ap.parse_args()
    settings = get_settings()
    db = open_database(settings)                     # Phase 11: SQLite or Turso, from PREDICTAI_DATABASE_BACKEND
    db.ensure_ready(auto_migrate=settings.auto_migrate)
    worker = Worker(db, settings, Clock(), PasswordHasher(*settings.password_hash_params), build_email_sender(settings), poll=args.poll)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    if args.once:
        while not stop["now"] and worker.tick() is not None:
            pass
    else:
        worker.run_forever(lambda: stop["now"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
