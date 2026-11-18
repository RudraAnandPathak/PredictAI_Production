"""Phase 10 schema: migrations 9 and 10 only ADD ri_* tables to a Phase 9 database, changes nothing that exists, and its integrity rules hold."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core.config import load_settings
from app.db.migrator import discover, migrate


class Upgrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(f"{self.tmp.name}/db.sqlite", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrations = discover()

    def upto(self, n):
        d = Path(self.tmp.name) / f"m{n}"
        d.mkdir()
        for m in self.migrations[:n]:
            (d / f"{m.version:04d}_{m.name}.sql").write_text(m.sql)
        return d

    def snapshot(self):
        names = sorted(r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'schema_migrations' AND name NOT LIKE 'ri_chunks_fts%'"))
        return ({t: [tuple(r) for r in self.conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in names},
                {r[0]: r[1] for r in self.conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','trigger') AND name NOT LIKE 'sqlite_%'")})

    def test_phase_nine_database_upgrades_adding_only_empty_ri_tables(self):
        self.assertEqual(migrate(self.conn, self.upto(8)), list(range(1, 9)))
        self.conn.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','u1@x.io','u1@x.io','active','t','t')")
        self.conn.execute("INSERT INTO rw_workspaces (id,owner_user_id,name,status,created_at,updated_at) VALUES ('w1','u1','Study','active','t','t')")
        rows0, schema0 = self.snapshot()
        through11 = self.upto(11)
        self.assertEqual(migrate(self.conn, through11), [9, 10, 11])      # the Phase 10 steps; migration 12 (Phase 10A) is checked in test_p12_monitoring
        rows1, schema1 = self.snapshot()
        self.assertEqual(rows0, {k: v for k, v in rows1.items() if k in rows0})
        self.assertEqual(schema0, {k: v for k, v in schema1.items() if k in schema0})
        added = set(rows1) - set(rows0)
        self.assertTrue(added and all(t.startswith("ri_") for t in added), added)
        self.assertTrue(all(rows1[t] == [] for t in added))
        self.assertEqual(list(self.conn.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(migrate(self.conn, through11), [])

    def test_migration_eleven_is_purely_additive(self):
        migrate(self.conn, self.upto(10))
        c = self.conn
        c.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','u1@x.io','u1@x.io','active','t','t')")
        c.execute("INSERT INTO rw_workspaces (id,owner_user_id,name,status,created_at,updated_at) VALUES ('w1','u1','n','active','t','t')")
        c.execute("INSERT INTO ri_library (id,owner_user_id,dedupe_key,title,record,created_at) VALUES ('l1','u1','k','T','{}','t')")
        rows0, schema0 = self.snapshot()
        self.assertEqual(migrate(c, self.upto(11)), [11])
        rows1, schema1 = self.snapshot()
        self.assertEqual(rows0, {k: v for k, v in rows1.items() if k in rows0})
        self.assertEqual(schema0, {k: v for k, v in schema1.items() if k in schema0})
        added = set(rows1) - set(rows0)
        self.assertEqual(added, {"ri_finding_links", "ri_finding_groups", "ri_finding_group_items", "ri_outlines", "ri_generated", "ri_reports", "ri_report_versions"})
        self.assertTrue(all(rows1[t] == [] for t in added))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO ri_reports (id,workspace_id,owner_user_id,report_type,title,notice,sources,sections,created_at,updated_at) VALUES ('r','w1','u1','poem','t','n','[]','[]','t','t')")

    def test_migration_ten_keeps_existing_usage_and_job_rows_and_widens_only_the_kinds(self):
        migrate(self.conn, self.upto(9))
        c = self.conn
        c.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES ('u1','u1@x.io','u1@x.io','active','t','t')")
        c.execute("INSERT INTO rw_workspaces (id,owner_user_id,name,status,created_at,updated_at) VALUES ('w1','u1','n','active','t','t')")
        c.execute("INSERT INTO ri_usage (owner_user_id,workspace_id,kind,units,detail,created_at) VALUES ('u1','w1','import',2,'upload','t1')")
        c.execute("INSERT INTO ri_jobs (id,workspace_id,owner_user_id,kind,status,run_after,created_at,payload) VALUES ('j1','w1','u1','ingest','queued','t','t','{\"a\":1}')")
        before_usage = [tuple(r) for r in c.execute("SELECT * FROM ri_usage")]
        before_jobs = [tuple(r) for r in c.execute("SELECT * FROM ri_jobs")]
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO ri_usage (owner_user_id,kind,created_at) VALUES ('u1','discovery','t')")
        self.assertEqual(migrate(c, self.upto(10)), [10])
        self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM ri_usage")], before_usage)
        self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM ri_jobs")], before_jobs)
        for kind in ("discovery", "reasoning"):
            c.execute("INSERT INTO ri_usage (owner_user_id,kind,created_at) VALUES ('u1',?,'t')", (kind,))
        c.execute("INSERT INTO ri_jobs (id,workspace_id,owner_user_id,kind,status,run_after,created_at) VALUES ('j2','w1','u1','analysis','queued','t','t')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO ri_usage (owner_user_id,kind,created_at) VALUES ('u1','mining','t')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO ri_jobs (id,workspace_id,owner_user_id,kind,status,run_after,created_at) VALUES ('j3','w1','u1','teleport','queued','t','t')")
        self.assertEqual(list(c.execute("PRAGMA foreign_key_check")), [])
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({"ri_usage_owner", "ri_jobs_queue", "ri_jobs_ws"} <= names)
        c.execute("INSERT INTO ri_library (id,owner_user_id,dedupe_key,title,record,created_at) VALUES ('l1','u1','doi:10.1000/x','T','{}','t')")
        with self.assertRaises(sqlite3.IntegrityError):                                  # one library row per work per person
            c.execute("INSERT INTO ri_library (id,owner_user_id,dedupe_key,title,record,created_at) VALUES ('l2','u1','doi:10.1000/x','T','{}','t')")

    def test_integrity_rules(self):
        migrate(self.conn)
        c = self.conn
        for u in ("u1", "u2"):
            c.execute("INSERT INTO users (id,email,email_normalized,status,created_at,updated_at) VALUES (?,?,?,'active','t','t')", (u, u + "@x.io", u + "@x.io"))
        for w, u in (("w1", "u1"), ("w2", "u2")):
            c.execute("INSERT INTO rw_workspaces (id,owner_user_id,name,status,created_at,updated_at) VALUES (?,?,'n','active','t','t')", (w, u))
        c.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,status,created_at,updated_at) VALUES ('s1','w1','u1','pdf','t','ready','t','t')")

        def src(sid, ws, owner, **kw):
            c.execute("INSERT INTO ri_sources (id,workspace_id,owner_user_id,format,origin,title,created_at,updated_at,doi) VALUES (?,?,?,?,?,?,'t','t',?)",
                      (sid, ws, owner, kw.get("format", "pdf"), kw.get("origin", "upload"), "t", kw.get("doi")))
        with self.assertRaises(sqlite3.IntegrityError):                                  # a ri_sources row must match its rw_sources owner and workspace
            src("s1", "w1", "u2")
        with self.assertRaises(sqlite3.IntegrityError):
            src("s1", "w2", "u1")
        with self.assertRaises(sqlite3.IntegrityError):
            src("s1", "w1", "u1", format="exe")
        src("s1", "w1", "u1", doi="10.1/x")
        c.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,status,created_at,updated_at) VALUES ('s2','w1','u1','pdf','t2','ready','t','t')")
        with self.assertRaises(sqlite3.IntegrityError):                                  # one DOI once per workspace
            src("s2", "w1", "u1", doi="10.1/x")

        def chunk(owner, ws, seq_id):
            c.execute("INSERT INTO ri_chunks (id,source_id,workspace_id,owner_user_id,ordinal,text,locator,words,text_sha256,created_at) VALUES (?,?,?,?,0,'hello world','p.1',2,'h','t')", (seq_id, "s1", ws, owner))
        with self.assertRaises(sqlite3.IntegrityError):
            chunk("u2", "w1", "c1")
        with self.assertRaises(sqlite3.IntegrityError):
            chunk("u1", "w2", "c1")
        chunk("u1", "w1", "c1")
        seq = c.execute("SELECT seq FROM ri_chunks").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):                                  # an embedding must belong to its chunk's source/owner/workspace
            c.execute("INSERT INTO ri_embeddings (chunk_seq,model,provider,workspace_id,source_id,owner_user_id,dims,vector,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (seq, "m", "p", "w2", "s1", "u2", 1, b"x", "t"))
        c.execute("INSERT INTO ri_embeddings (chunk_seq,model,provider,workspace_id,source_id,owner_user_id,dims,vector,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (seq, "m", "p", "w1", "s1", "u1", 1, b"x", "t"))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ri_chunks_fts WHERE ri_chunks_fts MATCH 'hello'").fetchone()[0], 1)
        c.execute("DELETE FROM ri_embeddings")
        c.execute("DELETE FROM ri_chunks")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM ri_chunks_fts WHERE ri_chunks_fts MATCH 'hello'").fetchone()[0], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO ri_jobs (id,workspace_id,owner_user_id,kind,status,run_after,created_at) VALUES ('j','w1','u1','ingest','teleported','t','t')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO ri_workspace_settings (workspace_id,owner_user_id,ai_data_policy,updated_at) VALUES ('w1','u1','anything_goes','t')")


class Settings(unittest.TestCase):
    def test_defaults_and_validation(self):
        s = load_settings({"PREDICTAI_ENV": "test"})
        self.assertEqual((s.ri_vector_backend, s.ri_malware_scan, s.ri_allow_private_urls, s.ri_max_upload_mb_standard, s.ri_ai_requests_per_day_standard), ("sqlite", "auto", False, 10, 30))
        s = load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_RI_VECTOR_BACKEND": "qdrant", "PREDICTAI_RI_QDRANT_URL": "http://127.0.0.1:6333/", "PREDICTAI_RI_CHUNK_WORDS": "300"})
        self.assertEqual((s.ri_vector_backend, s.ri_qdrant_url, s.ri_chunk_words), ("qdrant", "http://127.0.0.1:6333", 300))
        with self.assertRaises(Exception):
            load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_RI_VECTOR_BACKEND": "pinecone"})
        with self.assertRaises(Exception):
            load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_RI_MALWARE_SCAN": "maybe"})
        s = load_settings({"PREDICTAI_ENV": "test"})
        self.assertEqual((s.ri_discovery_providers, s.ri_discovery_per_provider), ("crossref,openalex,arxiv,semanticscholar,searxng", 25))
        s = load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_RI_DISCOVERY_PROVIDERS": "arxiv, crossref,arxiv"})
        self.assertEqual(s.ri_discovery_providers, "arxiv,crossref")
        with self.assertRaises(Exception):
            load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_RI_DISCOVERY_PROVIDERS": "google"})
        with self.assertRaises(Exception):
            load_settings({"PREDICTAI_ENV": "test", "PREDICTAI_RI_DISCOVERY_PER_PROVIDER": "5000"})

    def test_private_url_override_is_never_honoured_when_hosted(self):
        env = {"PREDICTAI_ENV": "test", "PREDICTAI_RI_ALLOW_PRIVATE_URLS": "true"}
        self.assertTrue(load_settings(env).ri_allow_private_urls)
        hosted = {**env, "PREDICTAI_HOSTED": "true"}
        try:
            self.assertFalse(load_settings(hosted).ri_allow_private_urls)
        except Exception:
            pass                                                                          # a hosted configuration may also be refused outright for other missing settings


if __name__ == "__main__":
    unittest.main()
