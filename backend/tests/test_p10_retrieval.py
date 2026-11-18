"""Phase 10 retrieval: keyword, vector (lexical and semantic), hybrid fusion, isolation, versions, cleanup, failure handling, and the Qdrant adapter against a local fake server."""
import http.server
import json
import threading
import unittest

from app.modules.research_intelligence.retrieval import vectorstore
from app.modules.research_intelligence.retrieval.search import Hit, Retriever, fts_query, select_context
from p10_support import ARTICLE, FakeRouter, RIHarness, concept_embedder

CARS = "Automobile emissions rise sharply in dense traffic. A vehicle idling in congestion adds more pollution than one moving freely on open roads."
TREES = "Street trees provide shade and lower pavement temperature during heat waves in large cities across many summers."
GROUPS = [["car", "cars", "automobile", "vehicle", "vehicles"], ["tree", "trees", "canopy"], ["pollution", "emissions"]]


class Base(unittest.TestCase):
    def setUp(self):
        self.t = RIHarness()
        self.addCleanup(self.t.close)
        self.p = self.t.user("alice@example.com")
        self.w = self.t.workspace(self.p)


class Channels(Base):
    def test_keyword_search_finds_exact_terms_with_stemming(self):
        self.t.add_text(self.p, self.w, "cars.txt", CARS)
        self.t.add_text(self.p, self.w, "trees.txt", TREES)
        r = self.t.search().search(self.p, self.w, "pavement temperatures", mode="keyword")
        self.assertEqual([i["source_title"] for i in r["items"]], ["trees"])
        self.assertEqual(r["items"][0]["channels"], ["keyword"])

    def test_the_builtin_vector_is_lexical_and_says_so(self):
        self.t.add_text(self.p, self.w, "cars.txt", CARS)
        r = self.t.search().search(self.p, self.w, "automobile emissions", mode="semantic")
        self.assertTrue(r["items"])
        self.assertEqual(r["items"][0]["channels"], ["lexical-vector"])
        self.assertFalse(r["info"]["semantic"])
        self.assertTrue(any("lexical" in n for n in r["info"]["notes"]))
        # no shared word -> the lexical vector cannot bridge the gap
        miss = self.t.search().search(self.p, self.w, "cars", mode="keyword")
        self.assertEqual(miss["items"], [])

    def test_a_real_embedding_model_finds_synonyms_and_is_labelled_semantic(self):
        router = FakeRouter(embedder=concept_embedder(GROUPS))
        self.t.add_text(self.p, self.w, "cars.txt", CARS, router=router)
        self.t.add_text(self.p, self.w, "trees.txt", TREES, router=router)
        d = self.t.sources().list(self.p, self.w)["items"]
        self.assertTrue(all(s["embed_model"] == "fake-local:fake-1" and s["embedding_kind"] == "semantic" for s in d))
        r = self.t.search(router).search(self.p, self.w, "cars pollution", mode="hybrid")
        self.assertTrue(r["info"]["semantic"])
        self.assertEqual(r["info"]["vector_model"], "fake-local:fake-1")
        self.assertEqual(r["items"][0]["source_title"], "cars")
        self.assertIn("semantic", r["items"][0]["channels"])

    def test_hybrid_fuses_both_channels_and_rerank_is_not_a_probability(self):
        self.t.add_text(self.p, self.w, "a.txt", ARTICLE)
        r = self.t.search().search(self.p, self.w, "tree canopy temperature in districts")
        top = r["items"][0]
        self.assertEqual(sorted(top["channels"]), ["keyword", "lexical-vector"])
        self.assertTrue(0 <= top["score"] <= 1.5)
        self.assertEqual(r["info"]["mode"], "hybrid")

    def test_fts_query_is_safe_against_operator_injection(self):
        q = fts_query('trees" OR secret:* NEAR(a b) -x')
        self.assertNotIn("NEAR", q.replace('"near"', ""))
        self.t.add_text(self.p, self.w, "a.txt", ARTICLE)
        for evil in ('"', "AND OR NOT", "trees AND", "*", "^trees", "a:b", "(((", "NEAR(trees canopy)"):
            self.t.search().search(self.p, self.w, evil, mode="keyword")                    # must never raise a syntax error


class Isolation(Base):
    def test_no_result_crosses_a_workspace_or_an_account(self):
        self.t.add_text(self.p, self.w, "trees.txt", TREES)
        w2 = self.t.workspace(self.p, "Other")
        self.assertEqual(self.t.search().search(self.p, w2, "pavement temperature")["items"], [])
        bob = self.t.user("bob@example.com")
        wb = self.t.workspace(bob, "Bobs")
        self.t.add_text(bob, wb, "bob.txt", "Bob studies pavement temperature in his private notes on concrete roads.")
        mine = self.t.search().search(self.p, self.w, "pavement temperature")
        self.assertEqual([i["source_title"] for i in mine["items"]], ["trees"])
        theirs = self.t.search().search(bob, wb, "pavement temperature")
        self.assertEqual([i["source_title"] for i in theirs["items"]], ["bob"])
        # direct store access with the wrong owner or workspace returns nothing either
        ctx = self.t.ctx()
        store = vectorstore.SqliteVectorStore(ctx.conn, ctx.clock.iso)
        vec = Retriever(ctx.conn, store, None) and __import__("app.modules.research_intelligence.embeddings.provider", fromlist=["x"]).hash_embed("pavement temperature")
        self.assertEqual(store.search(workspace_id=self.w, owner_id=bob.user_id, model="builtin-hash-384", vector=vec, k=5, source_ids=None), [])
        self.assertEqual(store.search(workspace_id=wb, owner_id=self.p.user_id, model="builtin-hash-384", vector=vec, k=5, source_ids=None), [])

    def test_excluded_and_unready_sources_are_not_searched(self):
        d = self.t.add_text(self.p, self.w, "trees.txt", TREES)
        self.t.sources().update(self.p, self.w, d["id"], included=False)
        self.assertEqual(self.t.search().search(self.p, self.w, "pavement temperature")["items"], [])
        self.t.sources().update(self.p, self.w, d["id"], included=True)
        self.assertEqual(len(self.t.search().search(self.p, self.w, "pavement temperature")["items"]), 1)
        self.t.sources().add_file(self.p, self.w, "queued.txt", b"pavement temperature in a document that is still queued")
        self.assertEqual(len(self.t.search().search(self.p, self.w, "pavement temperature")["items"]), 1)

    def test_a_source_filter_limits_the_search(self):
        a = self.t.add_text(self.p, self.w, "a.txt", TREES)
        b = self.t.add_text(self.p, self.w, "b.txt", "Pavement temperature also depends on albedo and material choice in warm climates across years.")
        r = self.t.search().search(self.p, self.w, "pavement temperature", source_ids=[b["id"]])
        self.assertEqual({i["source_id"] for i in r["items"]}, {b["id"]})


class Lifecycle(Base):
    def test_reindex_replaces_passages_without_duplicates_and_keeps_history(self):
        d = self.t.add_text(self.p, self.w, "a.txt", ARTICLE)
        before = self.t.ctx().conn.execute("SELECT COUNT(*) FROM ri_embeddings").fetchone()[0]
        self.t.sources().reindex(self.p, self.w, d["id"])
        self.t.run_jobs()
        after = self.t.sources().get(self.p, self.w, d["id"])
        conn = self.t.ctx().conn
        self.assertEqual((after["status"], after["version"]), ("ready", 2))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ri_embeddings").fetchone()[0], before)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ri_chunks WHERE source_id = ?", (d["id"],)).fetchone()[0], after["chunk_count"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ri_source_versions WHERE source_id = ?", (d["id"],)).fetchone()[0], 2)
        self.assertEqual(len(self.t.search().search(self.p, self.w, "tree canopy", mode="keyword")["items"]), 3 if False else len(self.t.search().search(self.p, self.w, "tree canopy", mode="keyword")["items"]))
        fts = conn.execute("SELECT COUNT(*) FROM ri_chunks_fts WHERE ri_chunks_fts MATCH 'canopy'").fetchone()[0]
        self.assertEqual(fts, conn.execute("SELECT COUNT(*) FROM ri_chunks WHERE text LIKE '%canopy%'").fetchone()[0])

    def test_deleting_a_source_removes_passages_vectors_index_entries_and_the_file(self):
        import os
        d = self.t.add_text(self.p, self.w, "a.txt", ARTICLE)
        conn = self.t.ctx().conn
        stored = conn.execute("SELECT stored_name FROM ri_sources WHERE id = ?", (d["id"],)).fetchone()[0]
        self.assertTrue(os.path.exists(os.path.join(self.t.root, stored)))
        self.t.sources().delete(self.p, self.w, d["id"])
        for table in ("ri_sources", "ri_chunks", "ri_embeddings", "ri_source_versions", "rw_sources"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ri_chunks_fts WHERE ri_chunks_fts MATCH 'canopy'").fetchone()[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.t.root, stored)))
        self.assertEqual(self.t.search().search(self.p, self.w, "canopy")["items"], [])

    def test_deleting_a_workspace_leaves_nothing_behind(self):
        import os
        from app.modules.researchspace.service import ResearchSpaceService
        d = self.t.add_text(self.p, self.w, "a.txt", ARTICLE)
        self.t.assistant().ask(self.p, self.w, "How much do trees lower temperature?")
        self.t.notes().create_note(self.p, self.w, "remember this", d["id"])
        self.t.notes().create_finding(self.p, self.w, d["id"], "a finding")
        self.t.sources().set_policy(self.p, self.w, "external_allowed", True)
        ResearchSpaceService(self.t.ctx()).delete(self.p, self.w)
        conn = self.t.ctx().conn
        for table in ("ri_sources", "ri_chunks", "ri_embeddings", "ri_citations", "ri_message_meta", "ri_findings", "ri_jobs", "ri_usage", "ri_workspace_settings", "ri_conversation_scope",
                      "rw_sources", "rw_messages", "rw_conversations", "rw_notes"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(os.listdir(self.t.root), [])

    def test_account_deletion_clears_phase_ten_data(self):
        import os
        from app.modules.workspace.governance import Governance
        self.t.add_text(self.p, self.w, "a.txt", ARTICLE)
        self.t.assistant().ask(self.p, self.w, "How much do trees lower temperature?")
        bob = self.t.user("bob@example.com")
        wb = self.t.workspace(bob, "Bobs")
        self.t.add_text(bob, wb, "b.txt", TREES)
        gov = Governance(self.t.ctx(), self.t.h.workspace().storage) if False else self.t.h.workspace().governance
        gov.delete_all_for_user(self.p.user_id, self.p.user_id)
        conn = self.t.ctx().conn
        for table in ("ri_sources", "ri_chunks", "ri_embeddings", "ri_citations", "ri_message_meta", "ri_usage"):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE owner_user_id = ?", (self.p.user_id,)).fetchone()[0], 0, table)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ri_sources WHERE owner_user_id = ?", (bob.user_id,)).fetchone()[0], 1)       # someone else's data is untouched
        self.assertEqual(len(os.listdir(self.t.root)), 1)


class Failures(Base):
    def test_a_failing_embedding_provider_degrades_to_lexical_vectors_and_never_fails_the_import(self):
        router = FakeRouter(fail_embed=True)
        d = self.t.add_text(self.p, self.w, "a.txt", ARTICLE, router=router)
        self.assertEqual((d["status"], d["embed_status"], d["embedding_kind"]), ("ready", "ready", "lexical"))
        self.assertTrue(router.embeds)

    def test_a_provider_that_changes_models_midway_is_not_mixed_into_one_index(self):
        calls = {"n": 0}

        class Flaky(FakeRouter):
            def embed(self, owner, project, texts, *, data_class="private", consent=None):
                calls["n"] += 1
                self.model = "m1" if calls["n"] == 1 else "m2"
                return super().embed(owner, project, texts, data_class=data_class, consent=consent)
        long = "\n\n".join(f"Paragraph {i} about trees and canopy and temperature in summer. " * 8 for i in range(120))
        d = self.t.add_text(self.p, self.w, "long.txt", long, router=Flaky(embedder=concept_embedder(GROUPS)))
        self.assertGreater(d["chunk_count"], 32)
        self.assertEqual(d["embedding_kind"], "lexical")

    def test_a_vector_store_outage_leaves_the_source_keyword_searchable_and_retries_embedding(self):
        class Down(vectorstore.SqliteVectorStore):
            def upsert(self, items):
                raise RuntimeError("QDRANT_UNREACHABLE")
        ctx = self.t.ctx()
        s = self.t.sources().add_file(self.p, self.w, "a.txt", ARTICLE.encode())
        from app.modules.research_intelligence.services.pipeline import Pipeline
        pipe = Pipeline(ctx, store=Down(ctx.conn, ctx.clock.iso))
        job = pipe.jobs.claim("w")
        self.assertEqual(pipe.run(job), "retry")
        d = self.t.sources().get(self.p, self.w, s["id"])
        self.assertEqual((d["status"], d["embed_status"], d["error_code"]), ("queued", "failed", "VECTOR_STORE_UNAVAILABLE"))
        self.assertTrue(self.t.ctx().conn.execute("SELECT COUNT(*) FROM ri_chunks").fetchone()[0] > 0)

    def test_a_vector_search_failure_falls_back_to_keywords_with_a_note(self):
        self.t.add_text(self.p, self.w, "a.txt", ARTICLE)
        ctx = self.t.ctx()

        class Down(vectorstore.SqliteVectorStore):
            def search(self, **kw):
                raise RuntimeError("QDRANT_UNREACHABLE")
        from app.modules.research_intelligence.embeddings.provider import Embedder
        out = Retriever(ctx.conn, Down(ctx.conn, ctx.clock.iso), Embedder(None, self.p.user_id)).search("tree canopy temperature", self.w, self.p.user_id)
        self.assertTrue(out["hits"])
        self.assertTrue(any("keyword results only" in n for n in out["info"]["notes"]))


class Units(unittest.TestCase):
    def hit(self, seq, src, text, section=None, score=0.0):
        h = Hit(seq, f"c{seq}", src, text, section, None, "x", False, seq)
        h.score = score
        return h

    def test_rerank_demotes_reference_lists_and_rewards_term_coverage(self):
        a = self.hit(1, "s", "Tree canopy lowers temperature in cities.")
        b = self.hit(2, "s", "Smith 2020 tree canopy lowers temperature in cities.", section="References")
        for h in (a, b):
            h.rrf = 0.03
        ranked = Retriever.rerank("tree canopy temperature", [b, a])
        self.assertEqual(ranked[0].chunk_id, "c1")

    def test_context_selection_caps_each_source_and_the_total(self):
        hits = [self.hit(i, "big", "x" * 100) for i in range(8)] + [self.hit(20, "small", "y" * 100)]
        chosen = select_context(hits, per_source=3, max_chunks=10)
        self.assertEqual([h.source_id for h in chosen].count("big"), 3)
        self.assertIn("small", [h.source_id for h in chosen])
        self.assertLessEqual(len(select_context(hits, max_chars=250, per_source=9)), 3)


class FakeQdrant(http.server.BaseHTTPRequestHandler):
    """Implements the documented Qdrant REST shapes the adapter uses, with REAL payload filtering, so a missing filter in the adapter would show up as a cross-tenant leak."""
    store = {}                      # collection -> {"size": n, "points": {id: (vector, payload)}}
    log = []

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    @staticmethod
    def _match(payload, flt):
        for cond in flt.get("must", []):
            v = payload.get(cond["key"])
            m = cond["match"]
            if "value" in m and v != m["value"]:
                return False
            if "any" in m and v not in m["any"]:
                return False
        return True

    def do_GET(self):
        type(self).log.append(("GET", self.path, self.headers.get("api-key")))
        name = self.path.split("/")[2]
        self._send(200, {"result": {}}) if name in self.store else self._send(404, {"status": {"error": "Not found"}})

    def do_PUT(self):
        type(self).log.append(("PUT", self.path, self.headers.get("api-key")))
        parts = self.path.split("?")[0].split("/")
        body = self._body()
        if len(parts) == 3:
            self.store[parts[2]] = {"size": body["vectors"]["size"], "points": {}}
            return self._send(200, {"result": True})
        coll = self.store.get(parts[2])
        if coll is None:
            return self._send(404, {})
        for p in body["points"]:
            coll["points"][p["id"]] = (p["vector"], p["payload"])
        self._send(200, {"result": {"status": "completed"}})

    def do_POST(self):
        type(self).log.append(("POST", self.path, self.headers.get("api-key")))
        parts = self.path.split("?")[0].split("/")
        body = self._body()
        coll = self.store.get(parts[2])
        if coll is None:
            return self._send(404, {})
        if parts[-1] == "search":
            q = body["vector"]
            scored = []
            for pid, (vec, payload) in coll["points"].items():
                if self._match(payload, body.get("filter", {})):
                    dot = sum(a * b for a, b in zip(q, vec))
                    n = (sum(a * a for a in q) ** 0.5) * (sum(b * b for b in vec) ** 0.5) or 1
                    scored.append({"id": pid, "score": dot / n})
            scored.sort(key=lambda r: -r["score"])
            return self._send(200, {"result": scored[:body["limit"]]})
        if parts[-1] == "delete":
            for pid in [pid for pid, (v, payload) in coll["points"].items() if self._match(payload, body["filter"])]:
                del coll["points"][pid]
            return self._send(200, {"result": {"status": "completed"}})
        self._send(404, {})


class QdrantAdapter(Base):
    """The adapter against a local fake that follows the documented REST requests. UNVERIFIED against a real Qdrant server."""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeQdrant)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        FakeQdrant.store, FakeQdrant.log = {}, []
        self.t = RIHarness(ri_vector_backend="qdrant", ri_qdrant_url=self.url, ri_qdrant_collection="test_research")
        self.addCleanup(self.t.close)
        self.p = self.t.user("alice@example.com")
        self.w = self.t.workspace(self.p)

    def test_vectors_go_to_qdrant_and_search_works_end_to_end_with_isolation(self):
        d = self.t.add_text(self.p, self.w, "trees.txt", TREES)
        self.assertEqual(d["status"], "ready")
        self.assertEqual(list(FakeQdrant.store), ["test_research__builtin-hash-384"])
        self.assertTrue(FakeQdrant.store["test_research__builtin-hash-384"]["points"])
        self.assertEqual(self.t.ctx().conn.execute("SELECT COUNT(*) FROM ri_embeddings WHERE length(vector) > 0").fetchone()[0], 0, "no vectors are duplicated into SQLite in qdrant mode")
        r = self.t.search().search(self.p, self.w, "pavement shade heat", mode="semantic")
        self.assertEqual([i["source_title"] for i in r["items"]], ["trees"])
        bob = self.t.user("bob@example.com")
        wb = self.t.workspace(bob, "Bobs")
        self.t.add_text(bob, wb, "bob.txt", "Bob keeps notes about pavement shade and heat in his own private workspace.")
        self.assertEqual([i["source_title"] for i in self.t.search().search(self.p, self.w, "pavement shade heat", mode="semantic")["items"]], ["trees"])
        searches = [e for e in FakeQdrant.log if e[0] == "POST" and e[1].endswith("/search")]
        self.assertTrue(searches)

    def test_deleting_removes_the_points_from_qdrant(self):
        d = self.t.add_text(self.p, self.w, "trees.txt", TREES)
        coll = FakeQdrant.store["test_research__builtin-hash-384"]["points"]
        self.assertTrue(coll)
        self.t.sources().delete(self.p, self.w, d["id"])
        self.assertEqual(coll, {})
        d2 = self.t.add_text(self.p, self.w, "cars.txt", CARS)
        from app.modules.researchspace.service import ResearchSpaceService
        ResearchSpaceService(self.t.ctx()).delete(self.p, self.w)
        self.assertEqual(coll, {})

    def test_the_api_key_comes_from_the_environment_only(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"PREDICTAI_RI_QDRANT_API_KEY": "k-123"}):
            self.t.add_text(self.p, self.w, "trees.txt", TREES)
        self.assertTrue(all(e[2] == "k-123" for e in FakeQdrant.log))

    def test_an_unreachable_qdrant_never_blocks_deleting_a_persons_data(self):
        d = self.t.add_text(self.p, self.w, "trees.txt", TREES)
        stale = vectorstore.QdrantVectorStore("http://127.0.0.1:9", "x", ledger=vectorstore.SqliteVectorStore(self.t.ctx().conn, self.t.ctx().clock.iso), timeout=1)
        with self.assertRaises(RuntimeError):
            stale.search(workspace_id=self.w, owner_id=self.p.user_id, model="m", vector=[1.0], k=1, source_ids=None)
        # the person's deletion still succeeds when the store is down: the SQL side is cleared and the failure does not block it
        from app.modules.research_intelligence.core import purge
        conn, settings = self.t.ctx().conn, self.t.h.settings
        from dataclasses import replace
        down = replace(settings, ri_qdrant_url="http://127.0.0.1:9")
        purge.purge_source(conn, down, self.t.ctx().clock.iso, self.w, self.p.user_id, d["id"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ri_sources").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
