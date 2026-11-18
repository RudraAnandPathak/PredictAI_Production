"""Security and administration audit trail. Metadata only: secrets are redacted defensively."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from ...core.clock import Clock, to_iso
from .constants import Event
from .http_security import truncate_ip
from .repository import Repository

_SENSITIVE_KEY = re.compile(r"pass|token|secret|cookie|authoriz|hash|credential|api_?key|^code$|csrf", re.I)
_MAX_VALUE = 200
_MAX_METADATA = 1500


def clean_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    if not metadata:
        return None
    cleaned: Dict[str, Any] = {}
    for key, value in metadata.items():
        if _SENSITIVE_KEY.search(str(key)):
            cleaned[str(key)] = "[redacted]"
        elif isinstance(value, (int, float, bool)) or value is None:
            cleaned[str(key)] = value
        else:
            cleaned[str(key)] = str(value)[:_MAX_VALUE]
    text = json.dumps(cleaned, sort_keys=True, ensure_ascii=False)
    return text if len(text) <= _MAX_METADATA else text[:_MAX_METADATA]


class AuditLog:
    def __init__(self, repo: Repository, clock: Clock, meta: Any) -> None:
        self.repo = repo
        self.clock = clock
        self.meta = meta

    def record(self, event_type: str, outcome: str = "success", *, actor: Optional[str] = None, target: Optional[str] = None,
               reason: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.repo.insert_audit(
            occurred_at=self.clock.iso(), event_type=event_type, outcome=outcome, actor=actor, target=target,
            request_id=self.meta.request_id, source_ip=truncate_ip(self.meta.ip), reason=(reason or None) and reason[:200],
            metadata=clean_metadata(metadata),
        )

    def query(self, *, event_type: Optional[str] = None, outcome: Optional[str] = None, actor: Optional[str] = None,
              target: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
              before_id: Optional[int] = None, limit: int = 50) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 200))
        rows = self.repo.query_audit(event_type=event_type, outcome=outcome, actor=actor, target=target,
                                     since=since, until=until, before_id=before_id, limit=limit + 1)
        page = rows[:limit]
        items: List[Dict[str, Any]] = []
        for row in page:
            items.append({
                "id": row["id"], "occurred_at": row["occurred_at"], "event_type": row["event_type"], "outcome": row["outcome"],
                "actor_user_id": row["actor_user_id"], "actor_email": row["actor_email"],
                "target_user_id": row["target_user_id"], "target_email": row["target_email"],
                "request_id": row["request_id"], "source_ip": row["source_ip"], "reason": row["reason"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
            })
        return {"items": items, "next_before_id": page[-1]["id"] if len(rows) > limit and page else None}

    def purge(self, retention_days: int) -> int:
        """Delete audit records older than the retention period, then note that it happened."""
        cutoff = to_iso(self.clock.now() - timedelta(days=retention_days))
        removed = self.repo.purge_audit(cutoff)
        if removed:
            self.record(Event.AUDIT_PURGED, metadata={"removed": removed, "retention_days": retention_days})
        return removed
