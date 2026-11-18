"""The Phase 6 requirements register must tell the truth about the repository.

It checks that every implementation path in the register exists, that every test it names exists (file, class and method), that a row is only 'Complete' when it names tests,
that the two Phase 7 deliverables are still tracked as Missing, that the spec's twelve modules are all represented, and that the generated files are in step with their source.
Run from backend/:  python -m unittest test_phase6_register"""
import glob
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"Complete", "Partial", "Missing", "Blocked", "Unverified"}
MODULE_PREFIXES = {"RES", "WEB", "SRC", "EVB", "CTX", "RET", "MEM", "WLD", "EVT", "INF", "PRV", "QC"}


def load_builder():
    spec = importlib.util.spec_from_file_location("build_register", ROOT / "docs/phase6/build_register.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve(token: str):
    token = token.strip()
    if token.startswith("frontend "):
        token = "frontend/src/" + token[len("frontend "):]
    for prefix in ("", "backend/app/modules/", "backend/app/", "frontend/src/"):
        hits = glob.glob(str(ROOT / (prefix + token)))
        if hits:
            return hits
    return []


def declared(path: Path, cls: str, method: str = None) -> bool:
    text = path.read_text(encoding="utf-8")
    if not re.search(rf"^class {re.escape(cls)}\b", text, re.M):
        return False
    if method is None:
        return True
    block = text.split(f"class {cls}", 1)[1]
    nxt = re.search(r"^class ", block, re.M)
    block = block[: nxt.start()] if nxt else block
    return re.search(rf"def {re.escape(method)}\(", block) is not None


class Register(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()
        cls.rows = json.loads((ROOT / "docs/phase6/requirements.json").read_text(encoding="utf-8"))
        cls.evidence = cls.builder.EVIDENCE

    def test_every_requirement_has_the_fields_the_spec_asks_for(self):
        for r in self.rows:
            for key in ("id", "description", "source", "implementation", "acceptance", "tests", "status"):
                self.assertIn(key, r, r.get("id"))
            self.assertTrue(r["source"]["phase6_spec"] and r["source"]["master_document"], r["id"])
            self.assertIn(r["status"], STATUSES, r["id"])
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
        self.assertEqual(missing, [], "tests named in the register that do not exist")

    def test_complete_needs_tests_and_missing_has_none(self):
        for r in self.rows:
            if r["status"] == "Complete":
                self.assertTrue(r["tests"], f"{r['id']} is Complete but names no test")
            if r["status"] in ("Missing", "Blocked", "Unverified"):
                self.assertTrue(r["note"], f"{r['id']} is {r['status']} and must say why")
            if r["status"] == "Partial":
                self.assertTrue(r["note"], f"{r['id']} is Partial and must say what is missing")

    def test_the_phase_7_deliverables_are_still_tracked_as_missing(self):
        by = {r["id"]: r for r in self.rows}
        self.assertEqual(by["OUT-01"]["status"], "Missing"); self.assertIn("Exam Intelligence", by["OUT-01"]["description"])
        self.assertEqual(by["OUT-02"]["status"], "Missing"); self.assertIn("Model Registry", by["OUT-02"]["description"])
        outstanding = (ROOT / "docs/phase6/OUTSTANDING.md").read_text(encoding="utf-8")
        for needle in ("OUT-01", "Exam Intelligence", "past-paper", "mock examinations", "OUT-02", "Model Registry", "rollback"):
            self.assertIn(needle, outstanding)

    def test_every_spec_module_is_represented(self):
        self.assertTrue(MODULE_PREFIXES <= {r["id"].split("-")[0] for r in self.rows})

    def test_every_row_has_evidence_and_nothing_extra_is_listed(self):
        self.assertEqual(sorted(self.evidence), sorted(r["id"] for r in self.rows))

    def test_the_generated_files_are_in_step_with_their_source(self):
        before = (ROOT / "docs/phase6/requirements.json").read_text(encoding="utf-8"), (ROOT / "docs/phase6/REQUIREMENTS_REGISTER.md").read_text(encoding="utf-8")
        built = [
            {"id": rid, "description": desc, "source": {"phase6_spec": self.builder.SPEC[rid.split("-")[0]][0], "master_document": self.builder.SPEC[rid.split("-")[0]][1]}, "implementation": impl, "acceptance": acc,
             "tests": self.evidence[rid]["tests"], "status": self.evidence[rid]["status"], "note": self.evidence[rid]["note"]} for rid, desc, impl, acc in self.builder.ROWS]
        self.assertEqual(json.loads(before[0]), built, "run: python docs/phase6/build_register.py")
        totals = {}
        for r in built:
            totals[r["status"]] = totals.get(r["status"], 0) + 1
        self.assertIn("Totals: " + ", ".join(f"{k} {v}" for k, v in sorted(totals.items())), before[1])

    def test_no_row_claims_a_test_run_that_the_register_cannot_show(self):
        for r in self.rows:
            if r["status"] == "Complete":
                self.assertNotIn("TODO", r["note"].upper())


if __name__ == "__main__":
    unittest.main()
