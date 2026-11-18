"""Shared fixtures for identity tests: a real SQLite database, a controllable clock and an in-memory outbox."""
import re
import tempfile
from dataclasses import replace
from typing import Optional

from app.core.clock import ManualClock
from app.core.config import load_settings
from app.db import Database, migrate
from app.modules.identity.container import Services, build_services
from app.modules.identity.context import RequestMeta
from app.modules.identity.email import MemoryOutbox
from app.modules.identity.passwords import PasswordHasher

GOOD_PASSWORD = "correct-horse-battery-staple"
OTHER_PASSWORD = "another-long-passphrase-42"


def make_test_database(path):
    """SQLite by default. With PREDICTAI_TEST_DB=fake-turso the same file is reached through the Turso adapter and the libsql
    stand-in (tests/fake_libsql.py), so the whole suite can check that the app works on the adapter path."""
    import os

    if os.environ.get("PREDICTAI_TEST_DB") == "fake-turso":
        import fake_libsql
        from app.db import TursoDatabase

        url, token = f"libsql://test-{abs(hash(path))}.turso.io", "test-token-" + "x" * 32
        fake_libsql.register(url, path, token)
        return TursoDatabase(url, token, module=fake_libsql)
    return Database(path)


class Harness:
    """One isolated database per instance. Call close() (or use addCleanup) when done."""

    def __init__(self, **overrides):
        self._tmp = tempfile.TemporaryDirectory()
        path = f"{self._tmp.name}/test.db"
        base = load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_DATABASE_PATH": path, "PREDICTAI_EMAIL_OUTBOX_DIR": f"{self._tmp.name}/outbox", "PREDICTAI_STORAGE_DIR": f"{self._tmp.name}/storage"})
        self.settings = replace(base, **overrides)
        self.db = make_test_database(path)
        with self.db.session() as conn:
            migrate(conn)
        self.clock = ManualClock()
        self.outbox = MemoryOutbox()
        self.hasher = PasswordHasher(*self.settings.password_hash_params)
        self.conn = self.db.connect()

    def close(self):
        if getattr(self, "_runner", None) is not None:
            self._runner.shutdown()
        self.conn.close()
        if hasattr(self.db, "close_all"):
            self.db.close_all()
        self._tmp.cleanup()

    def services(self, ip: str = "203.0.113.7", device: str = "Test browser", conn=None, providers=None, http=None) -> Services:
        return build_services(settings=self.settings, conn=conn or self.conn, clock=self.clock, hasher=self.hasher, sender=self.outbox,
                              meta=RequestMeta(request_id="req-test", ip=ip, device_label=device), providers=providers, http=http)

    def workspace(self, principal_services=None):
        """Workspace services (real SQLite, real local storage in a temp dir) sharing the identity connection."""
        from app.modules.storage import LocalStorage
        from app.modules.workspace.container import build_workspace

        if not hasattr(self, "storage"):
            self.storage = LocalStorage(self.settings.storage_dir)
        services = principal_services or self.services()
        return build_workspace(services.ctx, self.storage, runner=self.runner())

    def runner(self, workers: int = 2):
        """One background-job runner per harness (training jobs run in worker threads with their own connections)."""
        from app.modules.predictive.jobs import JobRunner
        from app.modules.storage import LocalStorage

        if not hasattr(self, "storage"):
            self.storage = LocalStorage(self.settings.storage_dir)
        if getattr(self, "_runner", None) is None:
            self._runner = JobRunner(self.db, self.storage, self.settings, self.clock, workers=workers)
        return self._runner

    # ---- convenience flows (use the real services) ----
    def register(self, email="alice@example.com", password=GOOD_PASSWORD, name=None, ip="203.0.113.7"):
        return self.services(ip=ip).auth.register(email, password, name)

    def login(self, email="alice@example.com", password=GOOD_PASSWORD, remember=True, ip="203.0.113.7"):
        return self.services(ip=ip).auth.login(email, password, remember)

    def make_user(self, email="alice@example.com", password=GOOD_PASSWORD, name=None):
        """Registered, active (verification off) and signed in. Returns (principal, raw session token)."""
        self.register(email, password, name)
        result = self.login(email, password)
        return result.principal, result.session.token

    def make_owner(self, email="owner@example.com", password=GOOD_PASSWORD):
        """An owner provisioned the way the CLI does it, then signed in."""
        from app.modules.identity.provisioning import provision_owner

        provision_owner(self.services().ctx, email=email, password=password, allow_additional=True)
        result = self.login(email, password)
        return result.principal, result.session.token

    def principal(self, token: str, ip="203.0.113.7"):
        return self.services(ip=ip).auth.authenticate(token)

    def last_email(self, to: Optional[str] = None):
        for message in reversed(self.outbox.messages):
            if to is None or message.to.lower() == to.lower():
                return message
        return None

    def last_token(self, to: Optional[str] = None) -> str:
        message = self.last_email(to)
        assert message is not None, "no email was sent"
        match = re.search(r"token=([A-Za-z0-9_-]+)", message.body)
        assert match, f"no token in email: {message.subject}"
        return match.group(1)

    def rows(self, sql: str, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def dump(self) -> str:
        """Every stored value as one string, for 'this secret must not appear anywhere' checks."""
        parts = []
        for (name,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            for row in self.conn.execute(f"SELECT * FROM {name}").fetchall():
                parts.append(repr(tuple(row)))
        return "\n".join(parts)
