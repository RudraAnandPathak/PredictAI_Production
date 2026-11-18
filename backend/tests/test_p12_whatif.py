"""What-if scenarios: every number comes from the normal prediction path; nothing is saved; bad scenarios are refused."""
from app.core.errors import AppError
from p7_support import P7Base, code_of

VALUES = {"x1": 1.0, "x2": 0.2, "plan": "pro", "noise": 0.1}


class WhatIf(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mid = cls.train()

    def test_changes_are_compared_with_the_baseline_and_not_saved(self):
        before = len(self.ws.predictive.list_predictions(self.alice, self.mid)["items"])
        r = self.ws.predictive.what_if(self.alice, self.mid, VALUES, [{"name": "High x1", "changes": {"x1": 5.0}}, {"changes": {"x1": -5.0, "plan": "free"}}])
        self.assertEqual([s["name"] for s in r["scenarios"]], ["High x1", "Scenario 2"])
        base = self.ws.predictive.predict(self.alice, self.mid, VALUES, explain_local=False, save=False)
        self.assertEqual(r["baseline"]["prediction"], base["prediction"])
        direct = self.ws.predictive.predict(self.alice, self.mid, {**VALUES, "x1": 5.0}, explain_local=False, save=False)
        self.assertEqual(r["scenarios"][0]["prediction"], direct["prediction"])
        self.assertIn("probability_change", r["scenarios"][0])
        self.assertAlmostEqual(r["scenarios"][0]["probability_change"], round(direct["positive"]["probability"] - base["positive"]["probability"], 5), places=5)
        self.assertIn("does not show what would happen in the real world", r["limits"][0])
        self.assertEqual(before, len(self.ws.predictive.list_predictions(self.alice, self.mid)["items"]))

    def test_unknown_inputs_and_bad_shapes_are_refused(self):
        w = self.ws.predictive.what_if
        self.assertEqual(code_of(w, self.alice, self.mid, VALUES, [{"changes": {"nope": 1}}])[0], "UNKNOWN_INPUT")
        self.assertEqual(code_of(w, self.alice, self.mid, VALUES, [])[1], 422)
        self.assertEqual(code_of(w, self.alice, self.mid, VALUES, [{"changes": {"x1": 1}}] * 6)[1], 422)
        self.assertEqual(code_of(w, self.alice, self.mid, VALUES, [{"changes": {}}])[1], 422)
        self.assertEqual(code_of(w, self.alice, self.mid, {}, [{"changes": {"x1": 1}}])[1], 422)

    def test_out_of_range_changes_are_flagged_not_hidden(self):
        r = self.ws.predictive.what_if(self.alice, self.mid, VALUES, [{"changes": {"x1": 1e9}}])
        self.assertTrue(r["scenarios"][0]["input_issues"])

    def test_another_user_gets_a_404(self):
        self.assertEqual(code_of(self.ws.predictive.what_if, self.bob, self.mid, VALUES, [{"changes": {"x1": 1}}])[1], 404)
