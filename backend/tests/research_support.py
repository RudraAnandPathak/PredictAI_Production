"""Shared fixtures for the Phase 6 research tests: a real database and storage, the real services and job runner, and a local FakeWeb standing in for a SearXNG instance and the pages
it returns. No internet is used, so nothing here shows how the live web behaves; it shows how the engine treats the shapes it is documented to receive."""
from __future__ import annotations

import json
import unittest
from typing import Dict, List, Optional

from app.modules.research.container import ResearchRuntime, build_research
from app.modules.retrieval.corpus import CORPUS_CACHE
from app.modules.webaccess.fetcher import SafeFetcher

from fixtures import pdf
from support import Harness
from test_workspace_uploads import upload
from web_support import FakeWeb, page

RIVER_DOC = ["The river rose by 2.4 metres on Tuesday after heavy rainfall.\nOfficials evacuated 1,200 residents from low lying areas.\nThe levee held at the northern bridge.",
             "Inflation slowed to 3.1 percent in October according to the statistics office.\nCentral bank policy rates were left unchanged."]


def serp(web: FakeWeb, results: List[Dict[str, str]]) -> None:
    web.json("/search", {"results": results})


def make_fetcher(web: FakeWeb) -> SafeFetcher:
    return SafeFetcher(user_agent="PredictAI-Test/1", allow_private=True, sleeper=lambda s: None, host_interval_s=0, timeout_s=5)


class ResearchBase(unittest.TestCase):
    settings_overrides: Dict[str, object] = {}

    def setUp(self):
        CORPUS_CACHE.clear()
        self.web = FakeWeb()
        self.web.__enter__()
        self.addCleanup(lambda: self.web.__exit__())
        self.h = Harness(**{"research_web_enabled": True, "research_allow_private_network": True, "searxng_url": self.web.base, "research_fetch_timeout_s": 5, **self.settings_overrides})
        self.addCleanup(self.h.close)
        self.alice, _ = self.h.make_user("alice@example.com")
        self.bob, _ = self.h.make_user("bob@example.com")
        self.ws = self.h.workspace()
        self.runtime = ResearchRuntime(db=self.h.db, storage=self.ws.storage, settings=self.h.settings, clock=self.h.clock, hasher=self.h.hasher, sender=self.h.outbox, http=None,
                                       fetcher=make_fetcher(self.web), workers=2, sleeper=lambda s: None)
        self.addCleanup(self.runtime.runner.shutdown)
        self.rs = build_research(self.ws, self.runtime)
        self.svc = self.rs.research
        self.pid = self.ws.projects.create(self.alice, "Research")["id"]

    # ---- helpers
    def add_pdf(self, name: str, pages: List[str], principal=None, pid: Optional[str] = None) -> str:
        return upload(self.ws, principal or self.alice, pid or self.pid, name, pdf(pages))["asset"]["id"]

    def river_web(self, a_height="2.4", b_height="3.1", extra_b=None):
        """Two pages and a search result list on the fake server (and one dead link). The dead link's title and summary match the topic: a result that matched none of the question's
        topic words is now dropped before it is opened (and listed as dropped), so a dead link that should be reported has to look relevant."""
        self.web.html("/a1", page("Flood report", [f"The river rose by {a_height} metres on Tuesday after heavy rainfall according to the regional authority.", "Residents were told to avoid the low lying areas near the bridge."],
                                  published="2026-10-20T08:00:00Z"))
        self.web.html("/a2", page("Weather notes", [f"The river rose by {b_height} metres on Tuesday after heavy rainfall according to the weather service.", *(extra_b or []),
                                                    "The rainfall total was the highest recorded this decade in the region."], published="2026-10-21T08:00:00Z"))
        serp(self.web, [{"title": "Flood report", "url": self.web.base + "/a1", "content": "river rose"}, {"title": "Weather notes", "url": self.web.base + "/a2", "content": "rainfall"},
                        {"title": "River flood archive", "url": self.web.base + "/gone", "content": "river rise rainfall archive"}])

    def allow_web(self, principal=None, providers=("searxng",)):
        self.svc.put_settings(principal or self.alice, None, {"web_enabled": True, "providers": list(providers)})

    def create(self, question="How high did the river rise after the heavy rainfall?", level="topic", principal=None, pid=None, **options):
        return self.svc.create_session(principal or self.alice, pid or self.pid, {"question": question, "level": level, "options": options})

    def run_to_end(self, session_id: str, principal=None, approve=True):
        p = principal or self.alice
        s = self.svc.get_session(p, session_id)
        job = self.svc.run(p, session_id, s["plan_hash"] if approve and s["needs_approval"] else None)
        self.runtime.runner.wait(job["id"], 60)
        return self.svc.job(p, job["id"])

    def bundle_of(self, session_id: str, principal=None) -> dict:
        p = principal or self.alice
        s = self.svc.get_session(p, session_id)
        return self.svc.get_bundle(p, s["latest_bundle_id"])
