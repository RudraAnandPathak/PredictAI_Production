"""Owner bootstrap from the server's environment (Phase 8). Runs on the server only: at start-up and from `python -m app.cli bootstrap-owner`.

Rules:
  * does nothing unless PREDICTAI_OWNER_EMAIL and PREDICTAI_OWNER_PASSWORD are set (the plain OWNER_* names work too);
  * never creates a second owner: when any active owner exists the owner settings are ignored and nothing is changed (a changed OWNER_PASSWORD does NOT reset
    an existing owner's password; use `python -m app.cli recover-owner` for that);
  * the password is hashed like any other and is never logged or returned;
  * OWNER_ACTIVATION_CODE, if set, must have the same strength as a generated access code (PAI-XXXXX-XXXXX-XXXXX-XXXXX, 100 bits). It is stored only as a hash, as a single-use
    code that expires, labelled "owner bootstrap". A weak or malformed value is rejected rather than accepted;
  * there is no public route to any of this."""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Dict

from ...db import transaction
from .constants import Event, Tier
from .context import Context
from .errors import validation_failed
from .provisioning import provision_owner
from .tokens import CODE_GROUPS, CODE_GROUP_LEN, CODE_PREFIX, _CODE_ALPHABET, hash_access_code, new_id, normalize_access_code
from ...core.clock import to_iso

BOOTSTRAP_LABEL = "owner bootstrap"
BOOTSTRAP_CODE_DAYS = 30
_SHAPE = re.compile(rf"^{normalize_access_code(CODE_PREFIX)}[{_CODE_ALPHABET}]{{{CODE_GROUPS * CODE_GROUP_LEN}}}$")


def code_problem(raw: str) -> str | None:
    if not _SHAPE.match(normalize_access_code(raw)):
        return f"OWNER_ACTIVATION_CODE must look like {CODE_PREFIX}-XXXXX-XXXXX-XXXXX-XXXXX (generate one in Owner controls, or any code in that format); weaker codes are refused."
    return None


def bootstrap_owner_from_env(ctx: Context) -> Dict[str, Any]:
    s = ctx.settings
    out: Dict[str, Any] = {"owner": "skipped", "activation_code": "skipped"}
    if not (s.owner_bootstrap_email and s.owner_bootstrap_password):
        out["reason"] = "owner environment variables not set"
        return out
    if ctx.repo.active_owner_ids():
        out["reason"] = "an owner already exists; environment values ignored"
        return out
    if s.owner_bootstrap_code and (problem := code_problem(s.owner_bootstrap_code)):
        raise validation_failed([{"field": "OWNER_ACTIVATION_CODE", "issue": "weak", "message": problem}])
    result = provision_owner(ctx, email=s.owner_bootstrap_email, password=s.owner_bootstrap_password, display_name=s.owner_bootstrap_name or None)
    out["owner"] = "created" if result["created"] else "promoted"
    if s.owner_bootstrap_code:
        now = ctx.clock.now()
        with transaction(ctx.conn):
            if ctx.conn.execute("SELECT 1 FROM access_codes WHERE code_hash = ?", (hash_access_code(s.owner_bootstrap_code),)).fetchone() is None:
                code_id = new_id()
                ctx.repo.insert_code(id=code_id, code_hash=hash_access_code(s.owner_bootstrap_code), tier=Tier.SPECIAL, label=BOOTSTRAP_LABEL, created_by=result["user_id"],
                                     target_user_id=None, now=to_iso(now), expires_at=to_iso(now + timedelta(days=BOOTSTRAP_CODE_DAYS)))
                ctx.audit.record(Event.ACCESS_CODE_CREATED, actor=result["user_id"], metadata={"access_code_id": code_id, "source": "environment"})
                out["activation_code"] = "stored (hashed, single use)"
    return out
