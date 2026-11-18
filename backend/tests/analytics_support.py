"""Deterministic synthetic data for analytics tests. Known structure (documented in `synth`) so results can be checked against truth."""
import io

import numpy as np
import pandas as pd

import fixtures as fx  # noqa: F401  (keeps import order consistent with other test modules)


def synth(n=600, seed=7):
    """age -> income (positive, +800 per year), region shifts spend, North mostly segment A, churn rises with age,
    30 missing incomes and one 900000 income outlier at row 6 (index 5)."""
    r = np.random.default_rng(seed)
    age = r.integers(18, 70, n).astype(float)
    region = r.choice(["North", "South", "East", "West"], n, p=[.4, .3, .2, .1])
    income = 20000 + 800 * age + r.normal(0, 6000, n)
    shift = {"North": 0, "South": 3000, "East": -2000, "West": 8000}
    spend = 0.2 * income + np.array([shift[x] for x in region]) * 0.5 + r.normal(0, 2500, n)
    churn = (r.random(n) < (0.1 + 0.006 * (age - 18))).astype(int)
    seg = np.where(r.random(n) < 0.8, np.where(region == "North", "A", "B"), r.choice(["A", "B"], n))
    dates = pd.to_datetime("2023-01-01") + pd.to_timedelta(r.integers(0, 720, n), unit="D")
    df = pd.DataFrame({"id": np.arange(1, n + 1), "region": region, "age": age, "income": income.round(2), "spend": spend.round(2), "churned": churn, "segment": seg,
                       "signup": dates.strftime("%Y-%m-%d"), "const": "x"})
    df.loc[r.choice(n, 30, replace=False), "income"] = np.nan
    df.loc[5, "income"] = 900000.0
    return df


def csv_of(df):
    return df.to_csv(index=False).encode()


def declared(df, dates=()):
    out = []
    for name in df.columns:
        if name in dates:
            kind = "date"
        elif str(df[name].dtype).startswith("int"):
            kind = "integer"
        elif str(df[name].dtype).startswith("float"):
            kind = "float"
        elif str(df[name].dtype) == "bool":
            kind = "boolean"
        else:
            kind = "text"
        out.append({"name": name, "type": kind})
    return out


def run_engine(df, ops, *, filters=None, dates=(), kind="descriptive", seed=None, null_policy=None, isolation="inprocess", limits=None, sample_rows=None):
    """Runs a plan through the real executor against an in-memory CSV (no database); returns (plan, output)."""
    from app.modules.analytics.executor import execute, limits_for
    from app.modules.analytics.ops import REGISTRY
    from app.modules.analytics.plan import build_plan

    decl = declared(df, dates)
    types = {c["name"]: c["type"] for c in decl}
    plan = build_plan(kind=kind, asset_id="a" * 32, version_id="v" * 32, operations=ops, filters=filters, null_policy=null_policy, seed=seed, sample_rows=sample_rows, registry=REGISTRY, types=types)
    body = csv_of(df)
    out = execute(plan, lambda: io.BytesIO(body), decl, len(df), limits or limits_for(False), isolation=isolation)
    return plan, out


def first(out, op=None):
    for r in out["results"]:
        if op is None or r["op"] == op:
            return r
    raise AssertionError("no such result")
