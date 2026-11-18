"""Shared helpers for predictive-modelling tests: typed frames from synthetic CSVs, and datasets with known structure."""
import io

import numpy as np
import pandas as pd

import analytics_support as sup
from app.modules.analytics.frame import load_snapshot
from app.modules.predictive.config import parse_config


def typed(df, dates=()):
    snap = load_snapshot(io.BytesIO(sup.csv_of(df)), sup.declared(df, dates=dates), rows_total=len(df), max_rows=10 ** 7, max_cells=10 ** 9)
    return snap.df, snap.types


def config(types, **raw):
    return parse_config(raw, types)


def classification_frame(n=600, seed=11):
    """Two informative numbers, one informative category, one noise column, one id, one constant; binary target `bought`."""
    r = np.random.default_rng(seed)
    x1 = r.normal(0, 1, n)
    x2 = r.normal(0, 1, n)
    plan = r.choice(["basic", "plus", "pro"], n, p=[.5, .3, .2])
    bonus = {"basic": -0.6, "plus": 0.0, "pro": 0.9}
    logit = 1.4 * x1 - 0.9 * x2 + np.array([bonus[p] for p in plan])
    y = (r.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    df = pd.DataFrame({"row_id": np.arange(1, n + 1), "x1": x1.round(4), "x2": x2.round(4), "plan": plan, "noise": r.normal(0, 1, n).round(4),
                       "flat": "same", "bought": np.where(y == 1, "yes", "no")})
    return df


def multiclass_frame(n=600, seed=5):
    r = np.random.default_rng(seed)
    a = r.normal(0, 1, n)
    b = r.normal(0, 1, n)
    score = a + 0.5 * b
    label = np.where(score < -0.5, "low", np.where(score < 0.6, "mid", "high"))
    return pd.DataFrame({"a": a.round(4), "b": b.round(4), "c": r.normal(0, 1, n).round(4), "grade": label})


def regression_frame(n=500, seed=3):
    r = np.random.default_rng(seed)
    x1 = r.uniform(0, 10, n)
    x2 = r.normal(0, 1, n)
    cat = r.choice(["u", "v", "w"], n)
    y = 3.0 * x1 + 2.0 * x2 + np.array([{"u": 0, "v": 4, "w": -3}[c] for c in cat]) + r.normal(0, 1.0, n)
    dates = pd.to_datetime("2022-01-01") + pd.to_timedelta(r.integers(0, 900, n), unit="D")
    return pd.DataFrame({"x1": x1.round(4), "x2": x2.round(4), "cat": cat, "when": dates.strftime("%Y-%m-%d"), "price": y.round(4)})
