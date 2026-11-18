"""World intelligence use cases: news sources, ingestion, articles, events, coverage trends, freshness, influence analyses.

Same rules as research: every method loads its target through the owner's own principal, so a source, article, event or analysis that belongs to someone else (or to a deleted project) is
indistinguishable from one that does not exist; nothing is fetched unless the person turned news on; every feed item is untrusted data."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from ...core.errors import AppError
from ...db import transaction
from ..explanation import explain
from ..identity.authorization import Principal, build_principal, require
from ..identity.constants import Perm
from ..identity.repository import Repository as IdentityRepository
from ..identity.tokens import new_id
from ..research.consent import ConsentStore
from ..research.errors import Cancelled, ResearchFailed, busy, limit
from ..research.events import REvent
from ..research.jobs import Control
from ..research.sources import parse_date
from ..storage import StorageError
from ..webaccess.fetcher import FetchError
from ..webaccess.urlguard import UrlRefused, check_url
from ..workspace import keys
from ..workspace.errors import invalid, not_found
from . import cluster as C
from . import events as E
from . import influence as I
from . import trends as T
from .feeds import normalise_url
from .ingest import DEFAULT_POLICY, Ingestor
from .series import load_series
from .topics import TOPIC_LABELS

MAX_SOURCES = 20
MAX_INFLUENCE = 100
ANALYSIS_DAYS = 30
ANALYSIS_LIMIT = 2000
FRESH_HOURS, AGING_HOURS = 24, 72
RULE_VERSION = I.METHOD


def _text(value: Any, field: str, lo: int, hi: int) -> str:
    if not isinstance(value, str):
        raise invalid(field, "Enter some text.")
    v = " ".join(value.split())
    if not lo <= len(v) <= hi or any(ord(c) < 32 for c in v):
        raise invalid(field, f"Use {lo} to {hi} characters.")
    return v


def _json(value: Optional[str], default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def _hours(then: Optional[str], now: datetime) -> Optional[float]:
    t = parse_date(then) if then else None
    return round((now - t).total_seconds() / 3600, 1) if t else None


class WorldService:
    def __init__(self, *, ctx: Any, ws: Any, repo: Any, rrepo: Any, storage: Any, consent: ConsentStore, fetcher: Any, runner: Any = None, resolver: Any = None) -> None:
        self.ctx, self.ws, self.repo, self.rrepo, self.storage, self.consent, self.runner, self.resolver = ctx, ws, repo, rrepo, storage, consent, runner, resolver
        self.ingestor = Ingestor(ctx, repo, storage, fetcher, new_id)

    # ================================================================== access
    def _need(self, principal: Principal) -> None:
        require(principal, Perm.DATA_MANAGE_OWN)

    def _project(self, principal: Principal, project_id: str, *, writable: bool = False):
        self._need(principal)
        return self.ws.projects.get_owned(principal, project_id, writable=writable)

    def _source(self, principal: Principal, source_id: Any, *, writable: bool = False):
        self._need(principal)
        row = self.repo.source(principal.user_id, source_id) if isinstance(source_id, str) and 0 < len(source_id) <= 64 else None
        if row is None:
            raise not_found("news source")
        self.ws.projects.get_owned(principal, row["project_id"], writable=writable)
        return row

    def _article(self, principal: Principal, article_id: Any, *, writable: bool = False):
        self._need(principal)
        row = self.repo.article(principal.user_id, article_id) if isinstance(article_id, str) and 0 < len(article_id) <= 64 else None
        if row is None or row["status"] == "expired":
            raise not_found("article")
        self.ws.projects.get_owned(principal, row["project_id"], writable=writable)
        return row

    def _event(self, principal: Principal, event_id: Any):
        self._need(principal)
        row = self.repo.event(principal.user_id, event_id) if isinstance(event_id, str) and 0 < len(event_id) <= 64 else None
        if row is None:
            raise not_found("event")
        self.ws.projects.get_owned(principal, row["project_id"])
        return row

    def _news_gate(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        eff = self.consent.effective(principal.user_id, project_id)
        reasons: List[str] = []
        if not eff["platform_enabled"]:
            reasons.append("The platform owner has not enabled outside access (PREDICTAI_RESEARCH_WEB_ENABLED).")
        elif not eff["person"]["news_enabled"]:
            reasons.append("You have not turned on news.")
        elif eff["project"] and not eff["project"]["news_enabled"]:
            reasons.append("News is switched off for this project.")
        return {"allowed": bool(eff["news_allowed"]), "reasons": reasons}

    # ================================================================== sources
    def _present_source(self, r: Any, counts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = self.ctx.clock.now()
        age = _hours(r["last_fetched_at"], now)
        counts = counts or {}
        state = ("paused" if r["status"] == "paused" else "error" if r["status"] == "error" else "never fetched" if age is None else "fresh" if age <= FRESH_HOURS else "aging" if age <= AGING_HOURS else "stale")
        return {"id": r["id"], "project_id": r["project_id"], "name": r["name"], "feed_url": r["feed_url"], "publisher": r["publisher"], "language": r["language"], "status": r["status"],
                "policy": {**DEFAULT_POLICY, **_json(r["policy"], {})}, "last_fetched_at": r["last_fetched_at"], "last_status": r["last_status"], "last_error_code": r["last_error_code"],
                "hours_since_fetch": age, "freshness": state, "article_count": r["article_count"], "quarantined": counts.get("quarantined", 0), "newest_article": counts.get("newest"),
                "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def list_sources(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id)
        counts = self.repo.source_counts(principal.user_id, project_id)
        return {"items": [self._present_source(r, counts.get(r["id"])) for r in self.repo.sources(principal.user_id, project_id)]}

    def _policy(self, data: Any, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        policy = {**DEFAULT_POLICY, **(base or {})}
        if data is None:
            return policy
        if not isinstance(data, dict) or set(data) - {"min_interval_s", "item_cap", "retention_days", "full_text", "terms_acknowledged"}:
            raise invalid("policy", "Use min_interval_s, item_cap, retention_days, full_text and terms_acknowledged.")
        for key, lo, hi in (("min_interval_s", 300, 86400), ("item_cap", 1, 100), ("retention_days", 1, 365)):
            if key in data:
                v = data[key]
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    raise invalid(f"policy.{key}", f"Enter a whole number from {lo} to {hi}.")
                policy[key] = v
        if "full_text" in data:
            if data["full_text"] is not False:
                raise invalid("policy.full_text", "Reading full articles is not available. Only the headline, link, date and summary the feed provides are stored.")
        if "terms_acknowledged" in data:
            if not isinstance(data["terms_acknowledged"], bool):
                raise invalid("policy.terms_acknowledged", "Use true or false.")
            policy["terms_acknowledged"] = data["terms_acknowledged"]
        return policy

    def add_source(self, principal: Principal, project_id: str, data: Any) -> Dict[str, Any]:
        project = self._project(principal, project_id, writable=True)
        self.ctx.limiter.hit("news:user", principal.user_id)
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        name = _text(data.get("name"), "name", 1, 80)
        url = normalise_url(data.get("feed_url") if isinstance(data.get("feed_url"), str) else "")
        if not url:
            raise invalid("feed_url", "Enter the web address (http or https) of an RSS or Atom feed.")
        try:
            check_url(url, allow_private=bool(self.ctx.settings.research_allow_private_network), resolver=self.resolver)
        except (UrlRefused, FetchError) as e:
            raise invalid("feed_url", e.message)
        policy = self._policy(data.get("policy"))
        if policy.get("terms_acknowledged") is not True:
            raise invalid("policy.terms_acknowledged", "Confirm that you have checked the feed's terms allow this use.")
        publisher = _text(data["publisher"], "publisher", 1, 80) if data.get("publisher") else None
        language = _text(data["language"], "language", 2, 12).lower() if data.get("language") else None
        if self.repo.count_sources(principal.user_id, project["id"]) >= MAX_SOURCES:
            raise limit(f"A project can follow {MAX_SOURCES} news sources. Remove one you no longer need.")
        if self.repo.source_by_url(principal.user_id, project["id"], url):
            raise AppError("SOURCE_EXISTS", "You already follow this feed in this project.", 409)
        sid, now = new_id(), self.ctx.clock.iso()
        with transaction(self.ctx.conn):
            self.repo.create_source(id=sid, project_id=project["id"], owner_user_id=principal.user_id, name=name, feed_url=url, publisher=publisher, language=language, policy=json.dumps(policy),
                                    created_at=now, updated_at=now)
            self.ctx.audit.record(REvent.NEWS_SOURCE_ADDED, actor=principal.user_id, target=sid, metadata={"project_id": project["id"]})
        return self._present_source(self.repo.source(principal.user_id, sid))

    def update_source(self, principal: Principal, source_id: str, data: Any) -> Dict[str, Any]:
        row = self._source(principal, source_id, writable=True)
        self.ctx.limiter.hit("news:user", principal.user_id)
        if not isinstance(data, dict) or not data or set(data) - {"name", "publisher", "language", "status", "policy"}:
            raise invalid("body", "Send one or more of: name, publisher, language, status, policy.")
        fields: Dict[str, Any] = {}
        if "name" in data:
            fields["name"] = _text(data["name"], "name", 1, 80)
        if "publisher" in data:
            fields["publisher"] = _text(data["publisher"], "publisher", 1, 80) if data["publisher"] else None
        if "language" in data:
            fields["language"] = _text(data["language"], "language", 2, 12).lower() if data["language"] else None
        if "status" in data:
            if data["status"] not in ("active", "paused"):
                raise invalid("status", "Use 'active' or 'paused'.")
            fields["status"] = data["status"]
            if data["status"] == "active":
                fields["last_error_code"] = None
        if "policy" in data:
            fields["policy"] = json.dumps(self._policy(data["policy"], _json(row["policy"], {})))
        fields["updated_at"] = self.ctx.clock.iso()
        with transaction(self.ctx.conn):
            self.repo.update_source(row["id"], **fields)
            self.ctx.audit.record(REvent.NEWS_SOURCE_CHANGED, actor=principal.user_id, target=row["id"], metadata={"project_id": row["project_id"], "changed": sorted(fields)})
        return self._present_source(self.repo.source(principal.user_id, row["id"]))

    def delete_source(self, principal: Principal, source_id: str) -> Dict[str, Any]:
        row = self._source(principal, source_id, writable=True)
        if self.rrepo.conn.execute("SELECT 1 FROM research_jobs WHERE owner_user_id = ? AND project_id = ? AND kind = 'news_ingest' AND status IN ('queued','running')", (principal.user_id, row["project_id"])).fetchone():
            raise busy("A news fetch is running for this project. Wait for it to finish or cancel it.")
        removed = 0
        for key in self.repo.article_keys_for_source(row["id"]):
            try:
                self.storage.delete(key)
                removed += 1
            except (StorageError, OSError):
                raise AppError("STORAGE_ERROR", "The source could not be deleted right now. Please try again.", 503)
        with transaction(self.ctx.conn):
            self.repo.delete_source(row["id"])
            self._rebuild(principal.user_id, row["project_id"])
            self.ctx.audit.record(REvent.NEWS_SOURCE_DELETED, actor=principal.user_id, target=row["id"], metadata={"project_id": row["project_id"], "objects": removed})
        return {"deleted": True, "articles_removed": removed}

    # ================================================================== fetching
    def start_ingest(self, principal: Principal, project_id: str, source_ids: Any = None) -> Dict[str, Any]:
        project = self._project(principal, project_id, writable=True)
        self.ctx.limiter.hit("news_ingest:user", principal.user_id)
        gate = self._news_gate(principal, project["id"])
        if not gate["allowed"]:
            self.ctx.audit.record(REvent.WEB_DENIED, "denied", actor=principal.user_id, target=project["id"], reason="news_not_allowed", metadata={"project_id": project["id"]})
            raise AppError("NEWS_NOT_ALLOWED", " ".join(gate["reasons"]) or "News is not allowed.", 403)
        if self.runner is None:
            raise AppError("RESEARCH_UNAVAILABLE", "Background jobs are not available on this server.", 503)
        ids: Optional[List[str]] = None
        if source_ids is not None:
            if not isinstance(source_ids, list) or not source_ids or len(source_ids) > MAX_SOURCES or not all(isinstance(x, str) for x in source_ids):
                raise invalid("source_ids", "Choose one or more of your sources.")
            ids = list(dict.fromkeys(source_ids))
            for sid in ids:
                if self._source(principal, sid)["project_id"] != project["id"]:
                    raise not_found("news source")
        if not self.repo.sources(principal.user_id, project["id"]):
            raise AppError("NO_SOURCES", "Add a news source first.", 409)
        if self.rrepo.conn.execute("SELECT 1 FROM research_jobs WHERE owner_user_id = ? AND project_id = ? AND kind = 'news_ingest' AND status IN ('queued','running')", (principal.user_id, project["id"])).fetchone():
            raise busy("A news fetch is already running for this project.")
        job_id, now = new_id(), self.ctx.clock.iso()
        with transaction(self.ctx.conn):
            self.rrepo.create_job(id=job_id, kind="news_ingest", owner_user_id=principal.user_id, project_id=project["id"], subject_id=ids[0] if ids and len(ids) == 1 else None, status="queued",
                                  params=json.dumps({"source_ids": ids}), extended=0, progress=json.dumps({"phase": "queued", "fraction": 0, "message": "Waiting to start"}), created_at=now)
            self.ctx.audit.record(REvent.RUN_REQUESTED, actor=principal.user_id, target=job_id, metadata={"project_id": project["id"], "kind": "news_ingest", "sources": len(ids) if ids else None})
        self.runner.submit(job_id)
        return self._present_job(self.rrepo.job(principal.user_id, job_id))

    def _present_job(self, j: Any) -> Dict[str, Any]:
        return {"id": j["id"], "kind": j["kind"], "status": j["status"], "progress": _json(j["progress"], None), "error_code": j["error_code"], "error_message": j["error_message"],
                "result_id": j["result_id"], "created_at": j["created_at"], "started_at": j["started_at"], "finished_at": j["finished_at"], "cancel_requested": bool(j["cancel_requested"])}

    def _job_principal(self, job: Any) -> Principal:
        user = IdentityRepository(self.ctx.conn).user_by_id(job["owner_user_id"])
        if user is None or user["status"] != "active":
            raise Cancelled()
        principal = build_principal(IdentityRepository(self.ctx.conn), user)
        if not principal.can(Perm.DATA_MANAGE_OWN):
            raise Cancelled()
        try:
            self.ws.projects.get_owned(principal, job["project_id"], writable=True)
        except AppError:
            raise Cancelled()
        return principal

    def job_ingest(self, job: Any, ctl: Control) -> Optional[str]:
        principal = self._job_principal(job)
        owner, pid = principal.user_id, job["project_id"]
        if not self._news_gate(principal, pid)["allowed"]:                                      # consent is read again at run time
            raise ResearchFailed("NEWS_NOT_ALLOWED", "News was switched off before this fetch started, so nothing was fetched.")
        wanted = _json(job["params"], {}).get("source_ids")
        sources = [s for s in self.repo.sources(owner, pid) if not wanted or s["id"] in wanted]
        results: List[Dict[str, Any]] = []
        for i, s in enumerate(sources):
            if ctl.cancelled():
                raise Cancelled()
            ctl.progress("fetching", 0.05 + 0.75 * i / max(1, len(sources)), f"Reading {s['name']}")
            why = self.ingestor.due(s, self.ctx.clock.now())
            if why:
                results.append({"source_id": s["id"], "name": s["name"], "status": "skipped", "reason": why})
                continue
            results.append(self.ingestor.fetch_source(owner, pid, s, ctl.cancelled))                # the network read happens outside any transaction; storing is atomic inside
        ctl.progress("analysing", 0.85, "Grouping articles into events")
        with transaction(self.ctx.conn):
            expired = self.ingestor.expire(owner, pid)
            counts = self._rebuild(owner, pid)
        new = sum(r.get("new", 0) for r in results)
        failed = [r for r in results if r["status"] == "error"]
        self.ctx.audit.record(REvent.NEWS_INGESTED, actor=owner, target=job["id"], metadata={"project_id": pid, "sources": len(results), "new": new, "failed": len(failed), "expired": expired, "events": counts["events"]})
        ctl.summary = {"message": f"{new} new article(s); {len(failed)} source(s) failed", "results": results}
        if results and len(failed) == len(results):
            raise ResearchFailed("INGEST_FAILED", "No source could be read. " + (failed[0]["error"]["message"] if failed[0].get("error") else ""))
        return None

    def expire_everywhere(self) -> int:
        """Housekeeping at start-up: apply every project's retention dates (no person is acting, so no consent or rate limit is involved; nothing is fetched)."""
        total = 0
        for row in self.repo.projects_due_for_expiry(self.ctx.clock.iso()):
            with transaction(self.ctx.conn):
                total += self.ingestor.expire(row["owner_user_id"], row["project_id"])
                self._rebuild(row["owner_user_id"], row["project_id"])
        return total

    # ================================================================== articles
    def _lead(self, r: Any, derived: Optional[Dict[str, Any]]) -> str:
        return (derived or {}).get("lead", "")

    def _present_article(self, r: Any, *, detail: bool = False) -> Dict[str, Any]:
        d = _json(r["derived"], None)
        topics = [{"topic": t["topic"], "label": t["label"], "strength": t["strength"], "matched": t["matched"]} for t in (d or {}).get("topics", {}).get("topics", [])]
        out = {"id": r["id"], "project_id": r["project_id"], "source_id": r["source_id"], "url": r["url"], "title": r["title"], "publisher": r["publisher"], "domain": r["domain"], "author": r["author"],
               "published_at": r["published_at"], "retrieved_at": r["retrieved_at"], "language": r["language"], "status": r["status"], "quarantine_reason": r["quarantine_reason"], "duplicate_of": r["duplicate_of"],
               "event_id": r["event_id"], "topics": topics, "entities": [{"text": e["text"], "kind": e["kind"]} for e in ((d or {}).get("entities") or {}).get("entities", [])[:6]],
               "tone": ((d or {}).get("tone") or {}).get("label"), "statement_kind": ((d or {}).get("statement") or {}).get("kind"), "origin": "external_research", "expires_at": r["expires_at"]}
        if detail:
            out["derived"] = d
            summary = ""
            if r["text_key"]:
                try:
                    summary = json.loads(self.storage.read_bytes(r["text_key"], 200_000).decode("utf-8")).get("summary", "")
                except (StorageError, OSError, ValueError):
                    summary = ""
            out["summary"] = summary
            out["note"] = "Only the headline and summary the feed provides are stored. Everything here is the outlet's own wording, quoted; PredictAI has not checked it."
        return out

    def list_articles(self, principal: Principal, project_id: str, *, status: Optional[str] = None, source_id: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
                      topic: Optional[str] = None, entity: Optional[str] = None, tone: Optional[str] = None, event_id: Optional[str] = None, q: Optional[str] = None,
                      limit_: int = 50, offset: int = 0) -> Dict[str, Any]:
        self._project(principal, project_id)
        if status not in (None, "active", "quarantined", "expired"):
            raise invalid("status", "Use active, quarantined or expired.")
        if topic is not None and topic not in TOPIC_LABELS:
            raise invalid("topic", "Unknown topic.")
        if tone not in (None, "positive", "negative", "neutral", "mixed"):
            raise invalid("tone", "Use positive, negative, neutral or mixed.")
        for name, v in (("since", since), ("until", until)):
            if v is not None and parse_date(v) is None:
                raise invalid(name, "Use a date such as 2026-10-20.")
        if q is not None:
            q = _text(q, "q", 2, 100)
        if entity is not None:
            entity = _text(entity, "entity", 1, 100)
        limit_, offset = max(1, min(int(limit_), 200)), max(0, int(offset))
        post = bool(topic or entity or tone)
        rows, total = self.repo.articles(principal.user_id, project_id, status=status, source_id=source_id, since=since, until=until, event_id=event_id, q=q,
                                         limit=500 if post else limit_, offset=0 if post else offset)
        items = [self._present_article(r) for r in rows]
        if post:                                                                                 # topic, name and tone live inside the analysis JSON, so they are filtered after the query
            items = [a for a in items if (not topic or topic in [t["topic"] for t in a["topics"]]) and (not entity or entity.lower() in [e["text"].lower() for e in a["entities"]])
                     and (not tone or a["tone"] == tone)]
            total = len(items)
            items = items[offset:offset + limit_]
        return {"items": items, "total": total, "limit": limit_, "offset": offset}

    def get_article(self, principal: Principal, article_id: str) -> Dict[str, Any]:
        return self._present_article(self._article(principal, article_id), detail=True)

    def delete_article(self, principal: Principal, article_id: str) -> Dict[str, Any]:
        row = self._article(principal, article_id, writable=True)
        if row["text_key"]:
            try:
                self.storage.delete(row["text_key"])
            except (StorageError, OSError):
                raise AppError("STORAGE_ERROR", "The article could not be deleted right now. Please try again.", 503)
        with transaction(self.ctx.conn):
            self.repo.delete_article(row["id"])
            self._rebuild(principal.user_id, row["project_id"])
        return {"deleted": True}

    # ================================================================== events
    def _article_dict(self, r: Any) -> Dict[str, Any]:
        d = _json(r["derived"], {}) or {}
        ents = [e["text"].lower() for e in (d.get("entities") or {}).get("entities", []) if e["kind"] != "acronym" or len(e["text"]) >= 3]
        return {"id": r["id"], "title": r["title"], "lead": d.get("lead", ""), "summary": d.get("lead", ""), "publisher": r["publisher"], "domain": r["domain"], "published": r["published_at"] or None,
                "url": r["url"], "derived": d, "entities": ents}

    def _rebuild(self, owner: str, project_id: str) -> Dict[str, int]:
        now = self.ctx.clock.now()
        since = (now - timedelta(days=ANALYSIS_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = self.repo.active_for_analysis(owner, project_id, since, ANALYSIS_LIMIT)
        arts = [self._article_dict(r) for r in rows]
        by_id = {a["id"]: a for a in arts}
        res = C.cluster(arts) if arts else {"groups": [], "skipped": 0, "method": C.METHOD, "parameters": {}}
        old_members: Dict[str, set] = {}
        for r in self.repo.conn.execute("SELECT id, event_id FROM news_articles WHERE owner_user_id = ? AND project_id = ? AND event_id IS NOT NULL", (owner, project_id)):
            old_members.setdefault(r["event_id"], set()).add(r["id"])
        old_events = {e["id"] for e in self.repo.all_events(owner, project_id)}
        taken: set = set()
        pairs: List[Any] = []
        stamp = self.ctx.clock.iso()
        self.repo.clear_event_ids(owner, project_id)
        for group in res["groups"]:
            members = [by_id[i] for i in group]
            s = set(group)
            best, best_j = None, 0.0
            for eid, o in old_members.items():
                if eid in taken:
                    continue
                j = len(s & o) / len(s | o)
                if j > best_j:
                    best, best_j = eid, j
            eid = best if best is not None and best_j >= 0.5 else new_id()
            taken.add(eid)
            summ = E.summarise(members)
            self.repo.upsert_event(id=eid, project_id=project_id, owner_user_id=owner, label=summ["label"][:300], first_seen=summ["first_seen"], last_seen=summ["last_seen"], article_count=summ["article_count"],
                                   source_count=summ["source_count"], status=summ["status"], members_hash=E.members_hash(group), updated_at=stamp,
                                   summary=json.dumps({k: summ[k] for k in ("basis", "topics", "entities", "outlets", "statement_kinds", "disagreements", "label_from", "note", "method", "undated")}
                                                      | {"cluster": {"method": res["method"], **res["parameters"]}}))
            pairs += [(eid, i) for i in group]
        self.repo.set_event_ids(pairs)
        self.repo.delete_events(old_events - taken)
        return {"events": len(res["groups"]), "articles": len(arts), "skipped": res["skipped"]}

    def rebuild_events(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id, writable=True)
        self.ctx.limiter.hit("news:user", principal.user_id)
        with transaction(self.ctx.conn):
            return self._rebuild(principal.user_id, project_id)

    def _present_event(self, r: Any) -> Dict[str, Any]:
        s = _json(r["summary"], {}) or {}
        return {"id": r["id"], "project_id": r["project_id"], "label": r["label"], "first_seen": r["first_seen"], "last_seen": r["last_seen"], "article_count": r["article_count"],
                "source_count": r["source_count"], "status": r["status"], "basis": s.get("basis"), "topics": s.get("topics", []), "entities": s.get("entities", []), "outlets": s.get("outlets", []),
                "statement_kinds": s.get("statement_kinds", {}), "disagreements": s.get("disagreements", []), "label_from": s.get("label_from"), "undated": s.get("undated", 0), "note": s.get("note"),
                "method": s.get("method"), "cluster": s.get("cluster"), "updated_at": r["updated_at"]}

    def list_events(self, principal: Principal, project_id: str, *, status: Optional[str] = None, min_sources: int = 1, limit_: int = 50, offset: int = 0) -> Dict[str, Any]:
        self._project(principal, project_id)
        if status not in (None, "confirmed", "reported", "disputed", "unverified"):
            raise invalid("status", "Use confirmed, reported, disputed or unverified.")
        rows, total = self.repo.events(principal.user_id, project_id, status=status, min_sources=max(1, min(int(min_sources), 50)), limit=max(1, min(int(limit_), 200)), offset=max(0, int(offset)))
        return {"items": [self._present_event(r) for r in rows], "total": total}

    def get_event(self, principal: Principal, event_id: str) -> Dict[str, Any]:
        row = self._event(principal, event_id)
        event = self._present_event(row)
        arts, _ = self.repo.articles(principal.user_id, row["project_id"], event_id=row["id"], limit=200)
        members = [self._article_dict(a) for a in arts]
        others = [self._present_event(e) for e in self.repo.all_events(principal.user_id, row["project_id"])]
        return {"event": event, "articles": [self._present_article(a) for a in arts], "timeline": E.timeline(members), "comparison": E.compare_outlets(members),
                "related": E.related(event, others), "explanation": explain("world:event", {"event": event})}

    def event_summary(self, principal: Principal, event_id: str) -> Dict[str, Any]:
        """What other parts of the platform may reuse: a labelled, attributed summary. Reported statements are never presented as facts."""
        got = self.get_event(principal, event_id)
        ev = got["event"]
        return {"schema": "predictai.world_event_summary/1", "event_id": ev["id"], "label": ev["label"], "status": ev["status"], "basis": ev["basis"], "origin": "external_research", "statement_type": "reported",
                "first_seen": ev["first_seen"], "last_seen": ev["last_seen"], "outlets": ev["outlets"], "disagreements": ev["disagreements"],
                "articles": [{"headline": a["title"], "outlet": a["publisher"], "published": a["published_at"], "url": a["url"], "statement_kind": a["statement_kind"]} for a in got["articles"][:20]],
                "caveat": "Reported by news outlets and not verified by PredictAI. Agreement between outlets is not proof."}

    # ================================================================== topics, trends, freshness, overview
    def _recent(self, principal: Principal, project_id: str, days: int) -> List[Dict[str, Any]]:
        since = (self.ctx.clock.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return [self._article_dict(r) for r in self.repo.active_for_analysis(principal.user_id, project_id, since, ANALYSIS_LIMIT)]

    def trends(self, principal: Principal, project_id: str, window_days: int = 7) -> Dict[str, Any]:
        self._project(principal, project_id)
        if isinstance(window_days, bool) or not isinstance(window_days, int) or not 1 <= window_days <= 30:
            raise invalid("window_days", "Enter a whole number from 1 to 30.")
        arts = self._recent(principal, project_id, 2 * window_days)
        for a in arts:
            a["published"] = a["published"]
        out = T.trends(arts, window_days=window_days, now=self.ctx.clock.now())
        out["explanation"] = explain("world:trends", out)
        return out

    def topics(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id)
        counts: Dict[str, int] = {}
        for a in self._recent(principal, project_id, ANALYSIS_DAYS):
            for t in ((a["derived"] or {}).get("topics") or {}).get("topics", []):
                counts[t["topic"]] = counts.get(t["topic"], 0) + 1
        return {"items": [{"topic": k, "label": TOPIC_LABELS[k], "articles": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))], "days": ANALYSIS_DAYS,
                "method": "keyword-rules/1", "note": "Topics come from simple keyword rules and are a guide to browsing."}

    def freshness(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id)
        now = self.ctx.clock.now()
        counts = self.repo.source_counts(principal.user_id, project_id)
        items, warnings = [], []
        for r in self.repo.sources(principal.user_id, project_id):
            p = self._present_source(r, counts.get(r["id"]))
            p["newest_article_age_hours"] = _hours(p["newest_article"], now)
            items.append(p)
            if p["freshness"] in ("stale", "never fetched") and r["status"] == "active":
                warnings.append(f"{p['name']}: " + ("never fetched" if p["freshness"] == "never fetched" else "not fetched for more than three days") + ", so its news may be out of date.")
            if r["status"] == "error":
                warnings.append(f"{p['name']}: stopped after an error ({r['last_error_code']}).")
        return {"sources": items, "warnings": warnings, "thresholds_hours": {"fresh": FRESH_HOURS, "aging": AGING_HOURS},
                "note": "Freshness is how recently a feed was read and how recent its newest article is. It says nothing about whether the news is accurate."}

    def overview(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id)
        gate = self._news_gate(principal, project_id)
        ev = {s: self.repo.events(principal.user_id, project_id, status=s, limit=1)[1] for s in ("confirmed", "reported", "disputed", "unverified")}
        return {"news_allowed": gate["allowed"], "why_not": gate["reasons"], "sources": self.repo.count_sources(principal.user_id, project_id), "articles": self.repo.count_articles(principal.user_id, project_id, "active"),
                "quarantined": self.repo.count_articles(principal.user_id, project_id, "quarantined"), "events": ev, "analyses": self.repo.count_influences(principal.user_id, project_id),
                "freshness_warnings": self.freshness(principal, project_id)["warnings"]}

    # ================================================================== influence
    def start_influence(self, principal: Principal, project_id: str, data: Any) -> Dict[str, Any]:
        project = self._project(principal, project_id, writable=True)
        self.ctx.limiter.hit("news:user", principal.user_id)
        if not isinstance(data, dict):
            raise invalid("body", "Send a JSON object.")
        asset_id = data.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise invalid("asset_id", "Choose a dataset.")
        asset = self.ws.assets.get_owned(principal, asset_id)
        if asset["project_id"] != project["id"] or asset["kind"] != "dataset":
            raise not_found("dataset")
        measure = data.get("measure", "count")
        topic, entity = data.get("topic"), data.get("entity")
        if measure not in ("count", "tone"):
            raise invalid("measure", "Use 'count' or 'tone'.")
        if topic is not None and topic not in TOPIC_LABELS:
            raise invalid("topic", "Unknown topic.")
        if topic is not None and entity is not None:
            raise invalid("entity", "Choose a topic or a name, not both.")
        entity = _text(entity, "entity", 1, 100) if entity is not None else None
        max_lag = data.get("max_lag", 7)
        if isinstance(max_lag, bool) or not isinstance(max_lag, int) or not 0 <= max_lag <= I.MAX_LAG:
            raise invalid("max_lag", f"Enter a whole number from 0 to {I.MAX_LAG}.")
        _, _, _, types = self.ws.analytics.open_dataset(principal, asset_id)
        for field, want in (("date_column", ("date", "datetime")), ("value_column", ("integer", "float"))):
            col = data.get(field)
            if not isinstance(col, str) or col not in types or types[col] not in want:
                raise invalid(field, "Choose " + ("a date column." if field == "date_column" else "a numeric column."))
        aggregate = data.get("aggregate", "mean")
        label = _text(data["label"], "label", 1, 120) if data.get("label") else None
        if self.repo.count_influences(principal.user_id, project["id"]) >= MAX_INFLUENCE:
            raise limit(f"A project can hold {MAX_INFLUENCE} influence analyses. Delete some you no longer need.")
        if self.runner is None:
            raise AppError("RESEARCH_UNAVAILABLE", "Background jobs are not available on this server.", 503)
        if self.rrepo.active_jobs(principal.user_id) >= 3:
            raise limit("You can have 3 background jobs at once. Wait for one to finish or cancel one.")
        config = {"asset_id": asset_id, "date_column": data["date_column"], "value_column": data["value_column"], "aggregate": aggregate, "measure": measure, "topic": topic, "entity": entity,
                  "max_lag": max_lag, "label": label}
        job_id, now = new_id(), self.ctx.clock.iso()
        with transaction(self.ctx.conn):
            self.rrepo.create_job(id=job_id, kind="influence", owner_user_id=principal.user_id, project_id=project["id"], subject_id=asset_id, status="queued", params=json.dumps(config),
                                  extended=int(principal.can(Perm.COMPUTE_EXTENDED)), progress=json.dumps({"phase": "queued", "fraction": 0, "message": "Waiting to start"}), created_at=now)
            self.ctx.audit.record(REvent.RUN_REQUESTED, actor=principal.user_id, target=job_id, metadata={"project_id": project["id"], "kind": "influence"})
        self.runner.submit(job_id)
        return self._present_job(self.rrepo.job(principal.user_id, job_id))

    def job_influence(self, job: Any, ctl: Control) -> Optional[str]:
        principal = self._job_principal(job)
        cfg = _json(job["params"], {})
        owner, pid = principal.user_id, job["project_id"]
        ctl.progress("reading", 0.1, "Reading your dataset")
        try:
            ds = load_series(self.ws, principal, cfg["asset_id"], cfg["date_column"], cfg["value_column"], cfg["aggregate"])
        except AppError as e:
            raise ResearchFailed(e.code, e.message)
        if ctl.cancelled():
            raise Cancelled()
        ctl.progress("coverage", 0.3, "Counting coverage by day")
        arts = [self._article_dict(r) for r in self.repo.active_for_analysis(owner, pid, None, ANALYSIS_LIMIT)]
        news = T.daily_series(arts, topic=cfg.get("topic"), entity=cfg.get("entity"), measure=cfg["measure"])
        if news and cfg["measure"] == "count":
            news = T.fill_days(news, min(news), max(news), 0.0)                                  # a day with no matching article is a real zero, not a missing day
        ctl.progress("testing", 0.45, "Testing for association")
        result = I.analyse(news, ds["series"], max_lag=cfg["max_lag"])
        if ctl.cancelled():
            raise Cancelled()
        payload = {"config": cfg, "dataset": ds["dataset"], "dataset_days": ds["days"], "dataset_rows_used": ds["rows_used"], "dataset_warnings": ds["warnings"], "coverage_days": len(news),
                   "coverage_articles": len(arts), "coverage_range": [min(news), max(news)] if news else None, "result": result}
        rhash = I.result_hash(result)
        aid = new_id()
        input_hash = hashlib.sha256(json.dumps({"news": news, "series": ds["series"], "cfg": cfg}, sort_keys=True).encode()).hexdigest()
        key = keys.influence_key(pid, aid)
        self.storage.put(key, [json.dumps(payload, ensure_ascii=False).encode("utf-8")], overwrite=True)
        try:
            with transaction(self.ctx.conn):
                self.repo.create_influence(id=aid, project_id=pid, owner_user_id=owner, label=cfg.get("label"), config=json.dumps(cfg), rule_version=RULE_VERSION, input_hash=input_hash, result_key=key,
                                           result_hash=rhash, candidate_count=len(result["raw"]["tested"]) if result.get("raw") else 0, created_at=self.ctx.clock.iso())
                self.ctx.audit.record(REvent.INFLUENCE_RUN, actor=owner, target=aid, metadata={"project_id": pid, "evidence": result["evidence"], "overlap_days": result["overlap_days"]})
        except BaseException:
            try:
                self.storage.delete(key)
            except (StorageError, OSError):
                pass
            raise
        return aid

    def _present_influence(self, r: Any, *, detail: bool = False) -> Dict[str, Any]:
        out = {"id": r["id"], "project_id": r["project_id"], "label": r["label"], "config": _json(r["config"], {}), "rule_version": r["rule_version"], "created_at": r["created_at"],
               "input_hash": r["input_hash"], "result_hash": r["result_hash"]}
        if detail:
            try:
                payload = json.loads(self.storage.read_bytes(r["result_key"], 5_000_000).decode("utf-8"))
            except (StorageError, OSError, ValueError):
                raise not_found("influence analysis")
            ok = I.result_hash(payload["result"]) == r["result_hash"]
            out.update(payload=payload, integrity_ok=ok, explanation=explain("world:influence", payload["result"]) if ok else None,
                       warnings=([] if ok else ["This saved analysis no longer matches its fingerprint. It may have been altered or damaged; do not rely on it."]) + list(payload["result"].get("warnings", [])))
        else:
            out["evidence"] = None
        return out

    def list_influence(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        self._project(principal, project_id)
        return {"items": [self._present_influence(r) for r in self.repo.influences(principal.user_id, project_id)]}

    def get_influence(self, principal: Principal, analysis_id: str) -> Dict[str, Any]:
        self._need(principal)
        row = self.repo.influence(principal.user_id, analysis_id) if isinstance(analysis_id, str) and 0 < len(analysis_id) <= 64 else None
        if row is None:
            raise not_found("influence analysis")
        self.ws.projects.get_owned(principal, row["project_id"])
        return self._present_influence(row, detail=True)

    def delete_influence(self, principal: Principal, analysis_id: str) -> Dict[str, Any]:
        self._need(principal)
        row = self.repo.influence(principal.user_id, analysis_id) if isinstance(analysis_id, str) and 0 < len(analysis_id) <= 64 else None
        if row is None:
            raise not_found("influence analysis")
        self.ws.projects.get_owned(principal, row["project_id"], writable=True)
        try:
            self.storage.delete(row["result_key"])
        except (StorageError, OSError):
            raise AppError("STORAGE_ERROR", "The analysis could not be deleted right now. Please try again.", 503)
        with transaction(self.ctx.conn):
            self.repo.delete_influence(row["id"])
        return {"deleted": True}
