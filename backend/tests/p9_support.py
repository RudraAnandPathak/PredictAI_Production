"""Phase 9 test support: a harness with Special access granted, a dev root in a temp dir, and a worker that runs jobs in-thread."""
import os
import time
from dataclasses import replace

from app.modules.devspace.engine import JobService, Worker
from app.modules.devspace.files import FileService
from support import Harness

PW = "correct-horse-battery-staple"


class DevHarness:
    def __init__(self, **overrides):
        self.h = Harness()
        self.root = os.path.join(self.h._tmp.name, "dev")
        self.h.settings = replace(self.h.settings, dev_execution=overrides.pop("dev_execution", "local_trusted"), dev_root=self.root, dev_git_allow_local=True, **overrides)
        self.owner, _ = self.h.make_owner()
        self._workers = []

    def close(self):
        for w in self._workers:
            w.close()
        self.h.close()

    def user(self, email, special=True):
        p, _ = self.h.make_user(email)
        if special:
            self.h.services().codes.grant_special(self.owner, p.user_id)
            p = self.h.login(email, PW).principal
        return p

    def refresh(self, email):
        return self.h.login(email, PW).principal

    def ctx(self):
        return self.h.services().ctx

    def ws(self):
        return self.h.workspace()

    def svc(self, cls):
        s = self.h.services()
        return cls(s.ctx, self.h.workspace(s))

    def project(self, principal, name="Dev project"):
        return self.ws().projects.create(principal, name)["id"]

    def worker(self):
        w = Worker(self.h.db, self.h.settings, self.h.clock, poll=0.01)
        self._workers.append(w)
        return w

    def run_all(self, worker=None, limit=20):
        w = worker or self.worker()
        done = []
        for _ in range(limit):
            j = w.tick()
            if j is None:
                break
            done.append(j)
        return done
