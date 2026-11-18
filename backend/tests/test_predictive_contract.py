"""The shape of what the modelling API returns, checked against real examples shared with the frontend (shared/contracts/ml-model-report.*.json).

Those files are real server responses, trimmed. If a field the interface reads changes name or type — a string that becomes an object, a list that
becomes a number — this test fails, and so does the frontend test that reads the same files. Adding new fields is always allowed."""
import json
import time
import unittest
from pathlib import Path

import analytics_support as sup
import ml_support as S
from support import Harness
from test_workspace_uploads import upload

CONTRACTS = Path(__file__).resolve().parents[2] / "shared" / "contracts"
CASES = {
    "binary": (S.classification_frame(300), "bought", ["logistic_regression", "decision_tree"]),
    "multiclass": (S.multiclass_frame(300), "grade", ["logistic_regression", "decision_tree"]),
    "regression": (S.regression_frame(300), "price", ["ridge", "decision_tree"]),
}


def kind(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def differences(expected, actual, path="$"):
    """Every place where `actual` lacks something `expected` has, or holds it as another kind of value. Extra fields in `actual` are fine."""
    found = []
    if expected is None:                                        # the example had no value here: any value is acceptable
        return found
    if kind(expected) != kind(actual):
        return [f"{path}: expected {kind(expected)}, got {kind(actual)}"]
    if isinstance(expected, dict):
        for key, value in expected.items():
            if key.startswith("_"):
                continue
            if key not in actual:
                found.append(f"{path}.{key}: missing")
            else:
                found += differences(value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list) and expected:
        if not actual:
            found.append(f"{path}: the example has items, the response has none")
        else:
            found += differences(expected[0], actual[0], f"{path}[0]")
    return found


class ContractHelpers(unittest.TestCase):
    def test_a_changed_type_is_reported(self):
        self.assertEqual(differences({"problem": "regression"}, {"problem": {"type": "regression"}}), ["$.problem: expected string, got object"])

    def test_a_missing_field_is_reported(self):
        self.assertEqual(differences({"a": {"b": 1}}, {"a": {}}), ["$.a.b: missing"])

    def test_a_list_that_lost_its_items_is_reported(self):
        self.assertEqual(len(differences({"xs": [{"v": 1}]}, {"xs": []})), 1)

    def test_extra_fields_and_unknown_example_values_are_fine(self):
        self.assertEqual(differences({"a": 1, "n": None, "_note": "x"}, {"a": 2, "n": {"anything": True}, "extra": 1}), [])


class ReportContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=16 << 20, ml_isolation="inprocess")
        cls.alice, _ = cls.h.make_user("alice@example.com")
        cls.ws = cls.h.workspace()
        cls.p = cls.ws.predictive
        project = cls.ws.projects.create(cls.alice, "Contract")["id"]
        cls.real = {}
        for name, (frame, target, algorithms) in CASES.items():
            asset = upload(cls.ws, cls.alice, project, f"{name}.csv", sup.csv_of(frame))["asset"]
            job = cls.p.start_training(cls.alice, asset["id"], {"target": target, "algorithms": algorithms, "time_budget_s": 60, "validation": {"cv_folds": 3}}, label=name)
            t0 = time.time()
            while time.time() - t0 < 150:
                done = cls.p.get_job(cls.alice, job["id"])
                if done["status"] in ("completed", "failed", "cancelled"):
                    break
                time.sleep(0.1)
            assert done["status"] == "completed", done
            model = cls.p.get_model(cls.alice, done["model"]["id"])
            cls.real[name] = {"model": model, "report": cls.p.report(cls.alice, model["id"])}

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def example(self, name):
        return json.loads((CONTRACTS / f"ml-model-report.{name}.example.json").read_text())

    def test_the_binary_report_and_model_match_the_shared_example(self):
        self.assertEqual(differences(self.example("binary"), json.loads(json.dumps(self.real["binary"], default=str))), [])

    def test_the_multiclass_report_and_model_match_the_shared_example(self):
        self.assertEqual(differences(self.example("multiclass"), json.loads(json.dumps(self.real["multiclass"], default=str))), [])

    def test_the_regression_report_and_model_match_the_shared_example(self):
        self.assertEqual(differences(self.example("regression"), json.loads(json.dumps(self.real["regression"], default=str))), [])

    def test_the_problem_is_an_object_with_a_type_the_interface_can_read(self):
        for name, expected in (("binary", "binary_classification"), ("multiclass", "multiclass_classification"), ("regression", "regression")):
            problem = self.real[name]["report"]["problem"]
            self.assertIsInstance(problem, dict, name)
            self.assertEqual(problem["type"], expected, name)
            self.assertEqual(self.real[name]["model"]["problem_type"], expected, name)

    def test_the_examples_hold_no_secrets_or_storage_paths(self):
        for name in CASES:
            text = (CONTRACTS / f"ml-model-report.{name}.example.json").read_text()
            for forbidden in ("model_key", "report_key", "background_key", "storage_key", "/m/", "password", "secret"):
                self.assertNotIn(forbidden, text, (name, forbidden))


if __name__ == "__main__":
    unittest.main()
