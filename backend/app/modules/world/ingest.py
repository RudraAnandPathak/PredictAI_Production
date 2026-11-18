"""Reading a feed into articles. Only what the feed itself provides (headline, link, date, short summary) is stored; reading full article pages is not available.

Politeness and safety: feeds are fetched through the same safe fetcher as web research (no private addresses, size and time limits, robots.txt respected, one request per host at a time at a
polite pace), no faster than the source's own minimum interval; every feed item is untrusted, so instruction-like text aimed at AI systems is removed or the article is held back
(quarantined) and never analysed; the same item seen twice is stored once; articles are deleted when their retention period ends."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from ...db import transaction
from ..research.safety import scan_sentence, screen
from ..research.sources import parse_date
from ..storage import StorageError
from ..textkit import sentences
from ..webaccess.fetcher import FEED_TYPES, FetchError
from ..workspace import keys
from .derive import DERIVED_VERSION, derive, lead_of
from .feeds import FeedError, content_hash, item_ids, parse_feed

DEFAULT_POLICY = {"min_interval_s": 900, "item_cap": 50, "retention_days": 30, "full_text": False, "terms_acknowledged": True}
PERMANENT = {"NOT_FOUND", "BLOCKED", "FEED_REFUSED", "FEED_UNREADABLE", "WRONG_TYPE", "UNSAFE_URL"}
MAX_PROJECT_ARTICLES = 5000


class Ingestor:
    def __init__(self, ctx: Any, repo: Any, storage: Any, fetcher: Any, new_id: Callable[[], str]) -> None:
        self.ctx, self.repo, self.storage, self.fetcher, self.new_id = ctx, repo, storage, fetcher, new_id

    # ------------------------------------------------------------------ one source
    def due(self, source: Any, now: Any) -> Optional[str]:
        policy = {**DEFAULT_POLICY, **json.loads(source["policy"] or "{}")}
        if source["status"] == "paused":
            return "paused"
        if source["status"] == "error":
            return "stopped after an error; re-enable it to try again"
        last = parse_date(source["last_fetched_at"]) if source["last_fetched_at"] else None
        if last and (now - last).total_seconds() < policy["min_interval_s"]:
            wait = int(policy["min_interval_s"] - (now - last).total_seconds())
            return f"fetched recently; this source allows a new fetch in {wait} seconds"
        return None

    def fetch_source(self, owner: str, project_id: str, source: Any, cancelled: Callable[[], bool]) -> Dict[str, Any]:
        clock = self.ctx.clock
        now = clock.now()
        policy = {**DEFAULT_POLICY, **json.loads(source["policy"] or "{}")}
        out = {"source_id": source["id"], "name": source["name"], "status": "ok", "new": 0, "duplicates": 0, "quarantined": 0, "seen_before": 0, "removed_sentences": 0, "skipped": 0, "error": None}
        try:
            got = self.fetcher.fetch(source["feed_url"], accept=FEED_TYPES, max_bytes=1_500_000)
            feed = parse_feed(got.body, now=now, max_items=int(policy["item_cap"]))
        except FetchError as e:
            return self._failed(source, out, e.code, e.message)
        except FeedError as e:
            return self._failed(source, out, e.code, e.message)
        items = feed["items"]
        out["skipped"] = feed["skipped"] + feed["future_dated"]
        hashed = [(it, item_ids(it["guid"], it["url"], it["title"], it["published"])) for it in items]
        publisher = source["publisher"] or feed["title"]
        with transaction(self.ctx.conn):                                                         # the network call above is finished: the write lock is held only while storing
            known = self.repo.known_guids(owner, project_id, [gh for _, gh in hashed])
            for it, gh in hashed:
                if cancelled():
                    break
                if gh in known:
                    out["seen_before"] += 1
                    continue
                known.add(gh)
                self._store(owner, project_id, source, it, gh, publisher, feed["language"] or source["language"], policy, now, out)
            total = self.repo.count_articles(owner, project_id, "active") + self.repo.count_articles(owner, project_id, "quarantined")
            self.repo.update_source(source["id"], last_fetched_at=clock.iso(), last_status="ok", last_error_code=None, status="active", updated_at=clock.iso(),
                                    article_count=self._source_total(source["id"]))
        return {**out, "project_total": total}

    def _source_total(self, source_id: str) -> int:
        return self.repo.conn.execute("SELECT COUNT(*) FROM news_articles WHERE source_id = ? AND status IN ('active','quarantined')", (source_id,)).fetchone()[0]

    def _failed(self, source: Any, out: Dict[str, Any], code: str, message: str) -> Dict[str, Any]:
        clock = self.ctx.clock
        self.repo.update_source(source["id"], last_fetched_at=clock.iso(), last_status="error", last_error_code=code, updated_at=clock.iso(), **({"status": "error"} if code in PERMANENT else {}))
        return {**out, "status": "error", "error": {"code": code, "message": message}}

    def _store(self, owner: str, pid: str, source: Any, it: Dict[str, Any], gh: str, publisher: Optional[str], language: Optional[str], policy: Dict[str, Any], now: Any, out: Dict[str, Any]) -> None:
        title, summary = it["title"], it["summary"]
        title_flags = scan_sentence(title)
        kept, removed = screen(sentences(summary, 400))
        out["removed_sentences"] += len(removed)
        clean_summary = " ".join(kept)
        quarantine = None
        if title_flags or len(removed) >= 2 or (removed and not kept):
            quarantine = "Instruction-like text aimed at AI systems was found in this feed item, so it was held back and is not analysed."
        aid = self.new_id()
        chash = content_hash(title, clean_summary)
        dup = self.repo.first_with_content(owner, pid, chash)
        derived = None
        if not quarantine:
            derived = derive(title, clean_summary)
            derived["lead"] = lead_of(title, clean_summary)[:400]
        domain = urlsplit(it["url"]).hostname
        retrieved = self.ctx.clock.iso()
        text_key = keys.news_text_key(pid, aid)
        payload = {"title": title[:300], "summary": clean_summary, "url": it["url"], "published": it["published"], "author": it["author"], "retrieved_at": retrieved, "content_hash": chash, "source_id": source["id"]}
        try:
            self.storage.put(text_key, [json.dumps(payload, ensure_ascii=False).encode("utf-8")], overwrite=True)
        except (StorageError, OSError):
            out["skipped"] += 1
            return
        self.repo.insert_article(id=aid, project_id=pid, owner_user_id=owner, source_id=source["id"], url=it["url"], guid_hash=gh, title=title[:300], publisher=publisher, domain=domain, author=it["author"],
                                 published_at=it["published"], retrieved_at=retrieved, language=language, content_hash=chash, text_key=text_key, status="quarantined" if quarantine else "active",
                                 quarantine_reason=quarantine, duplicate_of=dup["id"] if dup else None, derived=json.dumps(derived) if derived else None, derived_version=DERIVED_VERSION if derived else None,
                                 expires_at=(now + timedelta(days=int(policy["retention_days"]))).strftime("%Y-%m-%dT%H:%M:%SZ"))
        if quarantine:
            out["quarantined"] += 1
        elif dup:
            out["duplicates"] += 1
        else:
            out["new"] += 1

    # ------------------------------------------------------------------ retention
    def expire(self, owner: str, project_id: str) -> int:
        """Articles past their retention date lose their text and analysis; a stub (link, outlet, date, fingerprint) stays so the same item is not fetched again."""
        now = self.ctx.clock.iso()
        n = 0
        for row in self.repo.due_for_expiry(owner, project_id, now):
            self._drop_text(row)
            self.repo.update_article(row["id"], status="expired", title="(expired)", text_key=None, derived=None, derived_version=None, event_id=None, quarantine_reason=None)
            n += 1
        extra = self.repo.count_articles(owner, project_id, "active") + self.repo.count_articles(owner, project_id, "quarantined") - MAX_PROJECT_ARTICLES
        if extra > 0:
            for row in self.repo.oldest_active(owner, project_id, extra):
                self._drop_text(row)
                self.repo.update_article(row["id"], status="expired", title="(expired)", text_key=None, derived=None, derived_version=None, event_id=None, quarantine_reason=None)
                n += 1
        return n

    def _drop_text(self, row: Any) -> None:
        if row["text_key"]:
            try:
                self.storage.delete(row["text_key"])
            except (StorageError, OSError):
                pass
