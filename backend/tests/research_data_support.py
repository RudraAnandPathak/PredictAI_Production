"""Shared fixtures for the dataset-research tests: one database, one trained Phase 5 model and the real Phase 4 and 6 services, built once per test class because training is the slow part.
The data are generated from fixed seeds, so every number a test checks is also computed here independently with pandas, never copied from the code under test."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import analytics_support as sup
import ml_support as S
from app.modules.research.container import ResearchRuntime, build_research
from app.modules.retrieval.corpus import CORPUS_CACHE
from app.modules.webaccess.fetcher import SafeFetcher
from support import Harness
from test_predictive_service import FAST, wait_job
from test_workspace_uploads import upload

CANARY = "ZXCANARY"


def airline_frame(n: int = 600, seed: int = 7, year_shift: float = 0.0) -> pd.DataFrame:
    """Airline-like rows: business class has fewer seats and higher fares; passengers fall as price rises; 10% of passengers are missing; a few delays are extreme.
    `note` is unique per row and carries a canary string that must never leave the platform."""
    r = np.random.default_rng(seed)
    fare = r.choice(["economy", "business"], n, p=[0.75, 0.25])
    route = r.choice(["DEL-BOM", "DEL-BLR", "BOM-BLR"], n)
    price = np.where(fare == "business", r.normal(12000 + year_shift, 1500, n), r.normal(4000 + year_shift, 600, n)).round(0)
    seats = np.where(fare == "business", r.normal(8, 2, n), r.normal(120, 15, n)).round(0).clip(2)
    passengers = (seats * 0.8 - price / 1000.0 * 0.6 + r.normal(0, 2, n)).round(0).clip(0)
    delay = r.exponential(12, n).round(1)
    delay[:4] = [900.0, 950.0, 1000.0, 1100.0]
    df = pd.DataFrame({"flight_no": [f"AI{i:04d}" for i in range(1, n + 1)], "route": route, "fare_class": fare, "price": price, "seats_available": seats, "passengers": passengers,
                       "delay_minutes": delay, "note": [f"{CANARY}-{i:05d}" for i in range(n)]})
    df.loc[r.choice(n, size=n // 10, replace=False), "passengers"] = np.nan
    return df


class DatasetResearchBase(unittest.TestCase):
    """setUpClass: a project holding customers.csv (with a trained model and saved findings), airline.csv, airline_2023.csv, and a second user with a project of their own."""

    @classmethod
    def setUpClass(cls):
        CORPUS_CACHE.clear()
        cls.h = Harness(upload_chunk_max_bytes=1 << 20, upload_max_bytes=16 << 20, ml_isolation="inprocess", research_web_enabled=True, research_allow_private_network=True, research_fetch_timeout_s=5)
        cls.alice, _ = cls.h.make_user("alice@example.com")
        cls.bob, _ = cls.h.make_user("bob@example.com")
        cls.ws = cls.h.workspace()
        cls.fetcher = cls.make_fetcher()
        cls.runtime = ResearchRuntime(db=cls.h.db, storage=cls.ws.storage, settings=cls.h.settings, clock=cls.h.clock, hasher=cls.h.hasher, sender=cls.h.outbox, http=None, fetcher=cls.fetcher,
                                      workers=2, sleeper=lambda s: None)
        cls.rs = build_research(cls.ws, cls.runtime)
        cls.svc = cls.rs.research
        cls.pid = cls.ws.projects.create(cls.alice, "Airline and customers")["id"]
        cls.bob_pid = cls.ws.projects.create(cls.bob, "Bob")["id"]
        cls.cust_df = S.classification_frame(400)
        cls.cust = upload(cls.ws, cls.alice, cls.pid, "customers.csv", sup.csv_of(cls.cust_df))["asset"]
        cls.air_df = airline_frame()
        cls.air = upload(cls.ws, cls.alice, cls.pid, "airline.csv", sup.csv_of(cls.air_df))["asset"]
        cls.air23_df = airline_frame(500, seed=9, year_shift=800.0)
        cls.air23 = upload(cls.ws, cls.alice, cls.pid, "airline_2023.csv", sup.csv_of(cls.air23_df))["asset"]
        cls.bob_asset = upload(cls.ws, cls.bob, cls.bob_pid, "bobs.csv", sup.csv_of(S.classification_frame(120)))["asset"]
        cls.ws.profiles.generate(cls.alice, cls.cust["id"])
        cls.insights = cls.ws.insights.generate(cls.alice, cls.cust["id"])
        job = cls.ws.predictive.start_training(cls.alice, cls.cust["id"], FAST, label="Baseline")
        cls.model = wait_job(cls.ws.predictive, cls.alice, job["id"])["model"]

    @classmethod
    def make_fetcher(cls):
        return SafeFetcher(user_agent="PredictAI-Test/1", allow_private=True, sleeper=lambda s: None, host_interval_s=0, timeout_s=5)

    @classmethod
    def tearDownClass(cls):
        cls.runtime.runner.shutdown()
        cls.h.close()

    # ---- running a question the way the product does
    def ask(self, question, datasets, *, level="topic", principal=None, pid=None, **options):
        p = principal or self.alice
        s = self.svc.create_session(p, pid or self.pid, {"question": question, "level": level, "options": {"datasets": datasets, **options}})
        job = self.svc.run(p, s["id"], None)
        self.runtime.runner.wait(job["id"], 180)
        done = self.svc.job(p, job["id"])
        self.assertEqual(done["status"], "completed", done.get("error_message"))
        return s, self.svc.get_bundle(p, done["result_id"])["bundle"]

    @staticmethod
    def calc(bundle):
        return [e for e in bundle["evidence"] if e["origin"] == "calculated"]
