"""Fixtures for the Phase 6 world-intelligence tests: RSS documents served by the local FakeWeb, and a base class that has news switched on. No real feed is contacted, so these
tests show how the pipeline treats the documented feed shapes, not how any particular publisher's feed behaves."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

from research_support import ResearchBase


def rss(items: List[Dict[str, str]], title: str = "Test Feed") -> str:
    parts = []
    for it in items:
        parts.append("<item>" + "".join(f"<{k}>{escape(v)}</{k}>" for k, v in it.items()) + "</item>")
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(title)}</title>' + "".join(parts) + "</channel></rss>"


def item(title: str, summary: str, *, link: Optional[str] = None, guid: Optional[str] = None, when: Optional[datetime] = None, base: str = "https://news.example") -> Dict[str, str]:
    slug = "".join(c if c.isalnum() else "-" for c in title.lower())[:50]
    out = {"title": title, "link": link or f"{base}/{slug}", "guid": guid or f"{base}/{slug}", "description": summary}
    if when is not None:
        out["pubDate"] = format_datetime(when.astimezone(timezone.utc))
    return out


class WorldBase(ResearchBase):
    def setUp(self):
        super().setUp()
        self.world = self.rs.world
        self.now = self.h.clock.now()

    def days_ago(self, n: float) -> datetime:
        return self.now - timedelta(days=n)

    def allow_news(self, principal=None):
        self.svc.put_settings(principal or self.alice, None, {"news_enabled": True})

    def feed(self, path: str, items: List[Dict[str, str]], title: str = "Test Feed") -> str:
        self.web.text(path, rss(items, title), ctype="application/rss+xml; charset=utf-8")
        return self.web.base + path

    def add_source(self, url: str, name: str = "Wire", principal=None, pid=None, **extra):
        return self.world.add_source(principal or self.alice, pid or self.pid, {"name": name, "feed_url": url, "publisher": extra.pop("publisher", name), **extra})

    def ingest(self, principal=None, pid=None, source_ids=None):
        p = principal or self.alice
        job = self.world.start_ingest(p, pid or self.pid, source_ids)
        self.runtime.runner.wait(job["id"], 60)
        return self.svc.job(p, job["id"])
