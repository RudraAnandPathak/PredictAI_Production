#!/usr/bin/env python3
"""Run due report schedules and retry failed deliveries. PredictAI does not start any hidden background scheduler: run this from cron, launchd or a systemd timer.

    python scripts/run_schedules.py            # one pass, then exit (use this from cron, for example every 5 minutes)
    python scripts/run_schedules.py --loop 60  # keep running, one pass every 60 seconds

Safe to run from several places at once: every run and delivery has an idempotency key, so nothing is sent twice. Skips work while maintenance mode is on."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.clock import Clock  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db import Database, open_database  # noqa: E402,F401
from app.modules.identity.container import build_services  # noqa: E402
from app.modules.identity.email import build_email_sender  # noqa: E402
from app.modules.identity.passwords import PasswordHasher  # noqa: E402
from app.modules.phase7 import build_phase7  # noqa: E402
from app.modules.storage import LocalStorage  # noqa: E402
from app.modules.workspace.container import build_workspace  # noqa: E402


def one_pass(settings, db, clock, hasher, sender, storage) -> dict:
    db.ensure_ready(auto_migrate=settings.auto_migrate)
    with db.session() as conn:
        services = build_services(settings=settings, conn=conn, clock=clock, hasher=hasher, sender=sender)
        ws = build_workspace(services.ctx, storage)
        p7 = build_phase7(ws, None)
        paused = conn.execute("SELECT active FROM own_maintenance WHERE id = 1").fetchone()
        if paused is not None and paused["active"]:
            return {"skipped": "maintenance mode is on"}
        return p7.schedule.tick()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", type=int, default=0, help="seconds between passes; 0 runs once")
    args = ap.parse_args()
    settings = get_settings()
    db, clock, hasher = open_database(settings), Clock(), PasswordHasher(*settings.password_hash_params)
    sender, storage = build_email_sender(settings), LocalStorage(settings.storage_dir)
    while True:
        print(one_pass(settings, db, clock, hasher, sender, storage), flush=True)
        if not args.loop:
            return 0
        time.sleep(max(5, args.loop))


if __name__ == "__main__":
    raise SystemExit(main())
