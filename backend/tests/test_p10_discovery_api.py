"""R2 over HTTP: auth/CSRF on every discovery route, the search -> save -> import -> worker flow, isolation between users, and that no key or other secret is ever returned."""
import os
import unittest
from unittest import mock

try:
    import test_identity_api as base
except unittest.SkipTest:
    raise

from test_p10_api import P, RiApi
from p10_discovery_support import Routed, ok, CROSSREF_WORKS, OPENALEX_WORKS
from p10_support import make_pdf, page, FakeFetcher
from app.modules.research_intelligence.discovery.providers import ProviderGate
from app.modules.research_intelligence.discovery.service import DiscoveryService
from test_p10_discovery import standard_fetcher


class Discovery(RiApi):
    def setUp(self):
        super().setUp()
        self.fetcher = standard_fetcher()
        self.api.app.state.ri_discovery_factory = lambda ctx: DiscoveryService(ctx, fetcher=self.fetcher, gate=ProviderGate(sleep=lambda s: None, max_wait_s=0.0 if False else 4.0))

    def test_search_save_import_and_process_over_http(self):
        alice = self.user("alice@example.com")
        wid = self.workspace(alice)
        prov = alice("GET", f"{P}/discovery/providers")
        self.assertEqual(prov.status_code, 200, prov.text)
        self.assertEqual([p["key"] for p in prov.json()["data"]["items"]], ["crossref", "openalex", "arxiv", "semanticscholar", "searxng"])
        with mock.patch.dict(os.environ, {"PREDICTAI_RI_OPENALEX_API_KEY": "TOP-SECRET-KEY"}):
            r = alice("POST", f"{P}/discovery/search", {"query": "artificial intelligence employment", "providers": ["crossref", "openalex"], "workspace_id": wid})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("TOP-SECRET-KEY", r.text)
            prov_text = alice("GET", f"{P}/discovery/providers").text
            self.assertNotIn("TOP-SECRET-KEY", prov_text)
            self.assertIn('"key_configured":true', prov_text.replace(" ", ""))
        data = r.json()["data"]
        self.assertTrue(data["items"])
        sid, top = data["search_id"], data["items"][0]
        page2 = alice("GET", f"{P}/discovery/searches/{sid}", params={"page": 1, "page_size": 5, "open_access": "true"})
        self.assertEqual(page2.status_code, 200, page2.text)
        saved = alice("POST", f"{P}/discovery/library", {"search_id": sid, "rid": top["rid"]})
        self.assertEqual(saved.status_code, 201, saved.text)
        lid = saved.json()["data"]["library_id"]
        imp = alice("POST", f"{P}/workspaces/{wid}/discovery/import", {"library_id": lid, "full_text": False})
        self.assertEqual(imp.status_code, 201, imp.text)
        self.assertEqual(imp.json()["data"]["status"], "queued")
        self.assertEqual(self.drain(), ["succeeded"])
        src = alice("GET", f"{P}/workspaces/{wid}/sources/{imp.json()['data']['id']}").json()["data"]
        self.assertEqual((src["status"], src["access"], src["origin"]), ("ready", "abstract_only", "discovery"))
        again = alice("POST", f"{P}/workspaces/{wid}/discovery/import", {"library_id": lid})
        self.assertEqual(again.status_code, 409, again.text)
        self.assertEqual(len(alice("GET", f"{P}/discovery/searches").json()["data"]["items"]), 1)
        self.assertEqual(alice("DELETE", f"{P}/discovery/library/{lid}").json()["data"]["workspace_sources_kept"], 1)
        self.assertEqual(alice("GET", f"{P}/workspaces/{wid}/sources/{src['id']}").status_code, 200)

    def test_validation_errors_are_400_not_500(self):
        alice = self.user("alice@example.com")
        for body in ({}, {"query": "x", "type": "poem"}, {"query": "x", "year_from": 2022, "year_to": 2020}, {"doi": "zzz"}, {"query": "x", "providers": ["bing"]}):
            r = alice("POST", f"{P}/discovery/search", body)
            self.assertIn(r.status_code, (400, 422), f"{body} -> {r.status_code} {r.text}")
        self.assertIn(alice("POST", f"{P}/discovery/compare", {"ids": ["a"]}).status_code, (400, 422))

    def test_other_users_cannot_see_searches_library_or_import_into_foreign_workspaces(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        wid = self.workspace(alice)
        data = alice("POST", f"{P}/discovery/search", {"query": "artificial intelligence employment", "providers": ["crossref"]}).json()["data"]
        sid, rid = data["search_id"], data["items"][0]["rid"]
        self.assertEqual(bob("GET", f"{P}/discovery/searches/{sid}").status_code, 404)
        self.assertEqual(bob("POST", f"{P}/discovery/library", {"search_id": sid, "rid": rid}).status_code, 404)
        lid = alice("POST", f"{P}/discovery/library", {"search_id": sid, "rid": rid}).json()["data"]["library_id"]
        self.assertEqual(bob("GET", f"{P}/discovery/library/{lid}").status_code, 404)
        self.assertEqual(bob("DELETE", f"{P}/discovery/library/{lid}").status_code, 404)
        self.assertEqual(bob("POST", f"{P}/workspaces/{wid}/discovery/import", {"library_id": lid}).status_code, 404)
        self.assertEqual(bob("GET", f"{P}/discovery/library").json()["data"]["total"], 0)
        self.assertEqual(bob("GET", f"{P}/discovery/searches").json()["data"]["items"], [])
        self.assertEqual(bob("POST", f"{P}/discovery/search", {"query": "x", "workspace_id": wid}).status_code, 404)
        self.assertEqual(bob("DELETE", f"{P}/discovery/searches/{sid}").status_code, 404)
        self.assertEqual(len(alice("GET", f"{P}/discovery/searches").json()["data"]["items"]), 1)


if __name__ == "__main__":
    unittest.main()
