"""Shared fixtures for Phase 7 tests: real database, real storage, real Phase 5 models trained in-process."""
import unittest

import analytics_support as sup
import ml_support as S
from app.core.errors import AppError
from app.modules.phase7 import build_phase7
from support import Harness
from test_predictive_service import FAST, wait_job
from test_workspace_uploads import upload


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


class P7Base(unittest.TestCase):
    """One harness with alice (owner of data), bob and carol (other users), a project and one trained classification model per class."""

    @classmethod
    def setUpClass(cls):
        cls.h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=16 << 20, ml_isolation="inprocess", standard_max_documents=50)
        cls.alice, _ = cls.h.make_user("alice@example.com")
        cls.bob, _ = cls.h.make_user("bob@example.com")
        cls.carol, _ = cls.h.make_user("carol@example.com")
        cls.ws = cls.h.workspace()
        cls.p7 = build_phase7(cls.ws)
        cls.project = cls.ws.projects.create(cls.alice, "Churn")["id"]
        cls.df = S.classification_frame(500)
        cls.asset = upload(cls.ws, cls.alice, cls.project, "customers.csv", sup.csv_of(cls.df))["asset"]

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    @classmethod
    def train(cls, **config):
        job = cls.ws.predictive.start_training(cls.alice, cls.asset["id"], {**FAST, **config})
        return wait_job(cls.ws.predictive, cls.alice, job["id"])["model_id"]
