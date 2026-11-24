"""Database-backed sliding-window rate limits, shared by every process using the database.

Keys are keyed hashes, so neither raw emails nor IP addresses are stored. Rejected attempts
are not recorded, so an attacker cannot extend a victim's lock-out by hammering."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Optional

from ...core.clock import Clock, from_iso, to_iso
from .errors import rate_limited
from .repository import Repository
from .tokens import keyed_hash


@dataclass(frozen=True)
class Limit:
    attempts: int
    window_seconds: int


POLICIES: Dict[str, Limit] = {
    "register:ip": Limit(10, 3600),
    "login:ip": Limit(30, 900),
    "magic_link:ip": Limit(10, 3600),
    "magic_link:email": Limit(3, 3600),
    "magic_link_use:ip": Limit(20, 3600),
    "recovery:ip": Limit(10, 3600),
    "recovery:email": Limit(3, 3600),
    "recovery_complete:ip": Limit(20, 3600),
    "verify_resend:ip": Limit(10, 3600),
    "verify_resend:email": Limit(3, 3600),
    "code_activation:ip": Limit(30, 3600),
    "oauth:user": Limit(20, 3600),
    "owner_api:user": Limit(300, 60),
    # Phase 3
    "projects:user": Limit(60, 3600),
    "upload_start:user": Limit(60, 3600),
    "workspace_write:user": Limit(600, 60),
    "oauth_login:ip": Limit(30, 900),
    # Phase 4
    "analytics:user": Limit(120, 60),
    "analytics_export:user": Limit(30, 3600),
    "assistant:user": Limit(60, 60),
    # Phase 5
    "ml:user": Limit(120, 60),
    "ml_train:user": Limit(20, 3600),
    "ml_predict:user": Limit(120, 60),
    "ml_export:user": Limit(30, 3600),
    "monitor:user": Limit(120, 60),
    # Phase 6
    "research:user": Limit(120, 60),
    "research_run:user": Limit(30, 3600),
    "research_web:user": Limit(200, 3600),
    "retrieval:user": Limit(120, 60),
    "retrieval_build:user": Limit(60, 3600),
    "news:user": Limit(120, 60),
    "news_ingest:user": Limit(30, 3600),
    "providers:user": Limit(60, 60),
    "research_export:user": Limit(30, 3600),
    # Phase 7: expensive operations are limited per account
    "p7_register:user": Limit(60, 3600),
    "p7_exam_analyse:user": Limit(40, 3600),
    "p7_exam_generate:user": Limit(40, 3600),
    "p7_exam_predict:user": Limit(30, 3600),
    "brain:user": Limit(60, 60),
    "nb_studio:user": Limit(30, 3600),
    "nb_deep:user": Limit(10, 3600),
    "nb_import:user": Limit(5, 3600),
    "review_write:user": Limit(60, 3600),
    "api_token_create:user": Limit(20, 3600),
    "api_token_auth:ip": Limit(30, 600),
    "p7_intel:user": Limit(60, 3600),
    "p7_report:user": Limit(60, 3600),
    "p7_export:user": Limit(60, 3600),
    "p7_share:user": Limit(120, 3600),
    "p7_invite:user": Limit(40, 3600),
    "p7_owner_change:user": Limit(60, 3600),
    "p7_schedule:user": Limit(60, 3600),
    # Phase 8: research workspaces
    "rw_write:user": Limit(300, 3600),
    # Phase 9: development workspace
    "dev_write:user": Limit(1200, 3600),
    "dev_submit:user": Limit(120, 3600),
    "dev_env_build:user": Limit(20, 3600),
    "dev_git:user": Limit(300, 3600),
    "dev_session:user": Limit(60, 3600),
    # Phase 10: research intelligence
    "ri_write:user": Limit(600, 3600),
    "ri_read:user": Limit(600, 60),
    "ri_import:user": Limit(120, 3600),
    "ri_ask:user": Limit(60, 3600),
    "ri_search:user": Limit(120, 60),
    "ri_job:user": Limit(300, 3600),
    "ri_discover:user": Limit(30, 60),
    "ri_analyse:user": Limit(20, 3600),
    "ri_report:user": Limit(120, 3600),
    "ri_export:user": Limit(60, 3600),
}


class RateLimiter:
    def __init__(self, repo: Repository, clock: Clock, secret: bytes) -> None:
        self.repo = repo
        self.clock = clock
        self.secret = secret

    def _key(self, bucket: str, value: str) -> str:
        return keyed_hash(self.secret, bucket, value)

    def _retry_after(self, oldest: Optional[str], window_seconds: int) -> int:
        if not oldest:
            return window_seconds
        remaining = (from_iso(oldest) + timedelta(seconds=window_seconds) - self.clock.now()).total_seconds()
        return max(1, int(remaining) + 1)

    def wait_time(self, bucket: str, value: str, limit: Limit) -> int:
        """Seconds until another attempt is allowed (0 = allowed now). Does not record anything."""
        since = to_iso(self.clock.now() - timedelta(seconds=limit.window_seconds))
        count, oldest = self.repo.rate_events(bucket, self._key(bucket, value), since)
        return self._retry_after(oldest, limit.window_seconds) if count >= limit.attempts else 0

    def record(self, bucket: str, value: str) -> None:
        self.repo.insert_rate_event(bucket, self._key(bucket, value), self.clock.iso())

    def hit(self, policy: str, value: str) -> None:
        """Count one attempt against a named policy; raise 429 if the limit is already used up."""
        limit = POLICIES[policy]
        wait = self.wait_time(policy, value, limit)
        if wait:
            raise rate_limited(wait)
        self.record(policy, value)

    def ensure_allowed(self, bucket: str, value: str, limit: Limit) -> None:
        wait = self.wait_time(bucket, value, limit)
        if wait:
            raise rate_limited(wait)

    def purge(self, older_than_seconds: int = 2 * 86400) -> int:
        return self.repo.purge_rate_events(to_iso(self.clock.now() - timedelta(seconds=older_than_seconds)))
