"""Upload pipeline: chunking, resume, retry, duplicates, validation, quotas, isolation and cleanup (real SQLite + real local storage)."""
import hashlib
import io
import re
import unittest
import zipfile
from datetime import timedelta

from app.core.errors import AppError
from app.modules.workspace.processing import NullProcessor
from support import Harness

CSV = b"name,score\nAda,91\nGrace,88\nLinus,79\n"
KEY = re.compile(r"^[a-z0-9]+(/[a-z0-9_.\-]+)*$")


def code_of(call, *args, **kwargs):
    try:
        call(*args, **kwargs)
    except AppError as error:
        return error.code
    return None


class FixedPages(NullProcessor):
    def __init__(self, pages):
        self.pages = pages

    def pages_for_quota(self, path, fmt):
        return self.pages


def upload(ws, principal, project_id, name, data, *, checksum=True, send=None):
    """Drive the whole pipeline the way the browser does. Returns complete()'s result."""
    started = ws.uploads.start(principal, project_id, name, len(data), hashlib.sha256(data).hexdigest() if checksum else None)
    if started.get("duplicate"):
        return started
    info = started["upload"]
    size = info["chunk_size"]
    for index in range(info["total_chunks"]):
        if send is None or index in send:
            ws.uploads.put_chunk(principal, info["id"], index, data[index * size:(index + 1) * size])
    return ws.uploads.complete(principal, info["id"])


class UploadBase(unittest.TestCase):
    chunk = 16

    def setUp(self):
        self.h = Harness(upload_chunk_max_bytes=self.chunk, upload_max_bytes=4096)
        self.addCleanup(self.h.close)
        self.alice, self.alice_token = self.h.make_user("alice@example.com")
        self.bob, self.bob_token = self.h.make_user("bob@example.com")
        self.ws = self.h.workspace()
        self.project = self.ws.projects.create(self.alice, "P")["id"]

    def stored_keys(self):
        return self.h.storage.list_prefix("p") + self.h.storage.list_prefix("t") if self.h.storage.exists("x") is False else []

    def live_assets(self, project=None):
        return self.h.conn.execute("SELECT * FROM assets WHERE project_id = ? AND status IN ('processing','ready','failed')", (project or self.project,)).fetchall()


class PipelineTests(UploadBase):
    def test_happy_path_keeps_the_original_byte_for_byte(self):
        result = upload(self.ws, self.alice, self.project, "scores.csv", CSV)
        asset = result["asset"]
        self.assertFalse(result["duplicate"])
        self.assertEqual((asset["status"], asset["kind"], asset["format"], asset["size_bytes"]), ("ready", "dataset", "csv", len(CSV)))
        self.assertEqual(asset["checksum_sha256"], hashlib.sha256(CSV).hexdigest())
        version = self.ws.repo.versions(asset["id"])[0]
        self.assertEqual(self.h.storage.read_bytes(version["storage_key"]), CSV)
        self.assertEqual(version["kind"], "original")
        self.assertEqual(self.ws.projects.get(self.alice, self.project)["datasets"], 1)
        self.assertEqual(self.h.storage.list_prefix("t"), [])  # temporary chunks are gone

    def test_every_storage_key_is_opaque(self):
        upload(self.ws, self.alice, self.project, "../../etc/passwd.csv", CSV)
        keys = self.h.storage.list_prefix("p")
        self.assertTrue(keys)
        for key in keys:
            self.assertRegex(key, KEY)
            self.assertNotIn("passwd", key)
        self.assertEqual(self.live_assets()[0]["name"], "passwd.csv")

    def test_resume_after_an_interruption(self):
        start = self.ws.uploads.start(self.alice, self.project, "scores.csv", len(CSV))["upload"]
        total = start["total_chunks"]
        self.assertGreater(total, 2)
        self.ws.uploads.put_chunk(self.alice, start["id"], 0, CSV[:self.chunk])
        self.ws.uploads.put_chunk(self.alice, start["id"], 2, CSV[2 * self.chunk:3 * self.chunk])
        again = self.ws.uploads.start(self.alice, self.project, "scores.csv", len(CSV))
        self.assertTrue(again["resumed"])
        self.assertEqual((again["upload"]["id"], again["upload"]["received_chunks"]), (start["id"], [0, 2]))
        self.assertEqual(code_of(self.ws.uploads.complete, self.alice, start["id"]), "UPLOAD_INCOMPLETE")
        for i in range(total):
            if i not in (0, 2):
                self.ws.uploads.put_chunk(self.alice, start["id"], i, CSV[i * self.chunk:(i + 1) * self.chunk])
        self.assertEqual(self.ws.uploads.complete(self.alice, start["id"])["asset"]["status"], "ready")

    def test_resending_a_chunk_is_harmless_and_complete_twice_returns_the_same_asset(self):
        start = self.ws.uploads.start(self.alice, self.project, "s.csv", len(CSV))["upload"]
        for i in range(start["total_chunks"]):
            piece = CSV[i * self.chunk:(i + 1) * self.chunk]
            self.ws.uploads.put_chunk(self.alice, start["id"], i, piece)
            self.ws.uploads.put_chunk(self.alice, start["id"], i, piece)
        first = self.ws.uploads.complete(self.alice, start["id"])
        second = self.ws.uploads.complete(self.alice, start["id"])
        self.assertEqual(first["asset"]["id"], second["asset"]["id"])
        self.assertTrue(second["repeat"])
        self.assertEqual(len(self.live_assets()), 1)

    def test_the_same_bytes_never_create_a_second_live_asset(self):
        first = upload(self.ws, self.alice, self.project, "a.csv", CSV)
        again = upload(self.ws, self.alice, self.project, "renamed.csv", CSV)  # declared checksum -> caught at start
        blind = upload(self.ws, self.alice, self.project, "again.csv", CSV, checksum=False)  # no checksum declared -> caught at complete
        for result in (again, blind):
            self.assertTrue(result["duplicate"])
            self.assertEqual(result["asset"]["id"], first["asset"]["id"])
        self.assertEqual(len(self.live_assets()), 1)
        stored = self.h.storage.list_prefix("p")  # one original plus its derived readable table; the duplicates added nothing
        self.assertEqual([k for k in stored if k.endswith("/original")].__len__(), 1)
        self.assertEqual(len(stored), 2)
        self.assertEqual(self.h.storage.list_prefix("t"), [])
        other = self.ws.projects.create(self.alice, "Other")["id"]
        self.assertFalse(upload(self.ws, self.alice, other, "a.csv", CSV)["duplicate"])  # duplicates are per project

    def test_a_concurrent_complete_loses_cleanly(self):
        start = self.ws.uploads.start(self.alice, self.project, "s.csv", len(CSV))["upload"]
        for i in range(start["total_chunks"]):
            self.ws.uploads.put_chunk(self.alice, start["id"], i, CSV[i * self.chunk:(i + 1) * self.chunk])
        self.assertTrue(self.ws.repo.claim_upload(start["id"], "t"))  # somebody else is assembling
        self.assertEqual(code_of(self.ws.uploads.complete, self.alice, start["id"]), "UPLOAD_IN_PROGRESS")
        self.assertEqual(code_of(self.ws.uploads.cancel, self.alice, start["id"]), "UPLOAD_IN_PROGRESS")

    def test_cancel_discards_everything(self):
        start = self.ws.uploads.start(self.alice, self.project, "s.csv", len(CSV))["upload"]
        self.ws.uploads.put_chunk(self.alice, start["id"], 0, CSV[:self.chunk])
        self.assertTrue(self.h.storage.list_prefix("t"))
        self.assertTrue(self.ws.uploads.cancel(self.alice, start["id"])["cancelled"])
        self.assertEqual(self.h.storage.list_prefix("t"), [])
        self.assertEqual(code_of(self.ws.uploads.status, self.alice, start["id"]), "NOT_FOUND")

    def test_expired_uploads_are_refused(self):
        start = self.ws.uploads.start(self.alice, self.project, "s.csv", len(CSV))["upload"]
        self.h.clock.advance(hours=25)
        self.assertEqual(code_of(self.ws.uploads.put_chunk, self.alice, start["id"], 0, CSV[:self.chunk]), "UPLOAD_EXPIRED")
        self.assertEqual(self.ws.uploads.list_open(self.alice, self.project)["items"], [])

    def test_open_upload_cap(self):
        for i in range(5):
            self.ws.uploads.start(self.alice, self.project, f"f{i}.csv", 100 + i)
        self.assertEqual(code_of(self.ws.uploads.start, self.alice, self.project, "f9.csv", 999), "TOO_MANY_UPLOADS")


class ValidationTests(UploadBase):
    def test_chunk_checks(self):
        start = self.ws.uploads.start(self.alice, self.project, "s.csv", len(CSV))["upload"]
        u = start["id"]
        self.assertEqual(code_of(self.ws.uploads.put_chunk, self.alice, u, 0, b"short"), "CHUNK_SIZE_MISMATCH")
        self.assertEqual(code_of(self.ws.uploads.put_chunk, self.alice, u, 0, CSV[:self.chunk] + b"x"), "CHUNK_SIZE_MISMATCH")
        self.assertEqual(code_of(self.ws.uploads.put_chunk, self.alice, u, 99, CSV[:self.chunk]), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.ws.uploads.put_chunk, self.alice, u, -1, CSV[:self.chunk]), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.ws.uploads.put_chunk, self.alice, u, True, CSV[:self.chunk]), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.ws.uploads.put_chunk, self.alice, u, 0, CSV[:self.chunk], "0" * 64), "CHUNK_CHECKSUM_MISMATCH")
        self.assertEqual(self.ws.uploads.status(self.alice, u)["upload"]["received_chunks"], [])

    def test_size_is_enforced_by_the_server(self):
        self.assertEqual(code_of(self.ws.uploads.start, self.alice, self.project, "big.csv", 4097), "FILE_TOO_LARGE")
        for bad in (0, -5, "10", None, 1.5, True):
            self.assertEqual(code_of(self.ws.uploads.start, self.alice, self.project, "x.csv", bad), "VALIDATION_ERROR", bad)

    def test_declared_checksum_must_match(self):
        started = self.ws.uploads.start(self.alice, self.project, "s.csv", len(CSV), "ab" * 32)["upload"]
        for i in range(started["total_chunks"]):
            self.ws.uploads.put_chunk(self.alice, started["id"], i, CSV[i * self.chunk:(i + 1) * self.chunk])
        self.assertEqual(code_of(self.ws.uploads.complete, self.alice, started["id"]), "CHECKSUM_MISMATCH")
        self.assertEqual(self.live_assets(), [])
        self.assertEqual(self.h.storage.list_prefix("p"), [])
        self.assertEqual(code_of(self.ws.uploads.start, self.alice, self.project, "x.csv", 10, "not-a-hash"), "VALIDATION_ERROR")

    def test_unsupported_and_disguised_files_are_rejected_with_a_reason_and_leave_nothing_behind(self):
        cases = {
            "tool.exe": (b"MZ" + b"\x00" * 30, "UNSUPPORTED_FILE"),
            "noextension": (CSV, "UNSUPPORTED_FILE"),
            "archive.zip": (b"PK\x03\x04" + b"\x00" * 40, "UNSUPPORTED_FILE"),
            "evil.csv": (b"MZ\x90\x00" + b"\x00" * 30, "UNSUPPORTED_FILE"),         # program disguised as CSV
            "photo.pdf": (b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "UNSUPPORTED_FILE"),   # image disguised as PDF
            "fake.pdf": (b"just text, no pdf header here", "FILE_CORRUPTED"),
            "fake.xlsx": (b"plain text pretending", "UNSUPPORTED_FILE"),
            "fake.docx": (b"PK\x03\x04" + b"\x00" * 40, "FILE_CORRUPTED"),            # zip signature but not a zip
            "binary.csv": (bytes(range(256)) * 2, "UNSUPPORTED_FILE"),
            "empty.txt": (b"", "VALIDATION_ERROR"),
            "old.xls": (b"PK\x03\x04" + b"\x00" * 40, "UNSUPPORTED_FILE"),
            "bad.json": (b"this is not json at all", "FILE_CORRUPTED"),
            "bad.rtf": (b"not rtf", "UNSUPPORTED_FILE"),
        }
        for name, (data, expected) in cases.items():
            try:
                code = code_of(upload, self.ws, self.alice, self.project, name, data) if data else code_of(self.ws.uploads.start, self.alice, self.project, name, 0)
            finally:
                pass
            self.assertEqual(code, expected, name)
        self.assertEqual(self.live_assets(), [])
        self.assertEqual(self.h.storage.list_prefix("p"), [])
        self.assertEqual(self.h.storage.list_prefix("t"), [])

    def test_a_package_that_is_zip_but_not_the_claimed_format(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("readme.txt", "hi")
        self.assertEqual(code_of(upload, self.ws, self.alice, self.project, "report.docx", buffer.getvalue()), "UNSUPPORTED_FILE")

    def test_decompression_bombs_and_unsafe_paths_are_rejected(self):
        h = Harness(upload_chunk_max_bytes=64 * 1024, upload_max_bytes=2 * 1024 * 1024)
        self.addCleanup(h.close)
        user, _ = h.make_user("zed@example.com")
        ws = h.workspace()
        pid = ws.projects.create(user, "Z")["id"]
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", b"\x00" * (50 * 1024 * 1024))   # compresses ~1000:1
        self.assertLess(len(bomb.getvalue()), 2 * 1024 * 1024)
        self.assertEqual(code_of(upload, ws, user, pid, "bomb.docx", bomb.getvalue()), "FILE_CORRUPTED")
        sneaky = io.BytesIO()
        with zipfile.ZipFile(sneaky, "w") as z:
            z.writestr("word/document.xml", "<w/>")
            z.writestr("../../evil.txt", "x")
        self.assertEqual(code_of(upload, ws, user, pid, "sneaky.docx", sneaky.getvalue()), "FILE_CORRUPTED")
        self.assertEqual(ws.repo.count_assets(pid), 0)
        self.assertEqual(h.storage.list_prefix("p"), [])


class QuotaTests(UploadBase):
    def docs(self, ws, user, project, n, start=0):
        for i in range(start, start + n):
            upload(ws, user, project, f"d{i}.txt", f"document number {i}".encode())

    def test_standard_accounts_keep_three_documents_across_projects(self):
        self.docs(self.ws, self.alice, self.project, 2)
        second = self.ws.projects.create(self.alice, "Second")["id"]
        self.docs(self.ws, self.alice, second, 1, start=2)
        self.assertEqual(code_of(upload, self.ws, self.alice, self.project, "d3.txt", b"one too many"), "DOCUMENT_LIMIT_REACHED")
        self.assertEqual(code_of(self.ws.uploads.start, self.alice, self.project, "d3.txt", 12), "DOCUMENT_LIMIT_REACHED")
        self.assertTrue(upload(self.ws, self.alice, self.project, "data.csv", CSV)["asset"]["status"] == "ready")  # datasets are not limited by this rule
        self.assertEqual(self.ws.quota.summary(self.alice)["documents_used"], 3)

    def test_deleting_a_document_frees_a_slot(self):
        self.docs(self.ws, self.alice, self.project, 3)
        victim = self.live_assets()[0]["id"]
        self.ws.assets.delete(self.alice, victim)
        self.assertEqual(upload(self.ws, self.alice, self.project, "new.txt", b"fresh document")["asset"]["status"], "ready")

    def test_page_limit_for_standard_accounts_and_no_leftovers(self):
        h = Harness(upload_chunk_max_bytes=16, upload_max_bytes=4096)
        self.addCleanup(h.close)
        user, _ = h.make_user("pages@example.com")
        from app.modules.storage import LocalStorage
        from app.modules.workspace.container import build_workspace

        h.storage = LocalStorage(h.settings.storage_dir)
        ws = build_workspace(h.services().ctx, h.storage, FixedPages(101))
        pid = ws.projects.create(user, "P")["id"]
        self.assertEqual(code_of(upload, ws, user, pid, "long.txt", b"pretend this has 101 pages"), "PAGE_LIMIT_EXCEEDED")
        self.assertEqual(ws.repo.count_assets(pid), 0)
        self.assertEqual(h.storage.list_prefix("p"), [])
        ws_ok = build_workspace(h.services().ctx, h.storage, FixedPages(100))
        self.assertEqual(upload(ws_ok, user, pid, "ok.txt", b"exactly one hundred pages")["asset"]["status"], "ready")

    def test_special_access_is_unlimited_and_revocation_applies_to_the_next_request(self):
        owner, _ = self.h.make_owner("owner@example.com")
        self.h.services().codes.grant_special(owner, self.alice.user_id)
        special = self.h.principal(self.alice_token)
        self.assertTrue(self.ws.quota.summary(special)["unlimited"])
        self.docs(self.ws, special, self.project, 5)
        self.assertEqual(self.ws.quota.summary(special)["documents_used"], 5)
        entitlement = self.h.services().ctx.repo.active_entitlements(self.alice.user_id)[0]
        self.h.services().codes.revoke_entitlement(owner, entitlement["id"], "test")
        standard = self.h.principal(self.alice_token)
        self.assertFalse(self.ws.quota.summary(standard)["unlimited"])
        self.assertEqual(code_of(upload, self.ws, standard, self.project, "more.txt", b"blocked again"), "DOCUMENT_LIMIT_REACHED")

    def test_owner_follows_existing_permissions(self):
        owner, _ = self.h.make_owner("owner@example.com")
        ws = self.h.workspace()
        pid = ws.projects.create(owner, "Owner project")["id"]
        self.docs(ws, owner, pid, 4)
        self.assertEqual(ws.quota.summary(owner)["documents_used"], 4)


class IsolationTests(UploadBase):
    def test_other_users_cannot_touch_uploads_or_assets(self):
        bob_ws = self.h.workspace()
        asset = upload(self.ws, self.alice, self.project, "mine.csv", CSV)["asset"]["id"]
        start = self.ws.uploads.start(self.alice, self.project, "more.csv", 40)["upload"]["id"]
        probes = [
            lambda: bob_ws.uploads.start(self.bob, self.project, "x.csv", 10),
            lambda: bob_ws.uploads.list_open(self.bob, self.project),
            lambda: bob_ws.uploads.status(self.bob, start),
            lambda: bob_ws.uploads.put_chunk(self.bob, start, 0, b"x" * self.chunk),
            lambda: bob_ws.uploads.complete(self.bob, start),
            lambda: bob_ws.uploads.cancel(self.bob, start),
            lambda: bob_ws.assets.list(self.bob, self.project),
            lambda: bob_ws.assets.get(self.bob, asset),
            lambda: bob_ws.assets.delete(self.bob, asset),
            lambda: bob_ws.assets.open_original(self.bob, asset),
        ]
        for probe in probes:
            self.assertEqual(code_of(probe), "NOT_FOUND")
        self.assertEqual(self.ws.assets.get(self.alice, asset)["name"], "mine.csv")
        for weird in ("../../x", "' OR 1=1", "", "z" * 300):
            self.assertEqual(code_of(bob_ws.assets.get, self.bob, weird), "NOT_FOUND")

    def test_archived_projects_are_read_only(self):
        asset = upload(self.ws, self.alice, self.project, "mine.csv", CSV)["asset"]["id"]
        self.ws.projects.archive(self.alice, self.project)
        self.assertEqual(code_of(self.ws.uploads.start, self.alice, self.project, "n.csv", 10), "PROJECT_NOT_ACTIVE")
        self.assertEqual(code_of(self.ws.assets.delete, self.alice, asset), "PROJECT_NOT_ACTIVE")
        self.assertEqual(self.ws.assets.get(self.alice, asset)["status"], "ready")  # still readable
        self.assertEqual(b"".join(self.ws.assets.open_original(self.alice, asset)[2]), CSV)

    def test_download_and_delete(self):
        asset = upload(self.ws, self.alice, self.project, "mine.csv", CSV)["asset"]["id"]
        name, size, stream = self.ws.assets.open_original(self.alice, asset)
        self.assertEqual((name, size, b"".join(stream)), ("mine.csv", len(CSV), CSV))
        result = self.ws.assets.delete(self.alice, asset)
        self.assertTrue(result["deleted"])
        self.assertEqual(self.h.storage.list_prefix("p"), [])
        self.assertEqual(code_of(self.ws.assets.get, self.alice, asset), "NOT_FOUND")
        self.assertEqual(self.ws.projects.get(self.alice, self.project)["datasets"], 0)

    def test_logs_and_audit_never_contain_file_contents_or_names(self):
        upload(self.ws, self.alice, self.project, "salaries-secret.csv", b"name,pay\nSECRETPERSON,123456\n")
        dump = " ".join(str(tuple(r)) for r in self.h.conn.execute("SELECT metadata, reason FROM audit_events"))
        dump += " ".join(str(tuple(r)) for r in self.h.conn.execute("SELECT detail FROM project_events"))
        for needle in ("SECRETPERSON", "123456", "salaries-secret"):
            self.assertNotIn(needle, dump)


if __name__ == "__main__":
    unittest.main()
