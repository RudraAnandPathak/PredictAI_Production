"""Conversations hub over HTTP: one list/chat over the dataset assistant and the research assistant. Session + CSRF, isolation, create/ask/rename/export/delete,
search, cited research answers, and that conversations started through the older per-module endpoints appear too."""
import os
import tempfile
import unittest

try:
    from test_analytics_api import AnalyticsApiBase
    import test_identity_api as base
except unittest.SkipTest:
    raise

import analytics_support as sup
from app.modules.research_intelligence.workers.worker import Worker
from p10_support import ARTICLE

C, P = "/conversations", "/research-intelligence"


class Hub(AnalyticsApiBase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = base.Api(upload_chunk_max_bytes=1 << 20, upload_max_bytes=8 << 20, ri_root=os.path.join(self.tmp.name, "ri"))
        self.addCleanup(self.api.close)
        self.df = sup.synth(n=400)
        self.data = sup.csv_of(self.df)
        self.alice = self.user("alice@example.com")
        self.bob = self.user("bob@example.com")
        self.pid = self.alice("POST", "/projects", {"name": "P"}).json()["data"]["id"]
        self.asset = self.upload(self.alice, self.pid, "customers.csv", self.data)

    def drain(self):
        h = self.api.h
        w = Worker(h.db, h.settings, h.clock, h.hasher, h.outbox, poll=0.01)
        while w.tick() is not None:
            pass

    def research(self, call=None):
        call = call or self.alice
        wid = call("POST", f"{P}/workspaces", {"name": "Study"}).json()["data"]["id"]
        up = call("POST", f"{P}/workspaces/{wid}/sources/upload", params={"filename": "heat study.txt"}, content=ARTICLE.encode(), headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(up.status_code, 201, up.text)
        self.drain()
        return wid

    def data_conv(self, title=None):
        r = self.alice("POST", C, {"kind": "data", "target_id": self.asset, **({"title": title} if title else {})})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id"]


class Access(Hub):
    def test_every_route_needs_a_session_and_csrf(self):
        for method, path in (("GET", C), ("GET", f"{C}/targets"), ("POST", C), ("GET", f"{C}/data/x"), ("POST", f"{C}/data/x/messages"), ("PATCH", f"{C}/data/x"),
                             ("DELETE", f"{C}/data/x"), ("GET", f"{C}/data/x/export")):
            self.assertEqual(self.error_code(self.api.call(method, path, {} if method != "GET" else None), 401), "UNAUTHENTICATED", f"{method} {path}")
        self.api.signed_in()
        self.assertEqual(self.error_code(self.api.call("POST", C, {"kind": "data", "target_id": "x"}, csrf=False), 403), "CSRF_FAILED")

    def test_another_person_cannot_see_use_or_create_in_my_conversations(self):
        cid = self.data_conv("Spend questions")
        wid = self.research()
        rid = self.alice("POST", C, {"kind": "research", "target_id": wid}).json()["data"]["id"]
        self.assertEqual(self.bob("GET", C).json()["data"]["items"], [])
        self.assertEqual(self.bob("GET", f"{C}/targets").json()["data"], {"datasets": [], "workspaces": []})
        for kind, i in (("data", cid), ("research", rid)):
            for method, path, body in (("GET", f"{C}/{kind}/{i}", None), ("POST", f"{C}/{kind}/{i}/messages", {"text": "hello"}), ("PATCH", f"{C}/{kind}/{i}", {"title": "mine"}),
                                       ("DELETE", f"{C}/{kind}/{i}", None), ("GET", f"{C}/{kind}/{i}/export", None)):
                r = self.bob(method, path, body)
                self.assertEqual(self.error_code(r, 404), "NOT_FOUND", f"{method} {path}")
        self.assertEqual(self.error_code(self.bob("POST", C, {"kind": "data", "target_id": self.asset}), 404), "NOT_FOUND")
        self.assertEqual(self.error_code(self.bob("POST", C, {"kind": "research", "target_id": wid}), 404), "NOT_FOUND")
        self.assertEqual(self.alice("GET", f"{C}/data/{cid}").json()["data"]["title"], "Spend questions")    # untouched

    def test_unknown_kind_and_bad_input_are_rejected_plainly(self):
        self.assertEqual(self.alice("GET", C, params={"kind": "other"}).status_code, 422)
        self.assertEqual(self.alice("GET", f"{C}/other/x").status_code, 422)
        self.assertEqual(self.alice("POST", C, {"kind": "nope", "target_id": "x"}).status_code, 422)
        cid = self.data_conv()
        self.assertEqual(self.alice("PATCH", f"{C}/data/{cid}", {"title": "   "}).status_code, 422)
        self.assertEqual(self.alice("POST", f"{C}/data/{cid}/messages", {"text": "   "}).status_code, 422)
        self.assertEqual(self.alice("GET", C, params={"limit": "abc"}).status_code, 422)


class DataConversations(Hub):
    def test_start_ask_rename_export_and_delete(self):
        t = self.alice("GET", f"{C}/targets").json()["data"]
        self.assertEqual([d["id"] for d in t["datasets"]], [self.asset])
        cid = self.data_conv()
        self.assertEqual(self.alice("GET", f"{C}/data/{cid}").json()["data"]["messages"], [])
        sent = self.alice("POST", f"{C}/data/{cid}/messages", {"text": "What is the average spend?"})
        self.assertEqual(sent.status_code, 200, sent.text)
        body = sent.json()["data"]
        self.assertEqual(body["result"]["status"], "answered")
        self.assertEqual(body["result"]["conversation_id"], cid)
        msgs = body["conversation"]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertTrue(msgs[1]["run_id"])                                   # the answer points at the computed, stored run
        listed = self.alice("GET", C).json()["data"]["items"]
        self.assertEqual([(i["kind"], i["id"], i["messages"], i["target"]) for i in listed], [("data", cid, 2, "customers.csv")])
        self.assertTrue(listed[0]["preview"])
        ren = self.alice("PATCH", f"{C}/data/{cid}", {"title": "  Average\x00 spend \n check "})
        self.assertEqual(ren.json()["data"]["title"], "Average spend check")
        exp = self.alice("GET", f"{C}/data/{cid}/export")
        self.assertEqual(exp.status_code, 200)
        self.assertIn("attachment", exp.headers["content-disposition"])
        self.assertIn("text/markdown", exp.headers["content-type"])
        self.assertIn("# Average spend check", exp.text)
        self.assertIn("What is the average spend?", exp.text)
        self.assertEqual(self.alice("DELETE", f"{C}/data/{cid}").json()["data"]["deleted"], True)
        self.assertEqual(self.alice("GET", f"{C}/data/{cid}").status_code, 404)
        self.assertEqual(self.alice("GET", C).json()["data"]["items"], [])
        self.assertEqual(self.alice("GET", f"/assets/{self.asset}/assistant/conversations").json()["data"]["items"], [])
        self.assertEqual(self.alice("GET", f"/assets/{self.asset}/insights").status_code, 200)          # the dataset itself is untouched

    def test_a_conversation_started_the_old_way_shows_up_and_can_be_continued(self):
        old = self.alice("POST", f"/assets/{self.asset}/assistant/ask", {"question": "how many rows"}).json()["data"]
        listed = self.alice("GET", C).json()["data"]["items"]
        self.assertEqual([i["id"] for i in listed], [old["conversation_id"]])
        more = self.alice("POST", f"{C}/data/{old['conversation_id']}/messages", {"text": "What is the average spend?"})
        self.assertEqual(more.status_code, 200, more.text)
        self.assertEqual(len(more.json()["data"]["conversation"]["messages"]), 4)

    def test_deleting_the_dataset_removes_its_conversations_from_the_list(self):
        self.data_conv()
        self.assertEqual(self.alice("DELETE", f"/assets/{self.asset}").status_code, 200)
        self.assertEqual(self.alice("GET", C).json()["data"]["items"], [])


class ResearchConversations(Hub):
    def test_answers_carry_verified_citations_and_the_export_lists_the_sources(self):
        wid = self.research()
        t = self.alice("GET", f"{C}/targets").json()["data"]
        self.assertEqual([w["id"] for w in t["workspaces"]], [wid])
        made = self.alice("POST", C, {"kind": "research", "target_id": wid, "title": "Tree cooling"})
        self.assertEqual(made.status_code, 201, made.text)
        cid = made.json()["data"]["id"]
        sent = self.alice("POST", f"{C}/research/{cid}/messages", {"text": "How much do trees lower surface temperature?"})
        self.assertEqual(sent.status_code, 200, sent.text)
        msgs = sent.json()["data"]["conversation"]["messages"]
        answer = msgs[-1]
        self.assertEqual(answer["role"], "assistant")
        self.assertEqual(answer["answer"]["mode"], "extractive")
        self.assertTrue(answer["citations"] and all(c["verified"] for c in answer["citations"]))
        exp = self.alice("GET", f"{C}/research/{cid}/export").text
        self.assertIn("## Sources cited", exp)
        self.assertIn(answer["citations"][0]["quote"][:30], exp)
        listed = self.alice("GET", C).json()["data"]["items"]
        self.assertEqual([(i["kind"], i["title"], i["target"]) for i in listed], [("research", "Tree cooling", "Study")])
        self.assertEqual(self.alice("DELETE", f"{C}/research/{cid}").status_code, 200)
        self.assertEqual(self.alice("GET", f"{C}/research/{cid}").status_code, 404)

    def test_deleting_the_workspace_removes_its_conversations_from_the_list(self):
        wid = self.research()
        self.alice("POST", C, {"kind": "research", "target_id": wid})
        self.assertEqual(self.alice("DELETE", f"{P}/workspaces/{wid}").status_code, 200)
        self.assertEqual(self.alice("GET", C).json()["data"]["items"], [])


class Search(Hub):
    def test_list_merges_both_kinds_newest_first_and_search_reads_titles_and_messages(self):
        wid = self.research()
        d = self.data_conv("Spend questions")
        self.alice("POST", f"{C}/data/{d}/messages", {"text": "What is the average spend?"})
        r = self.alice("POST", C, {"kind": "research", "target_id": wid, "title": "Tree cooling"}).json()["data"]["id"]
        self.alice("POST", f"{C}/research/{r}/messages", {"text": "How much do trees lower surface temperature?"})
        everything = self.alice("GET", C).json()["data"]["items"]
        self.assertEqual({i["kind"] for i in everything}, {"data", "research"})
        stamps = [i["updated_at"] for i in everything]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertEqual([i["id"] for i in self.alice("GET", C, params={"kind": "data"}).json()["data"]["items"]], [d])
        self.assertEqual([i["id"] for i in self.alice("GET", C, params={"q": "tree cooling"}).json()["data"]["items"]], [r])        # title
        self.assertEqual([i["id"] for i in self.alice("GET", C, params={"q": "average spend"}).json()["data"]["items"]], [d])         # message text
        self.assertEqual(self.alice("GET", C, params={"q": "zebra"}).json()["data"]["items"], [])

    def test_search_text_is_never_a_wildcard(self):
        d = self.data_conv("Plain title")
        for q in ("%", "_", "\\", "100%"):
            self.assertEqual(self.alice("GET", C, params={"q": q}).json()["data"]["items"], [], q)
        self.assertEqual([i["id"] for i in self.alice("GET", C, params={"q": "Plain"}).json()["data"]["items"]], [d])


if __name__ == "__main__":
    unittest.main()
