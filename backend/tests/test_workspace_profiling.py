"""Profiling: exact facts checked against independent calculations, labelled heuristics, reproducibility, isolation."""
import json
import statistics
import unittest

import fixtures as fx
from test_workspace_extraction import Base, HAS_PYPDF
from test_workspace_uploads import code_of


def csv_bytes(header, rows):
    return (",".join(header) + "\n" + "".join(",".join(map(str, r)) + "\n" for r in rows)).encode()


class ProfilingTests(Base):
    def profile(self, asset, **kw):
        return self.ws.profiles.generate(self.alice, asset["id"], **kw)

    def col(self, result, name):
        return next(c for c in result["profile"]["columns"] if c["name"] == name)

    def test_numeric_statistics_match_an_independent_calculation(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]
        a = self.ok("n.csv", csv_bytes(["v", "label"], [(v, "x") for v in values]))
        r = self.profile(a)
        n = self.col(r, "v")["numeric"]
        self.assertEqual((n["min"], n["max"], n["count"]), (1, 100, 10))
        self.assertAlmostEqual(n["mean"], statistics.mean(values), places=5)
        self.assertAlmostEqual(n["median"], statistics.median(values), places=5)
        self.assertAlmostEqual(n["std"], statistics.stdev(values), places=4)
        q = statistics.quantiles(values, n=4, method="inclusive")
        self.assertAlmostEqual(n["q1"], q[0], places=5)
        self.assertAlmostEqual(n["q3"], q[2], places=5)
        self.assertEqual(sum(b["count"] for b in n["histogram"]["bins"]), 10)
        self.assertEqual(n["histogram"]["bins"][0]["count"], 9)  # 1..9 land in the first bin, 100 in the last
        self.assertEqual(n["outliers"]["count"], 1)
        self.assertEqual(n["outliers"]["basis"], "heuristic")
        self.assertIn("1.5", n["outliers"]["rule"])
        self.assertEqual(n["basis"], "fact")

    def test_dataset_facts_duplicates_missing_and_flags(self):
        rows = [(1, "a", ""), (2, "a", "x"), (2, "a", "x"), (3, "a", ""), (4, "a", "")]
        a = self.ok("d.csv", csv_bytes(["id", "k", "note"], rows))
        r = self.profile(a)
        d = r["profile"]["dataset"]
        self.assertEqual((d["rows"], d["columns"], d["duplicate_rows"], d["missing_cells"], d["complete_rows"]), (5, 3, 1, 3, 2))
        self.assertEqual(d["basis"], "fact")
        self.assertEqual(d["missing_pct"], 20.0)
        k = self.col(r, "k")
        self.assertEqual(k["distinct"], {"value": 1, "exact": True, "basis": "fact"})
        self.assertIn("CONSTANT", {f["code"] for f in k["flags"]})
        note = self.col(r, "note")
        self.assertEqual((note["missing"], note["missing_pct"]), (3, 60.0))
        self.assertIn("MOSTLY_EMPTY", {f["code"] for f in note["flags"]})
        ident = self.col(r, "id")
        self.assertNotIn("ALL_UNIQUE", {f["code"] for f in ident["flags"]})  # id 2 repeats
        self.assertEqual(ident["top_values"][0], {"value": "2", "count": 2})
        self.assertEqual(self.col(r, "k")["type"]["basis"], "heuristic")

    def test_unique_integer_column_is_flagged_as_a_likely_identifier_heuristic(self):
        a = self.ok("u.csv", csv_bytes(["id"], [(i,) for i in range(1, 8)]))
        flags = {f["code"]: f["basis"] for f in self.col(self.profile(a), "id")["flags"]}
        self.assertEqual(flags["ALL_UNIQUE"], "fact")
        self.assertEqual(flags["LIKELY_IDENTIFIER"], "heuristic")

    def test_dates_and_text_lengths(self):
        a = self.ok("t.csv", csv_bytes(["when", "word"], [("2024-03-01", "ab"), ("2024-01-15", "abcd"), ("2024-02-10", " pad ")]))
        r = self.profile(a)
        self.assertEqual((self.col(r, "when")["dates"]["min"], self.col(r, "when")["dates"]["max"]), ("2024-01-15 00:00:00", "2024-03-01 00:00:00"))
        w = self.col(r, "word")
        self.assertEqual((w["text_length"]["min"], w["text_length"]["max"]), (2, 5))
        self.assertIn("EXTRA_SPACES", {f["code"] for f in w["flags"]})

    def test_profile_records_source_version_checksum_time_and_profiler_version(self):
        a = self.ok("s.csv", b"a\n1\n2\n")
        r = self.profile(a)
        src = r["profile"]["source"]
        self.assertEqual((src["asset_id"], src["version_id"], src["version_number"], src["checksum_sha256"]),
                         (a["id"], a["current_version_id"], 1, a["checksum_sha256"]))
        self.assertEqual(r["profile"]["profiler_version"], "1")
        self.assertTrue(r["generated_at"] and r["profile"]["generated_at"] == r["generated_at"])
        self.assertFalse(r["stale"])
        self.assertTrue(r["profile"]["basis_legend"])

    def test_reproducible_same_data_gives_the_same_content_hash(self):
        a = self.ok("r.csv", csv_bytes(["x", "y"], [(i, i * i % 7) for i in range(1, 60)]))
        first = self.profile(a)
        again = self.profile(a)  # reuses the stored profile
        self.assertEqual(first["id"], again["id"])
        refreshed = self.profile(a, refresh=True)
        self.assertNotEqual(first["id"], refreshed["id"])
        self.assertEqual(first["profile"]["content_hash"], refreshed["profile"]["content_hash"])
        other = self.ok("o.csv", csv_bytes(["x", "y"], [(i, i * i % 5) for i in range(1, 60)]))
        self.assertNotEqual(self.profile(other)["profile"]["content_hash"], first["profile"]["content_hash"])

    def test_get_before_generate_says_so(self):
        a = self.ok("g.csv", b"a\n1\n")
        got = self.ws.profiles.get(self.alice, a["id"])
        self.assertEqual((got["profile"], got["can_generate"]), (None, True))
        self.profile(a)
        self.assertEqual(self.ws.profiles.get(self.alice, a["id"])["profile"]["kind"], "dataset")

    def test_document_profile_facts_and_estimates(self):
        a = self.ok("d.md", b"# Title\n\nOne two three four five.\n\n## Part\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        d = self.profile(a)["profile"]["document"]
        self.assertEqual(d["pages"]["basis"], "estimate")
        self.assertEqual(d["words"]["basis"], "fact")
        self.assertEqual((d["headings"]["count"], d["headings"]["by_level"]), (2, {"1": 1, "2": 1}))
        self.assertEqual(d["tables"]["count"], 1)
        self.assertEqual(d["reading_time_minutes"]["basis"], "estimate")
        self.assertIsNone(d["ocr"])

    @unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
    def test_pdf_profile_marks_exact_page_count(self):
        a = self.ok("p.pdf", fx.pdf(["First page has enough words to count.", "Second page also has words."]))
        d = self.profile(a)["profile"]["document"]
        self.assertEqual((d["pages"]["value"], d["pages"]["basis"]), (2, "fact"))
        self.assertEqual(d["pages_by_method"], {"text": 2})

    def test_isolation_failed_assets_and_archived_projects(self):
        a = self.ok("i.csv", b"a\n1\n2\n")
        self.profile(a)
        self.assertEqual(code_of(self.ws.profiles.get, self.bob, a["id"]), "NOT_FOUND")
        self.assertEqual(code_of(self.ws.profiles.generate, self.bob, a["id"]), "NOT_FOUND")
        bad = self.failed("bad.json", b'{"a": [1, 2')
        self.assertEqual(code_of(self.ws.profiles.generate, self.alice, bad["id"]), "ASSET_NOT_READY")
        self.assertEqual(code_of(self.ws.profiles.get, self.alice, a["id"], "f" * 32), "NOT_FOUND")
        self.ws.projects.archive(self.alice, self.project)
        self.assertEqual(self.ws.profiles.get(self.alice, a["id"])["profile"]["kind"], "dataset")  # reading is still allowed
        self.assertEqual(code_of(self.ws.profiles.generate, self.alice, a["id"], refresh=True), "PROJECT_NOT_ACTIVE")

    def test_profile_contents_stay_in_storage_not_in_the_database(self):
        a = self.ok("s.csv", b"name\nZebulon Quimby\nZebulon Quimby\n")
        r = self.profile(a)
        self.assertEqual(r["profile"]["columns"][0]["top_values"][0]["value"], "Zebulon Quimby")
        dump = "\n".join(str(tuple(x)) for t in ("profiles", "audit_events", "project_events") for x in self.h.conn.execute(f"SELECT * FROM {t}").fetchall())
        self.assertNotIn("Zebulon", dump)
        self.assertIn("asset.profiled", dump)

    def test_wide_and_large_columns_complete(self):
        rows = [tuple(range(i, i + 30)) for i in range(2000)]
        a = self.ok("w.csv", csv_bytes([f"c{i}" for i in range(30)], rows))
        self.assertEqual(self.profile(a)["profile"]["dataset"]["columns"], 30)


if __name__ == "__main__":
    unittest.main()
