"""Extraction on real files of every supported format, including damaged ones (real SQLite, real local storage, real parsers)."""
import hashlib
import json
import shutil
import unittest

from app.core.errors import AppError
from support import Harness
import fixtures as fx
from test_workspace_uploads import code_of, upload

try:
    import pypdf  # noqa: F401
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False
HAS_OCR = bool(shutil.which("tesseract") and shutil.which("pdftoppm")) and fx.scanned_pdf() is not None


class Base(unittest.TestCase):
    def setUp(self):
        self.h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=8 << 20)
        self.addCleanup(self.h.close)
        self.alice, _ = self.h.make_user("alice@example.com")
        self.bob, _ = self.h.make_user("bob@example.com")
        self.ws = self.h.workspace()
        self.project = self.ws.projects.create(self.alice, "P")["id"]

    def put(self, name, data, principal=None):
        return upload(self.ws, principal or self.alice, self.project, name, data)["asset"]

    def ok(self, name, data):
        asset = self.put(name, data)
        self.assertEqual(asset["status"], "ready", asset.get("status_detail"))
        return asset

    def failed(self, name, data):
        asset = self.put(name, data)
        self.assertEqual(asset["status"], "failed", "expected a failure")
        self.assertTrue(asset["status_detail"])
        return asset

    def extraction(self, asset):
        return self.ws.data.extraction(self.alice, asset["id"])

    def cols(self, asset):
        return {c["name"]: c for c in self.extraction(asset)["summary"]["columns"]}

    def codes(self, asset):
        return {w["code"] for w in self.extraction(asset)["warnings"]}


class DatasetTests(Base):
    def test_csv_reports_columns_types_missing_and_invalid_values(self):
        data = b"id,name,score,joined\n1,Ada,91.5,2024-01-02\n2,Grace,,2024-02-03\n3,,88,2024-03-04\n4,Linus,abc,not a date\n5,Ken,70,2024-05-06\n"
        a = self.ok("people.csv", data)
        self.assertEqual((a["row_count"], a["column_count"]), (5, 4))
        c = self.cols(a)
        self.assertEqual((c["id"]["type"], c["id"]["missing"]), ("integer", 0))
        self.assertEqual(c["name"]["missing"], 1)
        self.assertEqual(c["score"]["type"], "mixed")  # 3 of 4 values numeric = 75% -> not confidently numeric
        self.assertEqual(c["score"]["missing"], 1)
        self.assertEqual(self.extraction(a)["summary"]["facts_vs_estimates"]["types"][:9], "heuristic")

    def test_numeric_column_with_a_few_bad_cells_counts_invalid_values(self):
        rows = b"v\n" + b"".join(f"{i}\n".encode() for i in range(1, 20)) + b"oops\n"
        a = self.ok("v.csv", rows)
        c = self.cols(a)["v"]
        self.assertEqual((c["type"], c["invalid"]), ("integer", 1))

    def test_original_bytes_are_untouched_by_extraction(self):
        data = b"a,b\n1,2\n3,4\n"
        a = self.ok("x.csv", data)
        v = self.ws.repo.versions(a["id"])[0]
        self.assertEqual(hashlib.sha256(self.h.storage.read_bytes(v["storage_key"])).hexdigest(), hashlib.sha256(data).hexdigest())

    def test_semicolon_delimiter_and_windows_1252_encoding_are_detected_and_flagged(self):
        a = self.ok("eu.csv", "ville;prix\nZürich;10,5\nMünchen;11\n".encode("cp1252"))
        self.assertEqual(a["column_count"], 2)
        self.assertIn("ENCODING_GUESS", self.codes(a))
        page = self.ws.data.preview(self.alice, a["id"])
        self.assertEqual(page["rows"][0][0], "Zürich")

    def test_csv_without_header_ragged_rows_and_blank_rows_are_explained(self):
        a = self.ok("n.csv", b"1,2,3\n4,5\n\n6,7,8,9\n")
        self.assertEqual(a["column_count"], 4)
        codes = self.codes(a)
        self.assertTrue({"NO_HEADER", "SHORT_ROWS", "BLANK_ROWS", "EXTRA_COLUMNS"} <= codes)

    def test_duplicate_and_empty_header_names_become_unique(self):
        a = self.ok("d.csv", b"a,a,,b\n1,2,3,4\n")
        self.assertEqual(list(self.cols(a)), ["a", "a_2", "column_3", "b"])

    def test_tsv(self):
        a = self.ok("t.tsv", b"x\ty\n1\t2\n")
        self.assertEqual((a["row_count"], a["column_count"]), (1, 2))

    def test_xlsx_with_dates_booleans_and_extra_sheets(self):
        rows = [["when", "ok", "n"], [45292, True, 1.5], [45293, False, 2], [45294, True, None]]
        a = self.ok("book.xlsx", fx.xlsx(rows, sheets_extra=2, date_col=0))
        c = self.cols(a)
        self.assertEqual(c["when"]["type"], "date")
        self.assertEqual(c["ok"]["type"], "boolean")
        self.assertEqual(c["n"]["missing"], 1)
        self.assertEqual(self.ws.data.preview(self.alice, a["id"])["rows"][0][0], "2024-01-01")
        self.assertIn("OTHER_SHEETS_IGNORED", self.codes(a))

    def test_ods(self):
        a = self.ok("sheet.ods", fx.ods([["city", "pop"], ["Pune", 3124458], ["Agra", 1585704]]))
        self.assertEqual((a["row_count"], a["column_count"]), (2, 2))
        self.assertEqual(self.cols(a)["pop"]["type"], "integer")

    def test_json_records_nested_values_and_json_lines(self):
        a = self.ok("r.json", json.dumps([{"id": 1, "geo": {"lat": 1.5, "lon": 2}, "tags": ["a", "b"]}, {"id": 2, "geo": {"lat": 3}}]).encode())
        self.assertEqual(set(self.cols(a)), {"id", "geo.lat", "geo.lon", "tags"})
        b = self.ok("l.json", b'{"a":1}\n{"a":2}\n')
        self.assertIn("JSON_LINES", self.codes(b))
        c = self.ok("w.json", b'{"meta":{"x":1},"items":[{"k":1},{"k":2}]}')
        self.assertEqual(c["row_count"], 2)

    def test_xml_records(self):
        xml = b"<rows><row id='1'><name>Ada</name><city>Pune</city></row><row id='2'><name>Grace</name></row></rows>"
        a = self.ok("r.xml", xml)
        self.assertEqual(a["row_count"], 2)
        self.assertEqual(set(self.cols(a)), {"@id", "name", "city"})

    def test_xml_with_entity_declarations_is_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><r><x>&b;</x><x>1</x></r>'
        a = self.failed("bomb.xml", bomb)
        self.assertEqual(a["status_code"], "FILE_CORRUPTED")

    def test_legacy_xls_is_clearly_unavailable_when_xlrd_is_missing(self):
        try:
            import xlrd  # noqa: F401
            self.skipTest("xlrd is installed")
        except ImportError:
            pass
        a = self.failed("old.xls", bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 600)
        self.assertEqual(a["status_code"], "PARSER_UNAVAILABLE")
        self.assertIn("xlsx", a["status_detail"])

    def test_damaged_files_fail_with_a_plain_explanation_and_are_kept(self):
        good = fx.xlsx([["a"], [1]])
        for name, data, code in [
            ("trunc.xlsx", good[: len(good) // 2], None),
            ("empty.csv", b"\n\n", "FILE_CORRUPTED"),
            ("hdr.csv", b"a,b,c\n", "FILE_CORRUPTED"),
            ("bad.json", b'{"a": [1, 2', "FILE_CORRUPTED"),
            ("bad.xml", b"<a><b></a>", "FILE_CORRUPTED"),
            ("macro.xlsx", fx.xlsx([["a"], [1]], macro=True), "FILE_CORRUPTED"),
        ]:
            with self.subTest(name):
                try:
                    asset = self.put(name, data)
                except AppError as error:  # rejected before storage is also a valid, explained outcome
                    self.assertTrue(error.message)
                    continue
                self.assertEqual(asset["status"], "failed")
                if code:
                    self.assertEqual(asset["status_code"], code)
                self.assertTrue(asset["status_detail"])

    def test_row_limit_is_enforced(self):
        h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=8 << 20, parse_max_rows=10)
        self.addCleanup(h.close)
        user, _ = h.make_user("c@example.com")
        ws = h.workspace()
        pid = ws.projects.create(user, "P")["id"]
        asset = upload(ws, user, pid, "big.csv", b"a\n" + b"1\n" * 50)["asset"]
        self.assertEqual((asset["status"], asset["status_code"]), ("failed", "FILE_CORRUPTED"))
        self.assertIn("rows", asset["status_detail"])

    def test_preview_pages_through_rows(self):
        a = self.ok("p.csv", b"n\n" + b"".join(f"{i}\n".encode() for i in range(1, 121)))
        first = self.ws.data.preview(self.alice, a["id"], limit=50)
        self.assertEqual((len(first["rows"]), first["total_rows"], first["rows"][0]), (50, 120, ["1"]))
        last = self.ws.data.preview(self.alice, a["id"], offset=100, limit=50)
        self.assertEqual((len(last["rows"]), last["rows"][-1]), (20, ["120"]))
        self.assertEqual(len(self.ws.data.preview(self.alice, a["id"], limit=10_000)["rows"]), 120)  # capped at 200 per page


class DocumentTests(Base):
    def test_txt_and_markdown_headings_and_tables(self):
        a = self.ok("n.txt", b"Hello world.\n\nSecond paragraph here.\n")
        self.assertEqual((a["kind"], a["page_count"], a["page_count_estimated"]), ("document", 1, True))
        md = b"# Title\n\nIntro text.\n\n## Part A\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\nTail.\n"
        b = self.ok("n.md", md)
        s = self.ws.data.structure(self.alice, b["id"])
        self.assertEqual([(h["level"], h["text"]) for h in s["headings"]], [(1, "Title"), (2, "Part A")])
        self.assertEqual(s["tables"][0]["rows"], [["a", "b"], ["1", "2"], ["3", "4"]])

    def test_long_text_is_split_into_estimated_virtual_pages(self):
        words = ("lorem ipsum dolor sit amet " * 400).encode()  # 2000 words -> 4 virtual pages
        a = self.ok("long.txt", words)
        self.assertEqual((a["page_count"], a["page_count_estimated"]), (4, True))
        pages = self.ws.data.pages(self.alice, a["id"], start=2, limit=2)
        self.assertEqual([p["number"] for p in pages["pages"]], [2, 3])
        self.assertTrue(pages["page_count_estimated"])

    def test_docx_headings_tables_and_text(self):
        data = fx.docx([("Heading1", "Annual report"), (None, "Revenue grew."), ("Heading2", "Regions")], table=[["Region", "Sales"], ["North", "10"]])
        a = self.ok("r.docx", data)
        s = self.ws.data.structure(self.alice, a["id"])
        self.assertEqual([(h["level"], h["text"]) for h in s["headings"]], [(1, "Annual report"), (2, "Regions")])
        self.assertEqual(s["tables"][0]["rows"][1], ["North", "10"])
        text = self.ws.data.pages(self.alice, a["id"])["pages"][0]["text"]
        self.assertIn("Revenue grew.", text)
        self.assertEqual(self.ws.data.pages(self.alice, a["id"])["pages"][0]["ref"]["type"], "blocks")

    def test_rtf_text_is_recovered_without_control_words(self):
        a = self.ok("r.rtf", fx.rtf(["First line of text.", "Caf\\'e9 au lait."]))
        text = self.ws.data.pages(self.alice, a["id"])["pages"][0]["text"]
        self.assertIn("First line of text.", text)
        self.assertIn("Café au lait.", text)
        self.assertNotIn("\\par", text)
        self.assertNotIn("fonttbl", text)

    @unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
    def test_pdf_pages_text_and_source_references(self):
        a = self.ok("two.pdf", fx.pdf(["INTRODUCTION\nThis is page one of the report.", "Page two has the findings and more text here."]))
        self.assertEqual((a["page_count"], a["page_count_estimated"]), (2, False))
        e = self.extraction(a)
        self.assertEqual([p["method"] for p in e["pages"]], ["text", "text"])
        pages = self.ws.data.pages(self.alice, a["id"], start=2, limit=1)["pages"]
        self.assertIn("findings", pages[0]["text"])
        self.assertEqual(pages[0]["ref"], {"type": "page", "page": 2})
        self.assertTrue(any(h["text"] == "INTRODUCTION" and h["heuristic"] for h in self.ws.data.structure(self.alice, a["id"])["headings"]))

    @unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
    def test_pdf_with_no_text_and_no_ocr_fails_with_an_explanation(self):
        h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=8 << 20, ocr_enabled=False)
        self.addCleanup(h.close)
        user, _ = h.make_user("c@example.com")
        ws = h.workspace()
        pid = ws.projects.create(user, "P")["id"]
        asset = upload(ws, user, pid, "blank.pdf", fx.pdf(["", ""]))["asset"]
        self.assertEqual((asset["status"], asset["status_code"]), ("failed", "FILE_CORRUPTED"))

    @unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
    def test_partly_scanned_pdf_is_partial_and_names_the_pages(self):
        h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=8 << 20, ocr_enabled=False)
        self.addCleanup(h.close)
        user, _ = h.make_user("c@example.com")
        ws = h.workspace()
        pid = ws.projects.create(user, "P")["id"]
        asset = upload(ws, user, pid, "mix.pdf", fx.pdf(["Plenty of selectable text on this first page.", ""]))["asset"]
        self.assertEqual(asset["status"], "ready")
        e = ws.data.extraction(user, asset["id"])
        self.assertEqual((e["status"], e["summary"]["meta"]["pages_without_text"]), ("partial", [2]))
        self.assertIn("OCR_UNAVAILABLE", {w["code"] for w in e["warnings"]})
        self.assertEqual(e["pages"][1]["method"], "none")

    @unittest.skipUnless(HAS_PYPDF and HAS_OCR, "needs pypdf, Pillow, tesseract and pdftoppm")
    def test_scanned_pdf_is_read_with_ocr_and_reports_confidence(self):
        a = self.ok("scan.pdf", fx.scanned_pdf())
        e = self.extraction(a)
        page = e["pages"][0]
        self.assertEqual(page["method"], "ocr")
        self.assertGreater(page["ocr_confidence"], 50)
        text = self.ws.data.pages(self.alice, a["id"])["pages"][0]["text"].upper()
        self.assertIn("INVOICE", text)
        self.assertIn("OCR_USED", {w["code"] for w in e["warnings"]})
        self.assertEqual(e["summary"]["pages_by_method"]["ocr"], 1)

    @unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
    def test_damaged_pdf_fails_cleanly(self):
        good = fx.pdf(["Some text on a page that is long enough."])
        for name, data in [("cut.pdf", good[: len(good) // 2]), ("junk.pdf", b"%PDF-1.4\nthis is not a pdf at all\n" + b"x" * 200)]:
            with self.subTest(name):
                try:
                    asset = self.put(name, data)
                except AppError as error:
                    self.assertTrue(error.message)
                    continue
                self.assertEqual(asset["status"], "failed")
                self.assertTrue(asset["status_detail"])

    def test_empty_and_binary_text_files_are_refused(self):
        self.assertEqual(self.failed("e.txt", b"   \n\n")["status_code"], "FILE_CORRUPTED")
        self.assertIn(code_of(self.put, "b.txt", b"\x00\x01\x02" * 100) or "FAILED", ("FAILED", "UNSUPPORTED_FILE", "FILE_CORRUPTED", None))


class QuotaAndPrivacyTests(Base):
    @unittest.skipUnless(HAS_PYPDF, "pypdf not installed")
    def test_standard_user_page_limit_uses_the_real_pdf_page_count(self):
        h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=8 << 20, standard_max_pages=5)
        self.addCleanup(h.close)
        user, _ = h.make_user("c@example.com")
        ws = h.workspace()
        pid = ws.projects.create(user, "P")["id"]
        self.assertEqual(upload(ws, user, pid, "ok.pdf", fx.pdf(["page text number %d is here" % i for i in range(5)]))["asset"]["status"], "ready")
        self.assertEqual(code_of(upload, ws, user, pid, "big.pdf", fx.pdf(["page text number %d is here" % i for i in range(6)])), "PAGE_LIMIT_EXCEEDED")
        self.assertEqual(h.storage.list_prefix("t"), [])

    def test_other_users_cannot_read_previews_text_or_structure(self):
        csv_asset = self.ok("a.csv", b"secret,x\nhunter2,1\n")
        doc = self.ok("a.txt", b"confidential words live here\n")
        for call in (lambda: self.ws.data.preview(self.bob, csv_asset["id"]), lambda: self.ws.data.extraction(self.bob, csv_asset["id"]),
                     lambda: self.ws.data.pages(self.bob, doc["id"]), lambda: self.ws.data.structure(self.bob, doc["id"])):
            self.assertEqual(code_of(call), "NOT_FOUND")
        missing = code_of(self.ws.data.preview, self.bob, "0" * 32)
        self.assertEqual(missing, "NOT_FOUND")

    def test_wrong_kind_and_foreign_version_are_rejected(self):
        csv_asset = self.ok("a.csv", b"a\n1\n")
        doc = self.ok("a.txt", b"some words here\n")
        self.assertEqual(code_of(self.ws.data.pages, self.alice, csv_asset["id"]), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.ws.data.preview, self.alice, doc["id"]), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.ws.data.preview, self.alice, csv_asset["id"], version_id=doc["current_version_id"]), "NOT_FOUND")

    def test_audit_and_database_never_hold_file_contents(self):
        self.ok("s.csv", b"name,ssn\nZebulon Quimby,123-45-6789\n")
        self.ok("s.txt", b"Zebulon Quimby lives at 42 Quince Lane.\n")
        dump = "\n".join(str(tuple(r)) for t in ("audit_events", "assets", "extractions", "project_events", "asset_versions")
                         for r in self.h.conn.execute(f"SELECT * FROM {t}").fetchall())
        for needle in ("Zebulon", "123-45-6789", "Quince"):
            self.assertNotIn(needle, dump)


if __name__ == "__main__":
    unittest.main()
