"""Security of the predictive-modelling features: isolation between people and projects, permissions, a closed configuration vocabulary (no code or paths
can be supplied), hostile data and names, spreadsheet-formula safety in downloads, deletion and purge, audit trail without data values, and the training sandbox."""
import ast
import csv
import io
import json
import pathlib
import unittest
from dataclasses import replace
from unittest import mock

import analytics_support as sup
import ml_support as S
import test_predictive_service as svc
from app.core.errors import AppError
from app.modules.predictive.config import DEFAULT_CONFIG
from test_workspace_uploads import upload

code_of, wait_job, VALUES, FAST = svc.code_of, svc.wait_job, svc.VALUES, svc.FAST


class Shared(svc.Fixture):
    """One trained model owned by Alice, one prediction and one batch for it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        job = cls.p.start_training(cls.alice, cls.asset["id"], FAST, label="Alice churn model")
        cls.job_id = job["id"]
        cls.model = wait_job(cls.p, cls.alice, job["id"])["model"]
        cls.single = cls.p.predict(cls.alice, cls.model["id"], VALUES)
        cls.batch = cls.p.predict_batch(cls.alice, cls.model["id"], cls.asset["id"])


class Isolation(Shared):
    def calls(self, who):
        m, a, p = self.model["id"], self.asset["id"], self.p
        return {
            "schema": lambda: p.schema(who, a), "assess": lambda: p.assess(who, a, FAST), "start": lambda: p.start_training(who, a, FAST), "list_jobs": lambda: p.list_jobs(who, a),
            "get_job": lambda: p.get_job(who, self.job_id), "cancel": lambda: p.cancel_job(who, self.job_id), "list_models_for_asset": lambda: p.list_models(who, a),
            "get_model": lambda: p.get_model(who, m), "rename": lambda: p.rename_model(who, m, "x"), "report": lambda: p.report(who, m), "delete": lambda: p.delete_model(who, m),
            "predict": lambda: p.predict(who, m, VALUES), "batch": lambda: p.predict_batch(who, m, a), "history": lambda: p.list_predictions(who, m),
            "get_prediction": lambda: p.get_prediction(who, self.single["prediction_id"]), "download": lambda: p.download_prediction(who, self.batch["id"]),
            "pdp": lambda: p.partial_dependence(who, m, "x1"), "compare": lambda: p.compare_models(who, [m, m[::-1]]),
        }

    def test_another_person_gets_the_same_not_found_for_everything_and_learns_nothing(self):
        outcomes = {}
        for name, call in self.calls(self.bob).items():
            outcomes[name] = code_of(call)
        self.assertEqual(set(outcomes.values()), {("NOT_FOUND", 404)}, outcomes)

    def test_a_foreign_id_and_an_id_that_does_not_exist_are_indistinguishable(self):
        def message(fn):
            try:
                fn()
            except AppError as e:
                return e.code, e.status_code, e.message
            raise AssertionError("expected an error")

        real = message(lambda: self.p.get_model(self.bob, self.model["id"]))
        fake = message(lambda: self.p.get_model(self.bob, "0" * 32))
        self.assertEqual(real, fake)
        self.assertEqual(message(lambda: self.p.get_job(self.bob, self.job_id)), message(lambda: self.p.get_job(self.bob, "0" * 32)))
        self.assertEqual(message(lambda: self.p.get_prediction(self.bob, self.batch["id"])), message(lambda: self.p.get_prediction(self.bob, "0" * 32)))

    def test_nothing_of_alices_appears_in_bobs_lists(self):
        self.assertEqual(self.p.list_models(self.bob)["total"], 0)
        self.assertGreaterEqual(self.p.list_models(self.alice)["total"], 1)
        self.assertEqual(code_of(self.p.list_models, self.bob, self.asset["id"]), ("NOT_FOUND", 404))

    def test_nothing_was_changed_by_the_refused_attempts(self):
        before = self.p.get_model(self.alice, self.model["id"])
        for call in self.calls(self.bob).values():
            try:
                call()
            except AppError:
                pass
        after = self.p.get_model(self.alice, self.model["id"])
        self.assertEqual(before["name"], after["name"])
        self.assertEqual(self.p.list_predictions(self.alice, self.model["id"])["total"], 2)
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM ml_jobs WHERE owner_user_id = ?", (self.bob.user_id,))[0]["n"], 0)

    def test_a_model_is_only_ever_applied_to_data_of_its_own_project(self):
        other_project = self.ws.projects.create(self.alice, "Elsewhere")["id"]
        elsewhere = upload(self.ws, self.alice, other_project, "same_columns.csv", svc.sup.csv_of(S.classification_frame(80, seed=3)))["asset"]
        self.assertEqual(code_of(self.p.predict_batch, self.alice, self.model["id"], elsewhere["id"]), ("NOT_FOUND", 404))
        bobs = upload(self.ws, self.bob, self.ws.projects.create(self.bob, "Bob's")["id"], "b.csv", svc.sup.csv_of(S.classification_frame(80, seed=4)))["asset"]
        self.assertEqual(code_of(self.p.predict_batch, self.alice, self.model["id"], bobs["id"]), ("NOT_FOUND", 404))
        self.assertEqual(code_of(self.p.predict_batch, self.bob, self.model["id"], bobs["id"]), ("NOT_FOUND", 404))

    def test_malformed_identifiers_are_just_not_found(self):
        for bad in ("../../etc/passwd", "p/x/a/y", "", " ", "0" * 300, "%00", "model.joblib", None, 7, {"id": 1}, ["a"]):
            for fn in (self.p.get_model, self.p.report, self.p.delete_model, self.p.get_job, self.p.get_prediction):
                try:
                    fn(self.alice, bad)
                except AppError as e:
                    self.assertIn(e.status_code, (404, 422), (fn.__name__, bad))
                else:
                    self.fail(f"{fn.__name__}({bad!r}) was accepted")

    def test_comparing_never_reveals_whether_someone_elses_model_exists(self):
        mine = self.model["id"]
        self.assertEqual(code_of(self.p.compare_models, self.bob, [mine, mine[::-1]]), ("NOT_FOUND", 404))
        self.assertEqual(code_of(self.p.compare_models, self.alice, [mine, "0" * 32]), ("NOT_FOUND", 404))


class Permissions(Shared):
    def test_a_person_without_data_permission_can_do_nothing(self):
        nobody = replace(self.alice, permissions=frozenset())
        m, a = self.model["id"], self.asset["id"]
        for name, call in {"assess": lambda: self.p.assess(nobody, a, FAST), "train": lambda: self.p.start_training(nobody, a, FAST), "jobs": lambda: self.p.list_jobs(nobody, a),
                           "get_job": lambda: self.p.get_job(nobody, self.job_id), "model": lambda: self.p.get_model(nobody, m), "report": lambda: self.p.report(nobody, m),
                           "predict": lambda: self.p.predict(nobody, m, VALUES), "batch": lambda: self.p.predict_batch(nobody, m, a), "delete": lambda: self.p.delete_model(nobody, m),
                           "list": lambda: self.p.list_models(nobody, a), "download": lambda: self.p.download_prediction(nobody, self.batch["id"])}.items():
            self.assertEqual(code_of(call)[1], 403, name)

    def test_extended_limits_need_the_extended_compute_permission(self):
        big = {"target": "bought", "algorithms": ["logistic_regression"], "hpo": {"method": "random", "n_iter": 31}}
        self.assertEqual(code_of(self.p.assess, self.alice, self.asset["id"], big)[1], 422)


class ClosedVocabulary(Shared):
    """The configuration is a fixed set of named options with bounded values. Nothing the caller sends is ever executed, imported, compiled, joined into a path or a query."""

    HOSTILE = [
        {"target": "bought", "algorithms": ["sklearn.ensemble.RandomForestClassifier"]},
        {"target": "bought", "algorithms": ["__import__('os').system('id')"]},
        {"target": "bought", "algorithms": [["random_forest"]]},
        {"target": "bought", "algorithms": {"random_forest": 1}},
        {"target": "bought; DROP TABLE ml_models;--"},
        {"target": "../../etc/passwd"},
        {"target": "bought", "features": {"exclude": ["x'; DROP TABLE users;--"]}},
        {"target": "bought", "features": {"include": ["__class__"]}},
        {"target": "bought", "features": {"overrides": {"x1": {"role": "__import__('os')"}}}},
        {"target": "bought", "features": {"overrides": {"x1": {"callable": "os.system"}}}},
        {"target": "bought", "preprocessing": {"scaling": "lambda x: x"}},
        {"target": "bought", "preprocessing": {"imputer": "sklearn.impute.SimpleImputer"}},
        {"target": "bought", "validation": {"cv_strategy": "../../x"}},
        {"target": "bought", "validation": {"time_column": "x1'; --"}},
        {"target": "bought", "hpo": {"method": "grid", "param_grids": {"logistic_regression": {"__class__": [1]}}}},
        {"target": "bought", "hpo": {"method": "grid", "param_grids": {"logistic_regression": {"C": ["__import__('os')"]}}}},
        {"target": "bought", "hpo": {"method": "grid", "param_grids": {"logistic_regression": {"solver": ["saga"], "n_jobs": [-1]}}}},
        {"target": "bought", "hpo": {"method": "grid", "param_grids": {"os.system": {"C": [1]}}}},
        {"target": "bought", "primary_metric": "__import__('os')"},
        {"target": "bought", "positive_label": {"$ne": None}},
        {"target": "bought", "seed": "1; import os"},
        {"target": "bought", "n_jobs": -1},
        {"target": "bought", "memory_mb": 10 ** 9},
        {"target": "bought", "time_budget_s": 10 ** 9},
        {"target": "bought", "exec": "print(1)"},
        {"target": "bought", "pipeline": [{"step": "os.system"}]},
        {"target": "bought", "__proto__": {"x": 1}},
        {"target": ["bought"]},
        {"target": {"name": "bought"}},
        {"target": 5},
    ]

    def test_hostile_configurations_are_refused_before_any_job_exists(self):
        before = self.h.rows("SELECT COUNT(*) AS n FROM ml_jobs")[0]["n"]
        self.addCleanup(self.h.clock.advance, seconds=3700)                        # leave a fresh rate-limit window for the other tests in this class
        for cfg in self.HOSTILE:
            self.h.clock.advance(seconds=200)                                      # this test is about validation, not about the hourly training limit
            for call in (lambda c=cfg: self.p.start_training(self.alice, self.asset["id"], c), lambda c=cfg: self.p.assess(self.alice, self.asset["id"], c)):
                try:
                    call()
                except AppError as e:
                    self.assertEqual(e.status_code, 422, (cfg, e.code))
                    self.assertNotIn("Traceback", e.message)
                else:
                    self.fail(f"accepted {cfg!r}")
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM ml_jobs")[0]["n"], before)

    def test_every_setting_survives_garbage_values_with_a_clean_422_never_a_crash(self):
        """Fuzz: every top-level and nested setting gets values of every wrong shape. Only a validation error is acceptable."""
        from app.modules.predictive.config import parse_config

        _, types = S.typed(S.classification_frame(200))
        garbage = [None, [], [[]], [{}], {}, {"a": 1}, {"$ne": 1}, "", " ", "x" * 5000, "\x00", -1, 0, 10 ** 30, 1.5, float("nan"), float("inf"), True, [1, 2, 3], ["bought"], [None], [True]]
        paths = [[k] for k in DEFAULT_CONFIG] + [[k, n] for k, v in DEFAULT_CONFIG.items() if isinstance(v, dict) for n in v]
        for path in paths:
            for bad in garbage:
                raw = {"target": "bought"}
                node = raw
                for key in path[:-1]:
                    node = node.setdefault(key, {})
                node[path[-1]] = bad
                try:
                    parse_config(raw, types)
                except AppError as e:
                    self.assertEqual(e.status_code, 422, (path, bad))
                except Exception as e:                                                    # noqa: BLE001 - the point of the test
                    self.fail(f"{path} = {bad!r} raised {type(e).__name__}: {e}")

    def test_the_stored_configuration_has_only_known_keys_with_normalised_values(self):
        row = self.h.rows("SELECT config FROM ml_jobs WHERE id = ?", (self.job_id,))[0]
        stored = json.loads(row["config"])
        self.assertTrue(set(stored) <= set(DEFAULT_CONFIG), set(stored) - set(DEFAULT_CONFIG))
        for k, v in DEFAULT_CONFIG.items():
            if isinstance(v, dict):
                self.assertTrue(set(stored[k]) <= set(v), (k, set(stored[k]) - set(v)))

    def test_text_the_person_writes_is_data_it_is_cleaned_and_never_interpreted(self):
        nasty = "Ev‮il\x00 <img src=x onerror=alert(1)> \r\nX-Injected: 1"
        m = self.train(label=nasty)
        shown = self.p.get_model(self.alice, m["id"])
        for text in (shown["name"], self.p.get_job(self.alice, m["job_id"])["label"]):
            self.assertNotIn("\x00", text)
            self.assertNotIn("‮", text)
            self.assertNotIn("\n", text)
            self.assertNotIn("\r", text)
        renamed = self.p.rename_model(self.alice, m["id"], "  a‮\x07b\nc  ")
        self.assertEqual(renamed["name"], "ab c")            # control and direction-override characters are removed; a line break becomes a space
        self.assertEqual(code_of(self.p.rename_model, self.alice, m["id"], "‮\x00  "), ("VALIDATION_ERROR", 422))
        self.assertEqual(code_of(self.p.rename_model, self.alice, m["id"], 123)[1], 422)
        self.assertEqual(code_of(self.p.rename_model, self.alice, m["id"], "x" * 121)[1], 422)

    def test_every_filename_a_download_can_carry_is_made_from_ids_only(self):
        name, ctype, _ = self.p.download_prediction(self.alice, self.batch["id"])
        self.assertRegex(name, r"^predictions-[0-9a-f]{8}\.csv$")
        self.assertEqual(ctype, "text/csv; charset=utf-8")

    def test_hostile_column_names_and_values_in_the_dataset_stay_data(self):
        r = S.classification_frame(300, seed=21)
        r = r.rename(columns={"plan": "=HYPERLINK(\"http://evil\")", "noise": "a'; DROP TABLE ml_models;--"})
        r[r.columns[3]] = r[r.columns[3]].map({"basic": "=cmd|' /C calc'!A0", "plus": "+SUM(1)", "pro": "@x"})
        asset = upload(self.ws, self.alice, self.project, "hostile.csv", sup.csv_of(r))["asset"]
        job = self.p.start_training(self.alice, asset["id"], {"target": "bought", "algorithms": ["logistic_regression"], "time_budget_s": 60})
        done = wait_job(self.p, self.alice, job["id"])
        self.assertEqual(done["status"], "completed", done.get("error"))
        batch = self.p.predict_batch(self.alice, done["model"]["id"], asset["id"])
        _, _, data = self.p.download_prediction(self.alice, batch["id"])
        rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
        for line in rows:
            for cell in line:
                if cell and cell[0] in "=+@" or cell.startswith(("-",)) and not _is_number(cell):
                    self.fail(f"a downloaded cell would be read as a formula: {cell!r}")
        self.assertTrue(any(c.startswith("'=") or c.startswith("'+") or c.startswith("'@") for line in rows[1:] for c in line))
        self.assertTrue(self.h.rows("SELECT COUNT(*) AS n FROM ml_models")[0]["n"] >= 1)             # the table named in the column header still exists


def _is_number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


class DeletionAndPurge(Shared):
    def test_deleting_a_project_removes_every_modelling_row_and_file(self):
        project = self.ws.projects.create(self.alice, "Short lived")
        asset = upload(self.ws, self.alice, project["id"], "tmp.csv", sup.csv_of(S.classification_frame(300, seed=5)))["asset"]
        m = wait_job(self.p, self.alice, self.p.start_training(self.alice, asset["id"], FAST)["id"])["model"]
        self.p.predict(self.alice, m["id"], VALUES)
        self.p.predict_batch(self.alice, m["id"], asset["id"])
        self.assertTrue(self.h.storage.list_prefix(f"p/{project['id']}"))
        self.ws.projects.delete(self.alice, project["id"], "Short lived")
        for table in ("ml_jobs", "ml_models", "ml_predictions"):
            self.assertEqual(self.h.rows(f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = ?", (project["id"],))[0]["n"], 0, table)
        self.assertEqual(self.h.storage.list_prefix(f"p/{project['id']}"), [])
        self.assertEqual(code_of(self.p.get_model, self.alice, m["id"])[1], 404)
        self.assertGreaterEqual(self.p.list_models(self.alice)["total"], 1)                           # other projects are untouched

    def test_deleting_an_account_removes_models_jobs_predictions_and_files_but_not_other_peoples(self):
        carol, _ = self.h.make_user("carol@example.com")
        proj = self.ws.projects.create(carol, "Carol")["id"]
        asset = upload(self.ws, carol, proj, "c.csv", sup.csv_of(S.classification_frame(300, seed=8)))["asset"]
        m = wait_job(self.p, carol, self.p.start_training(carol, asset["id"], FAST)["id"])["model"]
        self.p.predict(carol, m["id"], VALUES)
        self.ws.governance.delete_all_for_user(carol.user_id, carol.user_id)
        for table in ("ml_jobs", "ml_models", "ml_predictions"):
            self.assertEqual(self.h.rows(f"SELECT COUNT(*) AS n FROM {table} WHERE owner_user_id = ?", (carol.user_id,))[0]["n"], 0, table)
        self.assertEqual(self.h.storage.list_prefix(f"p/{proj}"), [])
        self.assertEqual(self.p.get_model(self.alice, self.model["id"])["id"], self.model["id"])

    def test_the_deletion_footprint_lists_the_models_that_would_go(self):
        fp = self.ws.assets.footprint(self.alice, self.asset["id"])
        kinds = {i["kind"]: i for i in fp["items"]}
        self.assertIn("models", kinds)
        self.assertGreaterEqual(kinds["models"]["objects"], 3)                                         # model file, report and the stored batch output
        self.assertGreater(kinds["models"]["bytes"], 0)

    def test_retention_expiry_removes_models_with_the_dataset(self):
        asset = upload(self.ws, self.alice, self.project, "expiring.csv", sup.csv_of(S.classification_frame(300, seed=6)))["asset"]
        m = wait_job(self.p, self.alice, self.p.start_training(self.alice, asset["id"], FAST)["id"])["model"]
        self.ws.assets.set_retention(self.alice, asset["id"], "2026-03-01")
        self.h.clock.advance(days=120)
        self.assertEqual(self.ws.governance.run_retention()["expired_assets"], 1)
        self.assertEqual(self.h.storage.list_prefix(f"p/{self.project}/a/{asset['id']}"), [])
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM ml_models WHERE asset_id = ?", (asset["id"],))[0]["n"], 0)
        self.assertEqual(code_of(self.p.get_model, self.alice, m["id"])[1], 404)

    def test_a_deleted_dataset_hides_its_models_even_before_the_purge_finishes(self):
        asset = upload(self.ws, self.alice, self.project, "hidden.csv", sup.csv_of(S.classification_frame(300, seed=7)))["asset"]
        m = wait_job(self.p, self.alice, self.p.start_training(self.alice, asset["id"], FAST)["id"])["model"]
        self.h.conn.execute("UPDATE assets SET status = 'deleting' WHERE id = ?", (asset["id"],))
        self.assertEqual(code_of(self.p.get_model, self.alice, m["id"])[1], 404)
        self.assertEqual(code_of(self.p.predict, self.alice, m["id"], VALUES)[1], 404)
        self.assertEqual(code_of(self.p.report, self.alice, m["id"])[1], 404)
        self.assertNotIn(m["id"], [x["id"] for x in self.p.list_models(self.alice)["items"]])


class AuditTrail(Shared):
    def test_modelling_actions_are_audited_without_any_data_values(self):
        self.p.predict(self.alice, self.model["id"], {**VALUES, "plan": "UNIQUE-PLAN-VALUE"}, save=False)
        self.p.report(self.alice, self.model["id"])
        events = self.h.rows("SELECT event_type, metadata, actor_user_id FROM audit_events WHERE event_type LIKE 'ml.%'")
        kinds = {e["event_type"] for e in events}
        self.assertTrue({"ml.job.queued", "ml.predicted", "ml.explained"} <= kinds, kinds)
        blob = json.dumps([dict(e) for e in events])
        for secret in ("UNIQUE-PLAN-VALUE", "Alice churn model", "customers.csv", "bought"):
            self.assertNotIn(secret, blob)

    def test_refused_requests_by_other_people_leave_no_trace_of_the_object_they_probed(self):
        before = self.h.rows("SELECT COUNT(*) AS n FROM audit_events WHERE event_type LIKE 'ml.%'")[0]["n"]
        for call in Isolation.calls(self, self.bob).values():
            code_of(call)
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM audit_events WHERE event_type LIKE 'ml.%'")[0]["n"], before)
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM audit_events WHERE event_type LIKE 'ml.%' AND actor_user_id = ?", (self.bob.user_id,))[0]["n"], 0)


class WorkerSandbox(svc.Fixture):
    isolation = "subprocess"

    def test_the_training_process_is_started_with_a_scrubbed_environment_and_isolated_interpreter(self):
        import os
        from app.modules.analytics import sandbox

        captured = {}
        real = sandbox.run_limited

        def spy(argv, **kw):
            captured["argv"], captured["env"], captured["cwd"] = list(argv), dict(kw["env"]), kw["cwd"]
            return real(argv, **kw)

        with mock.patch.dict(os.environ, {"PREDICTAI_SECRET_KEY": "super-secret-value-123", "SMTP_PASSWORD": "hunter2-hunter2", "GOOGLE_CLIENT_SECRET": "oauth-secret"}):
            with mock.patch.object(sandbox, "run_limited", spy):
                m = self.train()
        env = captured["env"]
        self.assertEqual(captured["argv"][1], "-I")                                 # isolated mode: no user site, no current-directory imports, no PYTHON* variables
        self.assertFalse([k for k in env if any(w in k.upper() for w in ("SECRET", "PASSWORD", "TOKEN", "KEY", "SMTP", "GOOGLE", "DATABASE", "AWS"))], sorted(env))
        self.assertNotIn("super-secret-value-123", json.dumps(env))
        self.assertTrue(set(env) <= {"PATH", "PYTHONHASHSEED", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "LC_ALL", "TMPDIR", "JOBLIB_MULTIPROCESSING", "LOKY_MAX_CPU_COUNT"}, sorted(env))
        rep = self.p.report(self.alice, m["id"])
        self.assertIn("scrubbed environment (no application secrets)", rep["isolation"]["limits_applied"])
        self.assertNotIn("super-secret-value-123", json.dumps(rep))

    def test_the_working_folder_of_a_job_is_removed_afterwards(self):
        import os
        from app.modules.analytics import sandbox

        seen = {}
        real = sandbox.run_limited

        def spy(argv, **kw):
            seen["cwd"] = kw["cwd"]
            return real(argv, **kw)

        with mock.patch.object(sandbox, "run_limited", spy):
            self.train()
        self.assertTrue(seen["cwd"])
        self.assertFalse(os.path.exists(seen["cwd"]), "the temporary folder with the dataset must not outlive the job")

    def test_the_worker_receives_only_its_own_inputs(self):
        seen = {}
        real = svc.tasks.run_task

        def spy(task, job, sources, **kw):
            seen["task"], seen["job_keys"], seen["sources"] = task, set(job), set(sources)
            return real(task, job, sources, **kw)

        with mock.patch("app.modules.predictive.service.run_task", spy):
            self.p.assess(self.alice, self.asset["id"], {"target": "bought"})
        self.assertEqual(seen["task"], "assess")
        self.assertEqual(seen["sources"], {"table.csv"})
        forbidden = {"user_id", "email", "password", "token", "secret", "storage_key", "project_id", "owner"}
        self.assertFalse({k for k in seen["job_keys"] if any(f in k.lower() for f in forbidden)}, seen["job_keys"])


class SourceDiscipline(unittest.TestCase):
    """The modelling and explanation code never runs text it was given, never opens a network connection or a shell, and deserialises exactly one thing in
    one place: a model file the server wrote itself and verified by hash. Each reviewed exception is named here, so a new one cannot appear unnoticed."""
    ROOTS = ("predictive", "explanation")
    BANNED_CALLS = {"eval", "exec", "compile", "__import__", "system", "popen"}
    BANNED_IMPORTS = {"socket", "requests", "urllib", "http", "ftplib", "smtplib", "ctypes", "marshal", "shelve", "subprocess", "importlib", "runpy", "code"}
    OPEN_ALLOWED_IN = {"tasks.py", "worker.py"}               # tasks: copies the table into the job's private folder; worker: reads that private copy
    PICKLE_IMPORT_ALLOWED_IN = {"train.py"}                   # train: measures a fitted model's size and writes the model file; it never reads one
    LOAD_ALLOWED_IN = {"predict.py"}                          # predict: the one reader of model files (after the SHA-256 check, inside the isolated worker)

    def files(self):
        base = pathlib.Path(__file__).resolve().parents[1] / "app" / "modules"
        found = [p for r in self.ROOTS for p in (base / r).rglob("*.py")]
        self.assertGreaterEqual(len(found), 15)
        return found

    def test_no_dynamic_code_shell_or_network(self):
        for path in self.files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
                    if isinstance(fn, ast.Attribute) and name == "compile":
                        continue                                   # re.compile is a regular expression, not code
                    self.assertNotIn(name, self.BANNED_CALLS, f"{path.name}:{node.lineno} calls {name}")
                    if name == "open" and isinstance(fn, ast.Name):
                        self.assertIn(path.name, self.OPEN_ALLOWED_IN, f"{path.name}:{node.lineno} opens a file")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for m in mods:
                        self.assertNotIn(m.split(".")[0], self.BANNED_IMPORTS, f"{path.name}:{node.lineno} imports {m}")

    def test_models_are_read_in_one_place_and_pickle_is_only_used_to_measure_and_write(self):
        loads, pickle_imports = set(), set()
        for path in self.files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("load", "loads") and isinstance(node.func.value, ast.Name) \
                        and node.func.value.id in ("pickle", "joblib", "cloudpickle", "dill"):
                    loads.add(path.name)
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    if any(m.split(".")[0] in ("pickle", "cloudpickle", "dill") for m in mods):
                        pickle_imports.add(path.name)
        self.assertEqual(loads, self.LOAD_ALLOWED_IN)
        self.assertEqual(pickle_imports, self.PICKLE_IMPORT_ALLOWED_IN)

    def test_the_only_process_started_is_the_reviewed_sandbox(self):
        """The package has no subprocess of its own; it reuses the Phase 4 sandbox whose launcher only accepts the two named workers."""
        boot = (pathlib.Path(__file__).resolve().parents[1] / "app" / "modules" / "analytics" / "_boot.py").read_text(encoding="utf-8")
        self.assertIn('WORKERS = ("analytics", "predictive")', boot)
        self.assertIn("sys.exit(2)", boot)


if __name__ == "__main__":
    unittest.main()
