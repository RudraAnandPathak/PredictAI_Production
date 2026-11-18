"""Guards the Phase 11 documents against drifting from what exists: required documents, 14 runbooks with the five required parts, the
release-gate table (16 areas, allowed words, honest totals, nothing Blocked/Not tested described as Passed), the combined register, and pilot results."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "docs" / "phase11"
REQUIRED = ["ARCHITECTURE_STATUS", "REQUIREMENTS_REGISTER", "REQUIREMENTS_COVERAGE", "INSTALLATION_AND_CONFIGURATION", "SECURITY_REVIEW", "PERFORMANCE_BASELINE",
            "ACCESSIBILITY_REVIEW", "BACKUP_RESTORE", "MONITORING", "RUNBOOKS", "INCIDENT_RESPONSE", "RELEASE_AND_ROLLBACK", "PILOT_GUIDE", "PILOT_RESULTS",
            "KNOWN_LIMITATIONS", "DEFERRED_DECISIONS", "ENVIRONMENT_VERIFICATION", "FAILURE_RECOVERY", "RELEASE_GATES"]
AREAS = ["Functionality", "Performance", "Accessibility", "Security", "Identity", "Data", "AI and research", "Model execution", "Development", "Storage",
         "Monitoring", "Incident response", "Release", "Rollback", "Documentation", "Pilot"]
WORDS = {"Passed", "Passed with limitations", "Blocked", "Failed", "Not tested"}


class Documents(unittest.TestCase):
    def test_every_required_document_exists_and_is_not_empty(self):
        for name in REQUIRED:
            p = D / f"{name}.md"
            self.assertTrue(p.exists() and len(p.read_text()) > 400, name)

    def test_fourteen_runbooks_each_with_the_five_required_parts(self):
        text = (D / "RUNBOOKS.md").read_text()
        parts = re.split(r"^## (\d+)\. ", text, flags=re.M)[1:]
        numbers = parts[0::2]; bodies = parts[1::2]
        self.assertEqual(numbers, [str(i) for i in range(1, 15)])
        for n, body in zip(numbers, bodies):
            for heading in ("Identify", "Inspect", "Limit", "Recover", "Verify"):
                self.assertIn(f"**{heading}", body, f"runbook {n} lacks {heading}")   # maintenance mode (14) uses When/Do/Check/Leave
        titles = [t.split("\n", 1)[0] for t in bodies]
        for needle in ("Backend unavailable", "Database unavailable", "Storage failure", "Queue backlog", "Worker crash", "AI provider unavailable", "Web search unavailable",
                       "Failed report export", "Failed model execution", "Failed backup", "Failed restoration", "Suspected unauthorized access", "Development sandbox failure",
                       "Maintenance mode"):
            self.assertTrue(any(needle in t for t in titles), needle)

    def test_incident_categories_all_point_to_a_runbook(self):
        text = (D / "INCIDENT_RESPONSE.md").read_text()
        for c in ("Authentication failure", "Unauthorized access", "Data leakage", "Data corruption", "Failed backup", "Failed restore", "Database failure", "Storage failure",
                  "Worker failure", "Queue failure", "Research-provider outage", "AI-provider outage", "Model execution failure", "Report export failure", "Resource exhaustion",
                  "Development sandbox failure", "Dependency vulnerability"):
            self.assertIn(c, text)
        for field in ("Incident ID", "Severity", "Detected", "Affected component", "Affected operations", "Current status", "Responder", "Safe diagnostics", "Mitigation",
                      "Recovery", "Closure", "Follow-up"):
            self.assertIn(field, text)


class Gates(unittest.TestCase):
    def rows(self):
        return re.findall(r"^\| (\d+) \| ([^|]+) \| \*\*([^*]+)\*\* \|(.*)$", (D / "RELEASE_GATES.md").read_text(), re.M)

    def test_sixteen_areas_each_with_an_allowed_status(self):
        rows = self.rows()
        self.assertEqual([r[1].strip() for r in rows], AREAS)
        for r in rows:
            self.assertIn(r[2], WORDS)

    def test_totals_line_matches_the_table(self):
        rows = self.rows(); n = lambda w: sum(r[2] == w for r in rows)
        expected = f"**Passed {n('Passed')} · Passed with limitations {n('Passed with limitations')} · Blocked {n('Blocked')} · Failed {n('Failed')} · Not tested {n('Not tested')}**"
        self.assertIn(expected, (D / "RELEASE_GATES.md").read_text())

    def test_the_gates_that_cannot_honestly_pass_are_not_passed(self):
        status = {r[1].strip(): r[2] for r in self.rows()}
        self.assertEqual(status["Development"], "Blocked")
        self.assertEqual(status["Accessibility"], "Not tested")
        self.assertEqual(status["Pilot"], "Not tested")
        self.assertNotIn("Passed", [s for s in status.values()], "nothing has been verified in the target environment")

    def test_untrusted_execution_is_stated_disabled_and_production_is_not_recommended(self):
        text = (D / "RELEASE_GATES.md").read_text()
        self.assertRegex(text, r"(?i)untrusted multi-user code execution\*?\*? remains \*\*disabled\*\*")
        self.assertRegex(text, r"\*\*Public production release\*\* \| \*\*No\*\*")


class Evidence(unittest.TestCase):
    def test_the_combined_register_is_current_and_cites_only_real_tests(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_register", D / "build_register.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        rows = mod.build(); cov = mod.coverage(rows)
        self.assertEqual(cov["cites_missing_test_file"], {})
        for name, text in (("REQUIREMENTS_REGISTER.md", mod.render(rows, cov)), ("REQUIREMENTS_COVERAGE.md", mod.render_cov(cov))):
            self.assertEqual((D / name).read_text(), text, f"{name} is stale: run python3 docs/phase11/build_register.py")
        self.assertGreater(cov["total"], 400)
        self.assertEqual(cov["by_status"]["Broken"], 0)

    def test_nothing_unverified_is_promoted_in_the_register(self):
        rows = json.loads((D / "requirements_combined.json").read_text())["rows"]
        for r in rows:
            if "unverified" in r["original_status"].lower() or "blocked" in r["original_status"].lower():
                self.assertNotEqual(r["status"], "Complete and verified", r["id"])

    def test_pilot_results_record_g_as_blocked_and_every_other_scenario_without_problems(self):
        data = json.loads((D / "pilot_results.json").read_text())["results"]
        by = {r["scenario"]: r for r in data}
        self.assertEqual(sorted(by), list("ABCDEFGH"))
        self.assertEqual(by["G"]["gate"], "BLOCKED for untrusted execution")
        self.assertTrue(all(r["problems"] == 0 for r in data), [r["scenario"] for r in data if r["problems"]])
        self.assertTrue(all(s["ran"] > 0 for r in data for s in r["steps"]), "a pilot step found no tests")

    def test_performance_json_has_no_errors(self):
        d = json.loads((D / "perf_baseline.json").read_text())
        for v in d["api_concurrency"].values():
            self.assertTrue(set(v["status_codes"]) <= {"200", "201"})


if __name__ == "__main__":
    unittest.main()
