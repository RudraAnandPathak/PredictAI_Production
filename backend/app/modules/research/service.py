"""The research use cases a person performs: settings and consent, sessions and plans, approval, runs, bundles, source inspection, notes, search, comparison, export, deletion.

Every method begins by loading its target through the owner's own principal (`_session`, `_bundle`), so a session, bundle, source, note or job that belongs to someone else, or to a
deleted project, is indistinguishable from one that does not exist. Nothing here talks to the network: web access happens only inside the job (engine.py), only for an approved plan."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ...core.errors import AppError
from ...db import transaction
from ..explanation import explain
from ..identity.authorization import Principal, build_principal, require
from ..identity.constants import Perm
from ..identity.repository import Repository as IdentityRepository
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from ..webaccess.cache import WebCache
from ..webaccess.fetcher import FetchError
from ..webaccess.providers import PROVIDERS
from ..workspace import keys
from ..workspace.errors import invalid, not_found
from . import bundles as B
from . import memory as M
from .collector import failure_text
from .consent import ConsentStore
from .context import ContextOrchestrator
from .engine import ResearchEngine
from .intents import asks_about_data, INTENT_PATTERNS
from ..providers import build_messages
from .safety import sanitize_query
from .sources import source_class, source_label
from .errors import Cancelled, ResearchFailed, busy, limit
from .events import REvent
from .jobs import Control, recover_stale, run_job
from .planner import LEVELS, build_plan, plan_digest
from .repository import ResearchRepository

MAX_QUESTION, MIN_QUESTION = 500, 5
MAX_NOTE, MAX_NOTES = 5000, 200
MAX_SESSIONS = 500
MAX_ACTIVE_JOBS = 3
SETTING_KEYS = {"web_enabled", "providers", "external_ai", "news_enabled", "freshness_days", "cache_days"}
NOTE_TARGETS = ("session", "finding", "source", "bundle")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(value: Any, field: str, lo: int, hi: int, *, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise invalid(field, "Enter some text.")
    v = _CTRL.sub("", value).strip()
    if not multiline:
        v = " ".join(v.split())
    if not lo <= len(v) <= hi:
        raise invalid(field, f"Use between {lo} and {hi} characters.")
    return v


def _id_list(value: Any, field: str, maxn: int = 20) -> Optional[List[str]]:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > maxn or not all(isinstance(v, str) and 0 < len(v) <= 64 for v in value):
        raise invalid(field, f"Provide a list of up to {maxn} ids.")
    return list(dict.fromkeys(value))


def _int(value: Any, field: str, lo: int, hi: int, *, nullable: bool = True) -> Optional[int]:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise invalid(field, f"Enter a whole number from {lo} to {hi}.")
    return value


class ResearchService:
    def __init__(self, *, ctx: Any, ws: Any, repo: ResearchRepository, storage: StorageBackend, consent: ConsentStore, retrieval: Any, engine: ResearchEngine, verifier: Any,
                 runner: Any = None, cache: Optional[WebCache] = None) -> None:
        self.ctx, self.ws, self.repo, self.storage, self.consent = ctx, ws, repo, storage, consent
        self.retrieval, self.engine, self.verifier, self.runner, self.cache = retrieval, engine, verifier, runner, cache
        self.handlers: Dict[str, Callable[[Any, Control], Optional[str]]] = {"research": self._job_research}

    # ================================================================== access
    def _need(self, principal: Principal) -> None:
        require(principal, Perm.DATA_MANAGE_OWN)

    def _project(self, principal: Principal, project_id: str, *, writable: bool = False):
        self._need(principal)
        return self.ws.projects.get_owned(principal, project_id, writable=writable)

    def _session(self, principal: Principal, session_id: Any, *, writable: bool = False):
        self._need(principal)
        row = self.repo.session(principal.user_id, session_id) if isinstance(session_id, str) and 0 < len(session_id) <= 64 else None
        if row is None:
            raise not_found("research")
        self.ws.projects.get_owned(principal, row["project_id"], writable=writable)
        return row

    def _bundle_row(self, principal: Principal, bundle_id: Any, *, writable: bool = False):
        self._need(principal)
        row = self.repo.bundle(principal.user_id, bundle_id) if isinstance(bundle_id, str) and 0 < len(bundle_id) <= 64 else None
        if row is None:
            raise not_found("evidence bundle")
        self.ws.projects.get_owned(principal, row["project_id"], writable=writable)
        return row

    def _load_bundle(self, row) -> Dict[str, Any]:
        try:
            data = json.loads(self.storage.read_bytes(row["bundle_key"], 30_000_000).decode("utf-8"))
        except (StorageError, OSError, ValueError):
            raise not_found("evidence bundle")
        return data

    # ================================================================== settings and consent
    def settings(self, principal: Principal, project_id: Optional[str] = None) -> Dict[str, Any]:
        self._need(principal)
        if project_id is not None:
            self._project(principal, project_id)
        owner = principal.user_id
        eff = self.consent.effective(owner, project_id) if project_id else None
        person = self.consent.get(owner, None)
        return {"person": person, "project": self.consent.get(owner, project_id) if project_id else None, "project_overrides": bool(project_id and self.consent._row(owner, project_id)),
                "effective": eff, "platform": {"web_enabled": bool(self.ctx.settings.research_web_enabled), "providers": [p.describe(self.ctx.settings) for p in PROVIDERS.values()]},
                "notes": ["Everything outbound is off until you switch it on. A project can only narrow what you allowed for yourself."]}

    def put_settings(self, principal: Principal, project_id: Optional[str], data: Any) -> Dict[str, Any]:
        self._need(principal)
        self.ctx.limiter.hit("research:user", principal.user_id)
        if not isinstance(data, dict) or not data or set(data) - SETTING_KEYS:
            raise invalid("body", "Send one or more of: " + ", ".join(sorted(SETTING_KEYS)) + ".")
        if project_id is not None:
            self._project(principal, project_id, writable=True)
        values: Dict[str, Any] = {}
        for k in ("web_enabled", "external_ai", "news_enabled"):
            if k in data:
                if not isinstance(data[k], bool):
                    raise invalid(k, "Use true or false.")
                values[k] = data[k]
        if "providers" in data:
            p = data["providers"]
            if not isinstance(p, list) or not all(isinstance(x, str) for x in p) or set(p) - set(PROVIDERS):
                raise invalid("providers", "Choose from: " + ", ".join(PROVIDERS) + ".")
            values["providers"] = list(dict.fromkeys(p))
        if "freshness_days" in data:
            values["freshness_days"] = _int(data["freshness_days"], "freshness_days", 1, 3650)
        if "cache_days" in data:
            values["cache_days"] = _int(data["cache_days"], "cache_days", 1, 90)
        owner = principal.user_id
        if project_id is not None and self.consent._row(owner, project_id) is None:
            values = {"web_enabled": True, "external_ai": True, "news_enabled": True, "providers": [], **values}        # a new project row starts by NOT narrowing anything
        with transaction(self.ctx.conn):
            self.consent.put(owner, project_id, values, new_id())
            self.ctx.audit.record(REvent.SETTINGS_CHANGED, actor=owner, target=owner, metadata={"project_id": project_id, "changed": sorted(values), "web_enabled": values.get("web_enabled")})
        return self.settings(principal, project_id)

    def capabilities(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id)
        eff = self.consent.effective(principal.user_id, project_id)
        return {"levels": [{"key": k, "label": v["label"], "about": v["about"], "subtopics": v["subtopics"], "web_pages": v["web_pages"], "min_sources": v["min_sources"]} for k, v in LEVELS.items()],
                "web": {"allowed": eff["web_allowed"], "providers": eff["providers"], "why_not": eff["reasons"]},
                "search_providers": [{**p.describe(self.ctx.settings), "allowed": p.key in eff["providers"]} for p in PROVIDERS.values()],
                "external_ai_allowed": eff["external_ai"], "freshness_days": eff["freshness_days"],
                "dataset_research": {"questions": sorted(INTENT_PATTERNS), "max_datasets": 3, "note": "Questions about a selected dataset are answered by calculation with the platform's own analysis and model services. "
                                     "No data values leave the platform and no number is written by a language model."},
                "follow_ups": True}

    # ================================================================== sessions and plans
    def _options(self, principal: Principal, project, data: Mapping[str, Any], eff: Mapping[str, Any]) -> Dict[str, Any]:
        for k in ("web", "ai_summary", "reuse_earlier", "ai_plan", "proceed_anyway"):
            if k in data and not isinstance(data[k], bool):
                raise invalid(k, "Use true or false.")
        web = bool(data.get("web", False))
        arxiv_mode = data.get("arxiv_mode", "auto")
        if arxiv_mode not in ("auto", "always", "never"):
            raise invalid("arxiv_mode", "Use auto, always or never.")
        providers = data.get("providers")
        if providers is None:
            providers = list(eff["providers"]) or ["wikipedia"]
        if not isinstance(providers, list) or not all(isinstance(p, str) for p in providers) or set(providers) - set(PROVIDERS):
            raise invalid("providers", "Choose from: " + ", ".join(PROVIDERS) + ".")
        docs, sets = _id_list(data.get("documents"), "documents"), _id_list(data.get("datasets"), "datasets")
        for ids, kind in ((docs, "document"), (sets, "dataset")):
            for aid in ids or []:
                row = self.ws.assets.get_owned(principal, aid)
                if row["project_id"] != project["id"] or row["kind"] != kind:
                    raise not_found("file")
        parent_id = data.get("parent_session_id")
        if parent_id is None and isinstance(data.get("parent"), dict):
            parent_id = data["parent"].get("session_id")                                      # a plan being rebuilt keeps its earlier question
        parent = None
        if parent_id is not None:
            prow = self._session(principal, parent_id)                                        # the earlier question must be this person's own, in this project
            if prow["project_id"] != project["id"]:
                raise not_found("research")
            parent = {"session_id": prow["id"], "question": prow["question"]}
            left_out = data.get("datasets_left_out")
            if isinstance(left_out, int) and not isinstance(left_out, bool) and 0 < left_out <= 50:
                parent["datasets_left_out"] = left_out
        return {"web": web, "providers": list(dict.fromkeys(providers)), "documents": docs, "datasets": sets, "freshness_days": _int(data.get("freshness_days"), "freshness_days", 1, 3650),
                "ai_summary": bool(data.get("ai_summary", False)), "reuse_earlier": bool(data.get("reuse_earlier", False)), "parent": parent,
                "arxiv_mode": arxiv_mode, "ai_plan": bool(data.get("ai_plan", False)), "proceed_anyway": bool(data.get("proceed_anyway", False))}

    def _interpret(self, principal: Principal, project, question: str, eff: Mapping[str, Any]) -> Dict[str, Any]:
        """Ask the configured AI provider which extra angles the question deserves. Only the question's own words are sent (never a document or data value), and a provider that is not local is
        used only if the person's settings allow external AI. Any failure leaves the deterministic plan as it is."""
        clean, removed = sanitize_query(" ".join(question.split()), max_len=300)
        if removed:
            return {"status": "unavailable", "note": "The question contains something that looks private, so it was not sent to any model."}
        try:
            router = self.engine.router_factory()
            msgs = build_messages('Reply with JSON only: {"task": one of overview|causes|consequences|comparison|statistics|methodology, "parts": [{"title": short heading, "keywords": [3 to 5 single words]}]}. '
                                  "Suggest at most 3 extra angles a careful researcher would cover. Do not answer the question.", [], clean)
            out = router.chat(principal.user_id, project["id"], "plan", msgs, data_class="public" if eff.get("external_ai") else "private", max_tokens=300)
        except Exception:                                                                    # noqa: BLE001  planning never fails because a model did
            return {"status": "unavailable", "note": "No language model could be reached; the rule-based plan was used."}
        if not out.ok or not out.text:
            return {"status": "unavailable", "provider": out.provider, "note": "No language model could be used for planning; the rule-based plan was used."}
        m = re.search(r"\{.*\}", out.text, re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
        except ValueError:
            data = {}
        parts = [p for p in (data.get("parts") or []) if isinstance(p, dict)] if isinstance(data, dict) else []
        if not parts:
            return {"status": "unavailable", "provider": out.provider, "note": "The model's answer could not be used; the rule-based plan was used."}
        return {"status": "ok", "provider": out.provider, "parts": parts, "note": "Extra parts were suggested by a language model and are shown in the plan for your approval."}

    def _make_plan(self, question: str, level: str, opts: Mapping[str, Any], *, principal: Optional[Principal] = None, project: Any = None, eff: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        interp = self._interpret(principal, project, question, eff or {}) if (opts.get("ai_plan") and principal is not None) else None
        return build_plan(question, level, web=bool(opts["web"]), providers=list(opts["providers"]), freshness_days=opts.get("freshness_days"), documents=opts.get("documents"), datasets=opts.get("datasets"),
                          parent=opts.get("parent"), arxiv_mode=opts.get("arxiv_mode", "auto"), interpretation=interp)

    @staticmethod
    def _need_clarity(plan: Mapping[str, Any], opts: Mapping[str, Any]) -> None:
        c = plan.get("clarification")
        if c and not opts.get("proceed_anyway"):
            raise AppError("NEEDS_CLARIFICATION", c.get("question") or "The question needs to be made more specific before research starts.", 409)

    def create_follow_up(self, principal: Principal, session_id: str, data: Any) -> Dict[str, Any]:
        """A new question that builds on an earlier one: it keeps that question's files and settings, starts from the pages and articles it already read, and gets its own plan to approve."""
        parent = self._session(principal, session_id, writable=True)
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        if not parent["latest_bundle_id"]:
            raise invalid("session", "Run the earlier question first; a follow-up builds on what it found.")
        old = json.loads(parent["options"] or "{}")
        carry = {k: old[k] for k in ("web", "providers", "documents", "datasets", "freshness_days", "ai_summary", "reuse_earlier") if old.get(k) is not None}
        left_out = 0
        if carry.get("datasets") and "datasets" not in (data.get("options") or {}) and not asks_about_data(str(data.get("question") or "")):
            left_out = len(carry.pop("datasets"))                                             # a question about an article already read does not recalculate the data; the plan says so
        body = {"question": data.get("question"), "level": data.get("level") or parent["level"], "purpose": parent["purpose"], "options": {**carry, **(data.get("options") or {}), "parent_session_id": parent["id"], **({"datasets_left_out": left_out} if left_out else {})}}
        if data.get("title"):
            body["title"] = data["title"]
        out = self.create_session(principal, parent["project_id"], body)
        self.ctx.audit.record(REvent.FOLLOW_UP, actor=principal.user_id, target=out["id"], metadata={"project_id": parent["project_id"], "parent": parent["id"]})
        return out

    def create_session(self, principal: Principal, project_id: str, data: Any) -> Dict[str, Any]:
        project = self._project(principal, project_id, writable=True)
        self.ctx.limiter.hit("research:user", principal.user_id)
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        question = _text(data.get("question"), "question", MIN_QUESTION, MAX_QUESTION)
        level = data.get("level", "topic")
        if level not in LEVELS:
            raise invalid("level", "Choose one of: " + ", ".join(LEVELS) + ".")
        purpose = data.get("purpose", "general")
        if purpose not in ("general", "education"):
            raise invalid("purpose", "Use 'general' or 'education'.")
        title = _text(data["title"], "title", 1, 120) if data.get("title") else question[:80]
        if self.repo.count_sessions(principal.user_id, project["id"]) >= MAX_SESSIONS:
            raise limit(f"A project can hold {MAX_SESSIONS} research sessions. Delete some you no longer need.")
        eff = self.consent.effective(principal.user_id, project["id"])
        opts = self._options(principal, project, data.get("options") or {}, eff)
        plan = self._make_plan(question, level, opts, principal=principal, project=project, eff=eff)
        sid, now = new_id(), self.ctx.clock.iso()
        with transaction(self.ctx.conn):
            self.repo.create_session(id=sid, project_id=project["id"], owner_user_id=principal.user_id, title=title, question=question, level=level, purpose=purpose, status="draft",
                                     options=json.dumps(opts), plan=json.dumps(plan), plan_hash=plan["plan_hash"], search_text=M.build_search_text(question, title, [], []), created_at=now, updated_at=now)
            self.ctx.audit.record(REvent.SESSION_CREATED, actor=principal.user_id, target=sid, metadata={"project_id": project["id"], "level": level, "web": opts["web"]})
        return self.get_session(principal, sid)

    def update_session(self, principal: Principal, session_id: str, data: Any) -> Dict[str, Any]:
        row = self._session(principal, session_id, writable=True)
        self.ctx.limiter.hit("research:user", principal.user_id)
        if not isinstance(data, dict) or not data or set(data) - {"title", "question", "level", "options", "saved", "purpose"}:
            raise invalid("body", "Send one or more of: title, question, level, options, saved, purpose.")
        if row["status"] in ("queued", "running"):
            raise busy("This research is running. Cancel it first, or wait for it to finish.")
        fields: Dict[str, Any] = {}
        if "title" in data:
            fields["title"] = _text(data["title"], "title", 1, 120)
        if "saved" in data:
            if not isinstance(data["saved"], bool):
                raise invalid("saved", "Use true or false.")
            fields["saved"] = int(data["saved"])
        if "purpose" in data:
            if data["purpose"] not in ("general", "education"):
                raise invalid("purpose", "Use 'general' or 'education'.")
            fields["purpose"] = data["purpose"]
        replan = any(k in data for k in ("question", "level", "options"))
        if replan:
            question = _text(data["question"], "question", MIN_QUESTION, MAX_QUESTION) if "question" in data else row["question"]
            level = data.get("level", row["level"])
            if level not in LEVELS:
                raise invalid("level", "Choose one of: " + ", ".join(LEVELS) + ".")
            project = self.ws.projects.get_owned(principal, row["project_id"], writable=True)
            eff = self.consent.effective(principal.user_id, row["project_id"])
            old = json.loads(row["options"])
            opts = self._options(principal, project, {**old, **(data.get("options") or {})}, eff) if "options" in data else old
            plan = self._make_plan(question, level, opts, principal=principal, project=project, eff=eff)
            fields.update(question=question, level=level, options=json.dumps(opts), plan=json.dumps(plan), plan_hash=plan["plan_hash"], approved_plan_hash=None, status="draft")
        fields["updated_at"] = self.ctx.clock.iso()
        with transaction(self.ctx.conn):
            self.repo.update_session(row["id"], **fields)
            self.ctx.audit.record(REvent.SESSION_UPDATED, actor=principal.user_id, target=row["id"], metadata={"project_id": row["project_id"], "changed": sorted(fields), "replanned": replan})
        return self.get_session(principal, session_id)

    def _present_session(self, principal: Principal, row, *, with_plan: bool = True) -> Dict[str, Any]:
        plan = json.loads(row["plan"] or "null")
        job = self.repo.active_job_for_session(principal.user_id, row["id"])
        eff = self.consent.effective(principal.user_id, row["project_id"])
        web_queries = (plan or {}).get("web", {}).get("queries", []) if plan else []
        out = {"id": row["id"], "project_id": row["project_id"], "title": row["title"], "question": row["question"], "level": row["level"], "level_label": LEVELS[row["level"]]["label"],
               "purpose": row["purpose"], "status": row["status"], "options": json.loads(row["options"] or "{}"), "plan_hash": row["plan_hash"],
               "plan_approved": bool(row["approved_plan_hash"]) and row["approved_plan_hash"] == row["plan_hash"], "needs_approval": bool(web_queries),
               "latest_bundle_id": row["latest_bundle_id"], "run_count": row["run_count"], "saved": bool(row["saved"]), "notes": self.repo.count_notes(principal.user_id, row["id"]),
               "created_at": row["created_at"], "updated_at": row["updated_at"], "last_run_at": row["last_run_at"], "active_job": self._present_job(job) if job else None,
               "parent": (plan or {}).get("follow_up") or None,
               "web": {"allowed_now": eff["web_allowed"], "why_not": eff["reasons"],
                       "will_send": [{"query": q["query"], "providers": q["providers"], "removed": q["removed"],
                                      "variants": [{"provider": p, "query": v["query"], "role": v["role"]} for p, vs in (q.get("by_provider") or {}).items() for v in vs]} for q in web_queries],
                       "note": "Only these search phrases, taken from your question, would leave the platform. No document text is ever sent."}}
        if with_plan:
            out["plan"] = plan
        return out

    def get_session(self, principal: Principal, session_id: str) -> Dict[str, Any]:
        row = self._session(principal, session_id)
        out = self._present_session(principal, row)
        out["explanation"] = explain("research:plan", {"plan": out["plan"], "web": out["web"], "approved": out["plan_approved"]})
        return out

    def list_sessions(self, principal: Principal, project_id: str, *, status: Optional[str] = None, saved: Optional[bool] = None, limit_: int = 50, offset: int = 0) -> Dict[str, Any]:
        project = self._project(principal, project_id)
        if status not in (None, "draft", "queued", "running", "completed", "failed", "cancelled"):
            raise invalid("status", "Unknown status.")
        rows = self.repo.sessions(principal.user_id, project["id"], status=status, saved=saved, limit=max(1, min(int(limit_), 200)), offset=max(0, int(offset)))
        return {"items": [self._present_session(principal, r, with_plan=False) for r in rows], "total": self.repo.count_sessions(principal.user_id, project["id"])}

    def approve_plan(self, principal: Principal, session_id: str, plan_hash: Any) -> Dict[str, Any]:
        row = self._session(principal, session_id, writable=True)
        self.ctx.limiter.hit("research:user", principal.user_id)
        if row["status"] in ("queued", "running"):
            raise busy("This research is already running.")
        if not isinstance(plan_hash, str) or plan_hash != row["plan_hash"]:
            raise AppError("PLAN_CHANGED", "The plan changed after you read it. Review the new plan and approve it again.", 409)
        plan = json.loads(row["plan"])
        self._need_clarity(plan, json.loads(row["options"] or "{}"))
        if plan["web"]["queries"]:
            eff = self.consent.effective(principal.user_id, row["project_id"])
            if not eff["web_allowed"]:
                self.ctx.audit.record(REvent.WEB_DENIED, "denied", actor=principal.user_id, target=row["id"], reason="approve_without_consent", metadata={"project_id": row["project_id"]})
                raise AppError("WEB_NOT_ALLOWED", " ".join(eff["reasons"]) or "Web research is not allowed.", 403)
        with transaction(self.ctx.conn):
            self.repo.update_session(row["id"], approved_plan_hash=row["plan_hash"], updated_at=self.ctx.clock.iso())
            self.ctx.audit.record(REvent.PLAN_APPROVED, actor=principal.user_id, target=row["id"], metadata={"project_id": row["project_id"], "queries": len(plan["web"]["queries"])})
        return self.get_session(principal, session_id)

    # ================================================================== runs
    def run(self, principal: Principal, session_id: str, approve_plan_hash: Any = None) -> Dict[str, Any]:
        row = self._session(principal, session_id, writable=True)
        self.ctx.limiter.hit("research_run:user", principal.user_id)
        if self.runner is None:
            raise AppError("RESEARCH_UNAVAILABLE", "Background research is not available on this server.", 503)
        if self.repo.active_job_for_session(principal.user_id, row["id"]) is not None:
            raise busy("This research is already running.")
        if self.repo.active_jobs(principal.user_id) >= MAX_ACTIVE_JOBS:
            raise limit(f"You can have {MAX_ACTIVE_JOBS} research jobs at once. Wait for one to finish or cancel one.")
        plan = json.loads(row["plan"])
        self._need_clarity(plan, json.loads(row["options"] or "{}"))
        if plan["web"]["queries"]:
            if approve_plan_hash is not None:
                self.approve_plan(principal, session_id, approve_plan_hash)
                row = self._session(principal, session_id, writable=True)
            if row["approved_plan_hash"] != row["plan_hash"]:
                raise AppError("PLAN_NOT_APPROVED", "This plan sends web searches. Read the searches and approve the plan first, or turn web research off for this session.", 409)
            eff = self.consent.effective(principal.user_id, row["project_id"])
            if not eff["web_allowed"]:
                self.ctx.audit.record(REvent.WEB_DENIED, "denied", actor=principal.user_id, target=row["id"], reason="run_without_consent", metadata={"project_id": row["project_id"]})
                raise AppError("WEB_NOT_ALLOWED", " ".join(eff["reasons"]) or "Web research is not allowed.", 403)
        job_id, now = new_id(), self.ctx.clock.iso()
        with transaction(self.ctx.conn):
            self.repo.create_job(id=job_id, kind="research", owner_user_id=principal.user_id, project_id=row["project_id"], session_id=row["id"], status="queued", params=json.dumps({"plan_hash": row["plan_hash"]}),
                                 extended=int(principal.can(Perm.COMPUTE_EXTENDED)), progress=json.dumps({"phase": "queued", "fraction": 0, "message": "Waiting to start"}), created_at=now)
            self.repo.update_session(row["id"], status="queued", updated_at=now)
            self.ctx.audit.record(REvent.RUN_REQUESTED, actor=principal.user_id, target=job_id, metadata={"project_id": row["project_id"], "session_id": row["id"], "web": bool(plan["web"]["queries"])})
        self.runner.submit(job_id)
        return self.job(principal, job_id)

    def _present_job(self, j) -> Dict[str, Any]:
        return {"id": j["id"], "kind": j["kind"], "status": j["status"], "session_id": j["session_id"], "subject_id": j["subject_id"], "progress": json.loads(j["progress"] or "null"),
                "error_code": j["error_code"], "error_message": j["error_message"], "cancel_requested": bool(j["cancel_requested"]), "result_id": j["result_id"], "created_at": j["created_at"],
                "started_at": j["started_at"], "finished_at": j["finished_at"], "duration_ms": j["duration_ms"]}

    def job(self, principal: Principal, job_id: str) -> Dict[str, Any]:
        self._need(principal)
        j = self.repo.job(principal.user_id, job_id) if isinstance(job_id, str) and 0 < len(job_id) <= 64 else None
        if j is None:
            raise not_found("job")
        self.ws.projects.get_owned(principal, j["project_id"])
        if self.runner is not None:
            self.runner.recover_stale()
            j = self.repo.job(principal.user_id, job_id)
        return self._present_job(j)

    def jobs(self, principal: Principal, project_id: str, *, session_id: Optional[str] = None, kind: Optional[str] = None) -> Dict[str, Any]:
        self._project(principal, project_id)
        if kind not in (None, "research", "news_ingest", "influence"):
            raise invalid("kind", "Unknown job kind.")
        if self.runner is not None:
            self.runner.recover_stale()
        return {"items": [self._present_job(j) for j in self.repo.jobs(principal.user_id, project_id, session_id=session_id, kind=kind)]}

    def cancel(self, principal: Principal, job_id: str) -> Dict[str, Any]:
        self._need(principal)
        j = self.repo.job(principal.user_id, job_id) if isinstance(job_id, str) and 0 < len(job_id) <= 64 else None
        if j is None:
            raise not_found("job")
        self.ws.projects.get_owned(principal, j["project_id"])
        if j["status"] not in ("queued", "running"):
            raise AppError("JOB_FINISHED", "This job has already finished.", 409)
        self.repo.request_cancel(principal.user_id, job_id)
        return self.job(principal, job_id)

    # ---- the job body (runs in a worker thread with its own connection)
    def _job_research(self, job, ctl: Control) -> Optional[str]:
        owner = job["owner_user_id"]
        user = IdentityRepository(self.ctx.conn).user_by_id(owner)
        if user is None or user["status"] != "active":
            raise Cancelled()
        principal = build_principal(IdentityRepository(self.ctx.conn), user)
        if not principal.can(Perm.DATA_MANAGE_OWN):
            raise Cancelled()
        session = self.repo.session(owner, job["session_id"])
        if session is None:
            raise Cancelled()
        try:
            self.ws.projects.get_owned(principal, session["project_id"], writable=True)
        except AppError:
            raise Cancelled()
        self.repo.update_session(session["id"], status="running", updated_at=self.ctx.clock.iso())
        try:
            result = self.engine.run(principal, session, job["id"], ctl.cancelled, ctl.progress)
            if ctl.cancelled():
                raise Cancelled()
            return self._complete(principal, session, job, result)
        except Cancelled:
            self.repo.update_session(session["id"], status="cancelled", updated_at=self.ctx.clock.iso())
            raise
        except BaseException:
            self.repo.update_session(session["id"], status="failed", updated_at=self.ctx.clock.iso())
            raise

    def _complete(self, principal: Principal, session, job, result: Dict[str, Any]) -> str:
        bundle = result["bundle"]
        pid, sid, now = session["project_id"], session["id"], self.ctx.clock.iso()
        self.engine.persist(bundle)
        bkey = keys.bundle_key(pid, sid, bundle["id"])
        notes = [n["body"] for n in self.repo.notes(principal.user_id, sid)]
        try:
            with transaction(self.ctx.conn):
                for s in bundle["sources"]:
                    fr = s.get("freshness") or {}
                    self.repo.upsert_source(id=new_id(), project_id=pid, owner_user_id=principal.user_id, session_id=sid, bundle_id=bundle["id"], source_key=hashlib.sha256(s["key"].encode()).hexdigest()[:40],
                                            kind=s["kind"], origin_class=s["origin"], title=(s.get("title") or "Source")[:300], url=s.get("url"), domain=s.get("domain"), publisher=s.get("publisher"),
                                            ref_asset_id=s.get("asset_id"), ref_id=s.get("ref_id"), content_hash=s.get("content_hash"), text_key=s.get("text_key"), retrieved_at=s.get("retrieved_at"),
                                            published_at=fr.get("published"), freshness=json.dumps(fr), reliability=json.dumps(s.get("reliability")), status=s.get("status", "ok"),
                                            status_code=s.get("status_code"), provider=s.get("provider"), updated_at=now)
                self.repo.create_bundle(id=bundle["id"], project_id=pid, owner_user_id=principal.user_id, session_id=sid, job_id=job["id"], number=bundle["number"], question=bundle["question"], level=bundle["level"],
                                        status=bundle["status"], bundle_key=bkey, bundle_sha256=bundle["integrity"]["sha256"], asset_ids=json.dumps(bundle["asset_ids"]), source_count=len(bundle["sources"]),
                                        evidence_count=len(bundle["evidence"]), finding_count=len(bundle["findings"]), conflict_count=len(bundle["conflicts"]), quality_verdict=bundle["quality"]["verdict"],
                                        used_web=int(bundle["used_web"]), created_at=now)
                self.repo.update_session(sid, status="completed", latest_bundle_id=bundle["id"], run_count=session["run_count"] + 1, last_run_at=now, updated_at=now,
                                         search_text=M.build_search_text(session["question"], session["title"], [c["text"] for c in bundle["conclusions"]], notes))
        except BaseException:
            try:
                self.storage.delete(bkey)
            except (StorageError, OSError):
                pass
            raise
        return bundle["id"]

    def run_job(self, job_id: str) -> None:
        run_job(self, job_id, self.handlers)

    # ================================================================== bundles
    def list_bundles(self, principal: Principal, project_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        self._project(principal, project_id)
        rows = self.repo.bundles_of_session(principal.user_id, session_id) if session_id else self.repo.bundles(principal.user_id, project_id)
        return {"items": [B.listing(r) for r in rows if r["project_id"] == project_id]}

    def get_bundle(self, principal: Principal, bundle_id: str) -> Dict[str, Any]:
        row = self._bundle_row(principal, bundle_id)
        data = self._load_bundle(row)
        ok = B.verify_integrity(data) and data["integrity"]["sha256"] == row["bundle_sha256"]
        view = {**data, "sources": [B.public_source(s) for s in data["sources"]]}
        out = {"bundle": view, "integrity_ok": ok, "label": row["label"], "saved": bool(row["saved"]), "warnings": list(data["quality"]["warnings"])}
        if not ok:
            out["warnings"].insert(0, "This saved bundle no longer matches its fingerprint. It may have been altered or damaged; do not rely on it.")
        out["explanation"] = explain("research:bundle", {"bundle": data, "integrity_ok": ok})
        return out

    def summary(self, principal: Principal, bundle_id: str) -> Dict[str, Any]:
        """What other modules may reuse: labelled conclusions with their citations."""
        row = self._bundle_row(principal, bundle_id)
        data = self._load_bundle(row)
        if not B.verify_integrity(data):
            raise AppError("BUNDLE_DAMAGED", "This evidence bundle failed its integrity check, so it cannot be reused.", 409)
        return B.reusable(data)

    def set_bundle(self, principal: Principal, bundle_id: str, data: Any) -> Dict[str, Any]:
        row = self._bundle_row(principal, bundle_id, writable=True)
        if not isinstance(data, dict) or not data or set(data) - {"saved", "label"}:
            raise invalid("body", "Send saved and/or label.")
        fields: Dict[str, Any] = {}
        if "saved" in data:
            if not isinstance(data["saved"], bool):
                raise invalid("saved", "Use true or false.")
            fields["saved"] = int(data["saved"])
        if "label" in data:
            fields["label"] = None if data["label"] in (None, "") else _text(data["label"], "label", 1, 80)
        self.repo.set_bundle(principal.user_id, row["id"], **fields)
        return B.listing(self.repo.bundle(principal.user_id, row["id"]))

    def delete_bundle(self, principal: Principal, bundle_id: str) -> Dict[str, Any]:
        row = self._bundle_row(principal, bundle_id, writable=True)
        try:
            self.storage.delete(row["bundle_key"])
        except (StorageError, OSError):
            raise AppError("STORAGE_ERROR", "The bundle could not be deleted right now. Please try again.", 503)
        with transaction(self.ctx.conn):
            self.repo.delete_bundle(row["id"])
            latest = self.repo.bundles_of_session(principal.user_id, row["session_id"])
            sess = self.repo.session(principal.user_id, row["session_id"])
            if sess is not None:
                self.repo.update_session(sess["id"], latest_bundle_id=latest[0]["id"] if latest else None, updated_at=self.ctx.clock.iso())
            self.ctx.audit.record(REvent.BUNDLE_DELETED, actor=principal.user_id, target=row["id"], metadata={"project_id": row["project_id"]})
        return {"deleted": True}

    def export_bundle(self, principal: Principal, bundle_id: str, fmt: Any) -> Dict[str, Any]:
        row = self._bundle_row(principal, bundle_id)
        self.ctx.limiter.hit("research_export:user", principal.user_id)
        if fmt not in ("json", "markdown", "csv"):
            raise invalid("format", "Use json, markdown or csv.")
        data = self._load_bundle(row)
        if not B.verify_integrity(data):
            raise AppError("BUNDLE_DAMAGED", "This evidence bundle failed its integrity check, so it cannot be exported.", 409)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", row["question"])[:40].strip("-") or "research"
        if fmt == "json":
            body, ctype, ext = json.dumps({**data, "sources": [B.public_source(s) for s in data["sources"]],
                                           "export_note": "Internal storage paths were left out of this copy, so its fingerprint matches the copy stored in PredictAI, not this file. bundle_sha256 below identifies that stored copy."},
                                          ensure_ascii=False, indent=2), "application/json", "json"
        elif fmt == "markdown":
            body, ctype, ext = B.to_markdown(data, explain("research:bundle", {"bundle": data, "integrity_ok": True})), "text/markdown; charset=utf-8", "md"
        else:
            body, ctype, ext = B.evidence_csv(data), "text/csv; charset=utf-8", "csv"
        self.ctx.audit.record(REvent.EXPORT, actor=principal.user_id, target=row["id"], metadata={"project_id": row["project_id"], "format": fmt})
        return {"body": body, "content_type": ctype, "filename": f"research-{stem}-run{row['number']}.{ext}"}

    # ================================================================== inspecting what stands behind a claim
    def inspect_citation(self, principal: Principal, bundle_id: str, evidence_id: Any) -> Dict[str, Any]:
        row = self._bundle_row(principal, bundle_id)
        self.ctx.limiter.hit("research:user", principal.user_id)
        data = self._load_bundle(row)
        e = next((x for x in data["evidence"] if x["id"] == evidence_id), None)
        if e is None:
            raise not_found("passage")
        src = next(s for s in data["sources"] if s["id"] == e["source"])
        res = self.verifier.verify(principal, e, src)
        ctx_window: Optional[Dict[str, str]] = None
        s_, e_ = e["citation"].get("start"), e["citation"].get("end")
        if res.get("verified") and isinstance(s_, int) and isinstance(e_, int):
            if src["kind"] in ("web", "news"):
                snap = self.verifier.snapshot(src.get("text_key"))
                if snap:
                    t = snap["text"]
                    ctx_window = {"before": t[max(0, s_ - 300):s_], "quote": t[s_:e_], "after": t[e_:e_ + 300]}
            elif src["kind"] == "document":
                try:
                    _, _, _, doc = self.retrieval.views.document_json(principal, e["citation"]["asset_id"], e["citation"]["version_id"])
                    page = next(p for p in doc["pages"] if p["number"] == e["citation"]["page_start"])["text"]
                    ctx_window = {"before": page[max(0, s_ - 300):s_], "quote": page[s_:e_], "after": page[e_:e_ + 300]}
                except (AppError, StopIteration):
                    ctx_window = None
        return {"evidence": e, "source": B.public_source(src), "verification": res, "context": ctx_window, "open": self._open_target(e, src), "source_label": source_label(src),
                "calculation": self._calculation_view(e),
                "note": "The text above was read again from the saved source just now. If a check fails, the passage is shown with a warning and should not be relied on."}

    @staticmethod
    def _open_target(e: Mapping[str, Any], src: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Where the original can be opened from a citation: the live page or the exact section of an article or paper, or the platform's own document, calculation or model."""
        cit, loc = e.get("citation") or {}, e.get("location") or {}
        if src.get("kind") in ("web", "news"):
            url = loc.get("deep_url") or src.get("final_url") or src.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                return None
            return {"type": "external", "url": url, "label": "Open the original section" if loc.get("deep_url") else "Open the original page", "section": loc.get("section"),
                    "note": f"This opens the live address. It may have changed since PredictAI read it{(' on ' + str(src['retrieved_at'])) if src.get('retrieved_at') else ''}; the quoted text above is the saved copy."}
        if cit.get("kind") == "calculation":
            return {"type": "analysis_run", "asset_id": cit.get("asset_id"), "run_id": cit.get("run_id"), "label": "Open the calculation"}
        if cit.get("kind") == "model":
            return {"type": "model", "asset_id": cit.get("asset_id"), "model_id": cit.get("model_id"), "label": "Open the model"}
        if src.get("kind") == "document" and cit.get("asset_id"):
            return {"type": "document", "asset_id": cit.get("asset_id"), "page": loc.get("page_start"), "label": "Open the document"}
        return None

    @staticmethod
    def _calculation_view(e: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        cit = e.get("citation") or {}
        if cit.get("kind") not in ("calculation", "model") or not cit.get("calc"):
            return None
        calc = cit["calc"]
        return {"how": "A fixed template wrote this sentence from the stored result below; a language model wrote nothing. The sentence is rebuilt from the stored result each time you open this citation.",
                "operation": cit.get("op") or calc.get("fn"), "run_id": cit.get("run_id"), "model_id": cit.get("model_id"), "result_hash": cit.get("result_hash"), "facts": e.get("facts") or {}}

    def list_sources(self, principal: Principal, project_id: str, *, kind: Optional[str] = None, status: Optional[str] = None, saved: Optional[bool] = None, limit_: int = 100, offset: int = 0) -> Dict[str, Any]:
        self._project(principal, project_id)
        if kind not in (None, "document", "dataset", "analysis", "model", "web", "news", "evidence") or status not in (None, "ok", "inaccessible", "blocked", "stale"):
            raise invalid("filter", "Unknown kind or status.")
        rows = self.repo.sources(principal.user_id, project_id, kind=kind, status=status, saved=saved, limit=max(1, min(int(limit_), 200)), offset=max(0, int(offset)))
        return {"items": [self._present_source(r) for r in rows]}

    def _present_source(self, r) -> Dict[str, Any]:
        return {"id": r["id"], "session_id": r["session_id"], "bundle_id": r["bundle_id"], "kind": r["kind"], "origin": r["origin_class"], "title": r["title"], "url": r["url"], "domain": r["domain"],
                "publisher": r["publisher"], "asset_id": r["ref_asset_id"], "retrieved_at": r["retrieved_at"], "published_at": r["published_at"], "status": r["status"], "status_code": r["status_code"],
                "provider": r["provider"], "saved": bool(r["saved"]), "freshness": json.loads(r["freshness"] or "null"), "reliability": json.loads(r["reliability"] or "null"), "updated_at": r["updated_at"]}

    def get_source(self, principal: Principal, source_id: str) -> Dict[str, Any]:
        self._need(principal)
        r = self.repo.source(principal.user_id, source_id) if isinstance(source_id, str) and 0 < len(source_id) <= 64 else None
        if r is None:
            raise not_found("source")
        self.ws.projects.get_owned(principal, r["project_id"])
        out = self._present_source(r)
        cited = []
        if r["bundle_id"]:
            brow = self.repo.bundle(principal.user_id, r["bundle_id"])
            if brow is not None:
                data = self._load_bundle(brow)
                key = next((s["id"] for s in data["sources"] if hashlib.sha256(s["key"].encode()).hexdigest()[:40] == r["source_key"]), None)
                cited = [{"evidence": e["id"], "quote": e["quote"], "kind": e["kind"], "location": e["location"]} for e in data["evidence"] if e["source"] == key]
        out["cited_passages"] = cited
        return out

    def save_source(self, principal: Principal, source_id: str, saved: Any) -> Dict[str, Any]:
        if not isinstance(saved, bool):
            raise invalid("saved", "Use true or false.")
        self.get_source(principal, source_id)
        self.repo.set_source_saved(principal.user_id, source_id, saved)
        return self.get_source(principal, source_id)

    # ================================================================== notes (the person's own words; never generated)
    def _refresh_search_text(self, principal: Principal, session) -> None:
        conclusions: List[str] = []
        if session["latest_bundle_id"]:
            brow = self.repo.bundle(principal.user_id, session["latest_bundle_id"])
            if brow is not None:
                try:
                    conclusions = [c["text"] for c in self._load_bundle(brow)["conclusions"]]
                except AppError:
                    conclusions = []
        notes = [n["body"] for n in self.repo.notes(principal.user_id, session["id"])]
        self.repo.update_session(session["id"], search_text=M.build_search_text(session["question"], session["title"], conclusions, notes), updated_at=self.ctx.clock.iso())

    def _present_note(self, n) -> Dict[str, Any]:
        return {"id": n["id"], "session_id": n["session_id"], "target_type": n["target_type"], "target_id": n["target_id"], "body": n["body"], "created_at": n["created_at"], "updated_at": n["updated_at"],
                "origin": "written_by_you"}

    def list_notes(self, principal: Principal, session_id: str) -> Dict[str, Any]:
        row = self._session(principal, session_id)
        return {"items": [self._present_note(n) for n in self.repo.notes(principal.user_id, row["id"])]}

    def add_note(self, principal: Principal, session_id: str, data: Any) -> Dict[str, Any]:
        row = self._session(principal, session_id, writable=True)
        self.ctx.limiter.hit("research:user", principal.user_id)
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        ttype = data.get("target_type", "session")
        if ttype not in NOTE_TARGETS:
            raise invalid("target_type", "Use session, finding, source or bundle.")
        tid = data.get("target_id")
        if ttype != "session" and not (isinstance(tid, str) and 0 < len(tid) <= 64):
            raise invalid("target_id", "Say which item the note is about.")
        body = _text(data.get("body"), "body", 1, MAX_NOTE, multiline=True)
        if self.repo.count_notes(principal.user_id, row["id"]) >= MAX_NOTES:
            raise limit(f"A research session can hold {MAX_NOTES} notes.")
        nid, now = new_id(), self.ctx.clock.iso()
        with transaction(self.ctx.conn):
            self.repo.create_note(id=nid, project_id=row["project_id"], owner_user_id=principal.user_id, session_id=row["id"], target_type=ttype, target_id=tid if ttype != "session" else None, body=body, created_at=now, updated_at=now)
            self._refresh_search_text(principal, self.repo.session(principal.user_id, row["id"]))
            self.ctx.audit.record(REvent.NOTE_SAVED, actor=principal.user_id, target=nid, metadata={"project_id": row["project_id"], "session_id": row["id"]})
        return self._present_note(self.repo.note(principal.user_id, nid))

    def update_note(self, principal: Principal, note_id: str, data: Any) -> Dict[str, Any]:
        n = self.repo.note(principal.user_id, note_id) if isinstance(note_id, str) and 0 < len(note_id) <= 64 else None
        if n is None:
            raise not_found("note")
        row = self._session(principal, n["session_id"], writable=True)
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        body = _text(data.get("body"), "body", 1, MAX_NOTE, multiline=True)
        with transaction(self.ctx.conn):
            self.repo.update_note(principal.user_id, note_id, body, self.ctx.clock.iso())
            self._refresh_search_text(principal, row)
        return self._present_note(self.repo.note(principal.user_id, note_id))

    def delete_note(self, principal: Principal, note_id: str) -> Dict[str, Any]:
        n = self.repo.note(principal.user_id, note_id) if isinstance(note_id, str) and 0 < len(note_id) <= 64 else None
        if n is None:
            raise not_found("note")
        row = self._session(principal, n["session_id"], writable=True)
        with transaction(self.ctx.conn):
            self.repo.delete_note(principal.user_id, note_id)
            self._refresh_search_text(principal, self.repo.session(principal.user_id, row["id"]))
        return {"deleted": True}

    # ================================================================== memory: search, compare, delete
    def search(self, principal: Principal, project_id: str, query: Any) -> Dict[str, Any]:
        self._project(principal, project_id)
        self.ctx.limiter.hit("research:user", principal.user_id)
        q = _text(query, "q", 2, 200)
        return {"query": q, "items": M.search(self.repo.search_sessions(principal.user_id, project_id), q)}

    def compare(self, principal: Principal, bundle_a: str, bundle_b: str) -> Dict[str, Any]:
        ra, rb = self._bundle_row(principal, bundle_a), self._bundle_row(principal, bundle_b)
        self.ctx.limiter.hit("research:user", principal.user_id)
        if ra["id"] == rb["id"]:
            raise invalid("bundles", "Choose two different runs to compare.")
        if ra["project_id"] != rb["project_id"]:
            raise not_found("evidence bundle")
        da, db_ = self._load_bundle(ra), self._load_bundle(rb)
        if (ra["created_at"], ra["number"], ra["id"]) > (rb["created_at"], rb["number"], rb["id"]):         # run number settles ties inside one session
            da, db_ = db_, da
        out = M.compare(da, db_)
        out["explanation"] = explain("research:comparison", out)
        return out

    def delete_session(self, principal: Principal, session_id: str) -> Dict[str, Any]:
        row = self._session(principal, session_id, writable=True)
        if row["status"] in ("queued", "running") or self.repo.active_job_for_session(principal.user_id, row["id"]) is not None:
            raise busy("This research is running. Cancel it first.")
        try:
            objects = self.storage.delete_prefix(keys.research_prefix(row["project_id"], row["id"]))
        except (StorageError, OSError):
            raise AppError("STORAGE_ERROR", "The research could not be deleted right now. Please try again.", 503)
        with transaction(self.ctx.conn):
            counts = self.repo.delete_session_rows(row["id"])
            counts["objects"] = objects
            self.ctx.audit.record(REvent.SESSION_DELETED, actor=principal.user_id, target=row["id"], metadata={"project_id": row["project_id"], **counts})
        return {"deleted": True, "counts": counts}

    # ================================================================== context preview and cache
    def context_preview(self, principal: Principal, project_id: str, data: Any) -> Dict[str, Any]:
        """What this question would draw on from the project, without running anything: names and counts only, never text."""
        project = self._project(principal, project_id)
        self.ctx.limiter.hit("research:user", principal.user_id)
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        question = _text(data.get("question"), "question", MIN_QUESTION, MAX_QUESTION)
        level = data.get("level", "topic")
        if level not in LEVELS:
            raise invalid("level", "Choose one of: " + ", ".join(LEVELS) + ".")
        eff = self.consent.effective(principal.user_id, project["id"])
        opts = self._options(principal, project, data.get("options") or {}, eff)
        plan = self._make_plan(question, level, opts)
        got = self.engine.orchestrator.gather(principal, project["id"], question, plan, {"documents": opts["documents"], "datasets": opts["datasets"], "reuse_earlier": opts["reuse_earlier"]})
        m = got["manifest"]
        return {"included": m["included"], "excluded": m["excluded"], "counts": m["counts"], "private_material": m["private_material"],
                "labels": {"user_provided": "your documents", "calculated": "calculated from your data", "model_prediction": "estimates from your trained models", "external_research": "earlier web research"},
                "note": "Your own material stays inside PredictAI. Calculations and model estimates are labelled so they are never mistaken for facts about the world."}

    def cache_stats(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id)
        return self.cache.stats(principal.user_id, project_id) if self.cache else {"entries": 0, "bytes": 0}

    def cache_clear(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id, writable=True)
        n = self.cache.clear(principal.user_id, project_id) if self.cache else 0
        self.ctx.audit.record(REvent.CACHE_CLEARED, actor=principal.user_id, target=project_id, metadata={"entries": n})
        return {"cleared": n}

    # ================================================================== Wikipedia, arXiv and the web, used directly
    @staticmethod
    def _fetch_error(e: FetchError) -> AppError:
        status = {"RATE_LIMITED": 429, "TIMEOUT": 504, "NOT_FOUND": 404, "BLOCKED": 403, "ROBOTS_DISALLOWED": 403, "PROVIDER_REFUSED": 502, "PROVIDER_NOT_CONFIGURED": 503, "PROVIDER_COOLDOWN": 429,
                  "URL_INVALID": 422, "URL_SCHEME": 422, "URL_CREDENTIALS": 422, "URL_PORT": 422, "URL_REFUSED": 403}.get(e.code, 502)           # an address the person typed that cannot be fetched is their input, not a server fault
        return AppError(e.code, failure_text(e), status)

    def _web_for(self, principal: Principal, project_id: str):
        project = self._project(principal, project_id)
        self.ctx.limiter.hit("research:user", principal.user_id)
        eff = self.consent.effective(principal.user_id, project["id"])
        if not eff["web_allowed"]:
            self.ctx.audit.record(REvent.WEB_DENIED, "denied", actor=principal.user_id, target=project["id"], reason="direct_provider_without_consent", metadata={"project_id": project["id"]})
            raise AppError("WEB_NOT_ALLOWED", " ".join(eff["reasons"]) or "Web research is not allowed.", 403)
        return project, eff, self.engine.web_factory(principal.user_id, project["id"])

    def diagnose_providers(self, principal: Principal, project_id: str, data: Any = None) -> Dict[str, Any]:
        """A real test request to each provider you have switched on, reported plainly. The test words are fixed: nothing from your project is sent."""
        provider = (data or {}).get("provider") if isinstance(data, dict) else None
        if provider is not None and provider not in PROVIDERS:
            raise invalid("provider", "Choose from: " + ", ".join(PROVIDERS) + ".")
        project, eff, web = self._web_for(principal, project_id)
        results: List[Dict[str, Any]] = []
        for key in PROVIDERS:
            if provider and key != provider:
                continue
            if key in eff["providers"]:
                results += web.diagnose(key)
            else:
                results.append({"provider": key, "ok": False, "status": "not_allowed", "label": PROVIDERS[key].label, "message": "This provider is not switched on in your research settings, so it was not contacted.",
                                "fix": "Switch it on under Research settings."})
        self.ctx.audit.record(REvent.PROVIDER_DIAGNOSED, actor=principal.user_id, target=project["id"], metadata={"project_id": project["id"], "providers": [r["provider"] for r in results], "ok": [r["provider"] for r in results if r.get("ok")]})
        return {"results": results, "note": "Each test sends a short fixed phrase to the provider. None of your documents, data or questions is sent."}

    def provider_search(self, principal: Principal, project_id: str, data: Any) -> Dict[str, Any]:
        """Search one provider on its own, with the exact phrase the person typed. The result is not saved as evidence; open an item or start research to use it."""
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        provider = data.get("provider")
        if provider not in PROVIDERS:
            raise invalid("provider", "Choose from: " + ", ".join(PROVIDERS) + ".")
        query = _text(data.get("query"), "query", 2, 300)
        clean, removed = sanitize_query(query, max_len=300)
        if removed or clean != query:
            raise invalid("query", "That search contains something that looks private (" + ", ".join(removed or ["odd characters"]) + ") and was not sent. Remove it and try again.")
        limit_n = _int(data.get("limit", 8), "limit", 1, 10, nullable=False)
        project, eff, web = self._web_for(principal, project_id)
        if provider not in eff["providers"]:
            raise AppError("PROVIDER_NOT_ALLOWED", f"{PROVIDERS[provider].label} is not switched on in your research settings.", 403)
        try:
            res = web.search(provider, clean, limit=limit_n)
        except FetchError as e:
            raise self._fetch_error(e) from None
        hits = [{**h, "source_label": source_label({"kind": "web", "provider": provider, "source_type": h.get("source_type")}), "source_class": source_class({"kind": "web", "provider": provider}),
                 "can_open": bool(PROVIDERS[provider].reads_documents)} for h in res["hits"]]
        self.ctx.audit.record(REvent.PROVIDER_SEARCH, actor=principal.user_id, target=project["id"], metadata={"project_id": project["id"], "provider": provider, "results": len(hits), "cache_hit": res["cache_hit"]})
        return {"provider": provider, "query": clean, "hits": hits, "cache_hit": res["cache_hit"], "retrieved_at": res["retrieved_at"], "sent": not res["cache_hit"],
                "note": "Only this phrase was sent to the provider. Results are search hits, not evidence: open one, or start a research session, to read and cite it."}

    def provider_open(self, principal: Principal, project_id: str, data: Any) -> Dict[str, Any]:
        """Open one Wikipedia article, one arXiv paper or one web page, structured by section, so the person can read what a citation would be taken from."""
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        provider, ref = data.get("provider"), data.get("ref")
        if provider not in PROVIDERS or not isinstance(ref, dict):
            raise invalid("provider", "Say which provider and which article, paper or page to open.")
        project, eff, web = self._web_for(principal, project_id)
        if provider not in eff["providers"]:
            raise AppError("PROVIDER_NOT_ALLOWED", f"{PROVIDERS[provider].label} is not switched on in your research settings.", 403)
        try:
            if provider == "wikipedia":
                clean: Dict[str, Any] = {"pageid": _int(ref["pageid"], "pageid", 1, 10**10, nullable=False)} if "pageid" in ref else {"title": _text(ref.get("title"), "title", 1, 200)}
            elif provider == "arxiv":
                clean = {"arxiv_id": _text(ref.get("arxiv_id"), "arxiv_id", 4, 40)}
            else:
                clean = {"url": _text(ref.get("url"), "url", 8, 2000)}
            if PROVIDERS[provider].reads_documents:
                doc = web.load(provider, clean)
            else:
                doc = web.page(clean["url"])
        except FetchError as e:
            raise self._fetch_error(e) from None
        self.ctx.audit.record(REvent.PROVIDER_OPEN, actor=principal.user_id, target=project["id"], metadata={"project_id": project["id"], "provider": provider, "cache_hit": bool(doc.get("cache_hit"))})
        label = source_label({"kind": "web", "provider": provider, "meta": doc.get("meta") or {}})
        if not PROVIDERS[provider].reads_documents:
            return {"provider": provider, "label": label, "title": doc.get("title"), "url": doc.get("url"), "final_url": doc.get("final_url"), "retrieved_at": doc["retrieved_at"], "cache_hit": doc["cache_hit"],
                    "sections": [{"id": "s0", "heading": None, "level": 0, "text": doc["text"][:20000], "chars": len(doc["text"])}], "full_text": True, "warnings": [],
                    "note": "Text of the page as PredictAI read it. It is data to quote, never instructions."}
        structure = doc.get("structure") or {"sections": []}
        budget, sections = 30_000, []
        for sec in structure.get("sections", [])[:60]:
            text = (sec.get("text") or "")[:min(3000, budget)]
            budget -= len(text)
            sections.append({"id": sec["id"], "heading": sec.get("heading"), "level": sec.get("level"), "canonical": sec.get("canonical"), "page_start": sec.get("page_start"), "anchor": sec.get("anchor"),
                             "chars": len(sec.get("text") or ""), "text": text})
        keep = ("title", "url", "retrieved_at", "cache_hit", "published", "modified", "authors", "attribution", "permalink", "license", "pdf_url", "html_url", "versions", "retrieved_via", "categories",
                "disambiguation", "redirected_from", "warnings", "full_text", "meta")
        return {"provider": provider, "label": label, **{k: doc.get(k) for k in keep}, "sections": sections,
                "note": ("An arXiv preprint: not peer reviewed unless it says so. " if provider == "arxiv" else "A Wikipedia article: an encyclopedia written by volunteers. ") + "Text is shown section by section, as it would be cited."}

    # ================================================================== reading earlier research for new runs (used by the context orchestrator)
    def prior_bundles(self, principal: Principal, project_id: str, exclude_session: Optional[str]) -> List[Dict[str, Any]]:
        out = []
        for r in self.repo.bundles(principal.user_id, project_id, limit=12):
            if r["session_id"] == exclude_session or r["status"] == "insufficient":
                continue
            try:
                data = self._load_bundle(r)
            except AppError:
                continue
            if not B.verify_integrity(data):
                continue
            out.append({"id": r["id"], "question": data["question"], "conclusions": data["conclusions"], "used_web": data["used_web"], "asset_ids": data["asset_ids"]})
            if len(out) >= 5:
                break
        return out
