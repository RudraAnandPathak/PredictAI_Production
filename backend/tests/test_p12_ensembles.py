"""Voting and stacking ensembles live in the existing algorithm catalogue: opt-in, closed list, trained by the same engine."""
import unittest

from app.modules.predictive.catalog import CATALOG, select
from p7_support import P7Base

VALUES = {"x1": 1.0, "x2": 0.2, "plan": "pro", "noise": 0.1}


class Catalogue(unittest.TestCase):
    def test_ensembles_are_opt_in(self):
        auto, _ = select("auto", "binary_classification", 500, 5)
        self.assertNotIn("voting_ensemble", auto)
        self.assertNotIn("stacking_ensemble", auto)
        named, skipped = select(["voting_ensemble", "stacking_ensemble"], "regression", 500, 5)
        self.assertEqual(sorted(named), ["stacking_ensemble", "voting_ensemble"])
        self.assertEqual(skipped, [])

    def test_ensembles_have_no_free_parameters_to_tune(self):
        for k in ("voting_ensemble", "stacking_ensemble"):
            self.assertEqual(dict(CATALOG[k].params), {})
            self.assertFalse(CATALOG[k].auto)


class Training(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.voting = cls.train(algorithms=["voting_ensemble"])
        cls.stacking = cls.train(algorithms=["stacking_ensemble"])

    def test_both_ensembles_train_and_predict_with_probabilities(self):
        for mid, key in ((self.voting, "voting_ensemble"), (self.stacking, "stacking_ensemble")):
            m = self.ws.predictive.get_model(self.alice, mid)
            self.assertEqual(m["algorithm"], key)
            out = self.ws.predictive.predict(self.alice, mid, VALUES)
            self.assertIn(out["prediction"]["label"], [str(c) for c in m.get("classes", [out["prediction"]["label"]])])
            self.assertAlmostEqual(sum(p["probability"] for p in out["probabilities"]), 1.0, places=2)

    def test_explanation_uses_permutation_importance_and_does_not_claim_native_importance(self):
        imp = self.ws.predictive.report(self.alice, self.voting)["importance"]
        self.assertTrue(imp["permutation"]["available"] if isinstance(imp.get("permutation"), dict) and "available" in imp["permutation"] else imp.get("permutation"))
        self.assertFalse((imp.get("native") or {}).get("available", False))
        pdp = self.ws.predictive.partial_dependence(self.alice, self.voting, "x1")
        self.assertTrue(pdp)

    def test_regression_ensembles_fit_and_predict(self):
        import numpy as np
        rng = np.random.default_rng(0)
        X = rng.normal(size=(120, 3))
        y = X[:, 0] * 2 + rng.normal(0, 0.1, 120)
        for k in ("voting_ensemble", "stacking_ensemble"):
            est = CATALOG[k].build("regression", 1, None).fit(X, y)
            self.assertLess(float(np.mean(np.abs(est.predict(X) - y))), 0.5)


if __name__ == "__main__":
    unittest.main()
