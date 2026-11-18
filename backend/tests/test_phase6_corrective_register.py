"""The corrective requirements mapping must tell the truth about the repository, and the release archive must hold everything the tests read.

It checks that every implementation path in docs/phase6/CORRECTIVE_REQUIREMENTS.md exists, that every test it names exists (file, class and method), that a row is only 'Complete' when it names
tests, that every section of the corrective specification and addendum is represented, that the 'Unverified' production-build row is still there, and that the generated files are in step
with their source. The packaging checks guard a mistake found during the corrective work: the packaging script skipped any folder called "data", which silently dropped the test fixtures.
Run from backend/:  python -m unittest test_phase6_corrective_register"""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from test_phase6_register import declared, resolve

ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"Complete", "Partial", "Documented", "Unverified"}
SECTIONS = ["§1", "§2", "§3", "§4", "§5", "§6", "§7", "§8", "§9", "§10", "§11", "§12", "Addendum §1", "Addendum §2", "Addendum §3"]


def load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Register(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load("docs/phase6/build_corrective_register.py", "build_corrective_register")
        cls.rows = json.loads((ROOT / "docs/phase6/corrective_requirements.json").read_text(encoding="utf-8"))

    def test_every_row_has_its_fields_and_ids_are_unique(self):
        for r in self.rows:
            for key in ("id", "source", "requirement", "implementation", "tests", "status", "note"):
                self.assertIn(key, r, r.get("id"))
            self.assertIn(r["status"], STATUSES, r["id"])
            self.assertTrue(r["requirement"].strip() and r["implementation"].strip(), r["id"])
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_implementation_path_exists(self):
        missing = [(r["id"], t) for r in self.rows for t in re.split(r",\s*", r["implementation"]) if not resolve(t)]
        self.assertEqual(missing, [], "implementation paths that do not exist")

    def test_every_named_test_exists(self):
        missing = []
        for r in self.rows:
            for ref in r["tests"]:
                path, _, name = ref.partition("::")
                file = ROOT / path
                if not file.is_file():
                    missing.append((r["id"], ref)); continue
                if not name:
                    continue
                cls, _, method = name.partition(".")
                if not declared(file, cls, method or None):
                    missing.append((r["id"], ref))
        self.assertEqual(missing, [], "tests that do not exist")

    def test_a_row_is_only_complete_when_it_names_tests_and_an_unverified_or_partial_row_says_why(self):
        for r in self.rows:
            if r["status"] == "Complete":
                self.assertTrue(r["tests"], r["id"])
            if r["status"] in ("Partial", "Unverified"):
                self.assertTrue(r["note"].strip(), f"{r['id']} must state the gap")
            if r["status"] == "Documented":
                self.assertEqual(r["tests"], [], r["id"])

    def test_every_section_of_the_specification_and_the_addendum_is_represented(self):
        sources = " | ".join(r["source"] for r in self.rows)
        for s in SECTIONS:
            self.assertIn(s, sources, s)

    def test_the_production_build_is_still_recorded_as_not_verified_and_the_dataset_requirement_is_complete(self):
        by_id = {r["id"]: r for r in self.rows}
        self.assertEqual(by_id["CR-37"]["status"], "Unverified")
        self.assertEqual(by_id["CR-24"]["status"], "Complete")

    def test_the_generated_files_are_in_step_with_their_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "docs/phase6").mkdir(parents=True)
            self.builder.build(tmp_root)
            for name in ("corrective_requirements.json", "CORRECTIVE_REQUIREMENTS.md"):
                self.assertEqual((tmp_root / "docs/phase6" / name).read_text(encoding="utf-8"), (ROOT / "docs/phase6" / name).read_text(encoding="utf-8"),
                                 f"{name} is out of date: run python docs/phase6/build_corrective_register.py")


class Packaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        import package_release
        cls.pkg = package_release

    def test_every_file_the_backend_tests_read_is_in_the_release_archive(self):
        included = {p.relative_to(ROOT).as_posix() for p in self.pkg.release_files(ROOT)}
        needed = [p.relative_to(ROOT).as_posix() for p in (ROOT / "backend/tests").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
        self.assertGreater(len(needed), 50)
        self.assertEqual([f for f in needed if f not in included], [], "test files that the release archive would drop")

    def test_the_corrective_fixtures_are_shipped(self):
        included = {p.relative_to(ROOT).as_posix() for p in self.pkg.release_files(ROOT)}
        for name in ("arxiv_html_paper.html", "arxiv_paper.pdf", "scanned_paper.pdf", "no_headings.pdf", "wikipedia_business_class.html"):
            self.assertIn(f"backend/tests/fixtures/{name}", included, name)
        self.assertIn("docker/searxng/settings.yml", included)
        self.assertIn("docker/searxng/.env.example", included)

    def test_nothing_local_or_secret_is_shipped(self):
        included = [p.relative_to(ROOT).as_posix() for p in self.pkg.release_files(ROOT)]
        bad = [f for f in included if re.search(r"(^|/)(\.env|\.env\.(?!example$).*|dev-secret-key|__pycache__|node_modules|dist|\.venv)(/|$)|\.(db|db-wal|db-shm|pem|key|pyc|zip|log)$", f)]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
