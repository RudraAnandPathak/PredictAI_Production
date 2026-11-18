"""Distribution comparison for monitoring. Pure functions over arrays: no data access, no randomness.

PSI bands (below 0.10 stable, 0.10 to 0.25 moderate, above 0.25 significant) are a common industry rule of thumb, not a statistical test; the response says so.
Drift in inputs means the new data looks different from the training data. It does NOT show that predictions became wrong: only recorded outcomes can show that."""
from __future__ import annotations

import math
from typing import Any, Dict, Sequence

import numpy as np
import pandas as pd

MIN_ROWS = 30
BINS = 10
EPS = 1e-4
PSI_MODERATE, PSI_SIGNIFICANT = 0.10, 0.25
RULE_NOTE = "PSI bands (0.10 and 0.25) are a common rule of thumb, not a statistical test. Input drift does not show that predictions are wrong; only recorded outcomes can."


def band(psi: float) -> str:
    return "stable" if psi < PSI_MODERATE else "moderate" if psi < PSI_SIGNIFICANT else "significant"


def _f(x: Any, d: int = 5) -> Any:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, d) if math.isfinite(v) else None


def _psi(p: np.ndarray, q: np.ndarray) -> float:
    p, q = np.clip(p, EPS, None), np.clip(q, EPS, None)
    p, q = p / p.sum(), q / q.sum()
    return float(np.sum((q - p) * np.log(q / p)))


def numeric(ref: pd.Series, cur: pd.Series) -> Dict[str, Any]:
    r = pd.to_numeric(ref, errors="coerce").dropna().to_numpy(float)
    c = pd.to_numeric(cur, errors="coerce").dropna().to_numpy(float)
    out: Dict[str, Any] = {"kind": "numeric", "reference_n": int(len(r)), "current_n": int(len(c)),
                           "reference_missing_share": _f(1 - len(r) / max(len(ref), 1), 4), "current_missing_share": _f(1 - len(c) / max(len(cur), 1), 4)}
    if len(r) < MIN_ROWS or len(c) < MIN_ROWS:
        return {**out, "status": "insufficient_data", "note": f"At least {MIN_ROWS} usable values are needed on each side."}
    edges = np.unique(np.quantile(r, np.linspace(0, 1, BINS + 1)))
    if len(edges) < 3:                                              # a (nearly) constant column: compare the constant itself
        v = float(edges[0])
        share = float(np.mean(c != v))
        return {**out, "status": band(share), "psi": None, "shift_share": _f(share, 4), "ks": None,
                "note": "The training values were (almost) constant; the figure is the share of new values that differ from that constant."}
    edges[0], edges[-1] = -np.inf, np.inf
    psi = _psi(np.histogram(r, edges)[0] / len(r), np.histogram(c, edges)[0] / len(c))
    from scipy.stats import ks_2samp
    ks = ks_2samp(r, c)
    return {**out, "status": band(psi), "psi": _f(psi), "ks": {"statistic": _f(ks.statistic), "p_value": _f(ks.pvalue)},
            "reference_mean": _f(r.mean()), "current_mean": _f(c.mean()), "reference_median": _f(np.median(r)), "current_median": _f(np.median(c)),
            "out_of_training_range_share": _f(float(np.mean((c < r.min()) | (c > r.max()))), 4)}


def categorical(ref: pd.Series, cur: pd.Series) -> Dict[str, Any]:
    r, c = ref.dropna().astype(str), cur.dropna().astype(str)
    out: Dict[str, Any] = {"kind": "categorical", "reference_n": int(len(r)), "current_n": int(len(c)),
                           "reference_missing_share": _f(1 - len(r) / max(len(ref), 1), 4), "current_missing_share": _f(1 - len(c) / max(len(cur), 1), 4)}
    if len(r) < MIN_ROWS or len(c) < MIN_ROWS:
        return {**out, "status": "insufficient_data", "note": f"At least {MIN_ROWS} usable values are needed on each side."}
    cats = sorted(set(r) | set(c))
    rp = r.value_counts(normalize=True).reindex(cats, fill_value=0.0).to_numpy()
    cp = c.value_counts(normalize=True).reindex(cats, fill_value=0.0).to_numpy()
    psi = _psi(rp, cp)
    unseen = sorted(set(c) - set(r))
    return {**out, "status": band(psi), "psi": _f(psi), "unseen_categories": unseen[:10], "unseen_share": _f(float(c.isin(unseen).mean()), 4)}


def compare(ref: pd.DataFrame, cur: pd.DataFrame, roles: Dict[str, str]) -> Dict[str, Any]:
    """roles maps column -> 'numeric' | 'categorical' | 'datetime' | other. Date and free-text columns are listed as not compared, never silently skipped."""
    cols: Dict[str, Any] = {}
    skipped: Dict[str, str] = {}
    for name, role in roles.items():
        if name not in ref.columns or name not in cur.columns:
            skipped[name] = "column missing from one of the two datasets"
        elif role == "numeric":
            cols[name] = numeric(ref[name], cur[name])
        elif role == "categorical":
            cols[name] = categorical(ref[name], cur[name])
        else:
            skipped[name] = f"'{role}' columns are not compared"
    order = {"stable": 0, "moderate": 1, "significant": 2}
    judged = [v["status"] for v in cols.values() if v["status"] in order]
    status = max(judged, key=order.__getitem__) if judged else "insufficient_data"
    return {"status": status, "columns": cols, "not_compared": skipped,
            "drifted_columns": sorted([k for k, v in cols.items() if v["status"] in ("moderate", "significant")]),
            "thresholds": {"moderate": PSI_MODERATE, "significant": PSI_SIGNIFICANT, "min_rows": MIN_ROWS}, "note": RULE_NOTE}


def performance(kind: str, actual: Sequence[Any], predicted: Sequence[Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """kind: 'classification' or 'regression'. baseline holds the hold-out score recorded at registration (may be empty)."""
    n = len(actual)
    if kind == "classification":
        acc = float(np.mean([str(a) == str(p) for a, p in zip(actual, predicted)])) if n else None
        base = baseline.get("accuracy")
        out: Dict[str, Any] = {"n": n, "accuracy": _f(acc, 4), "baseline_accuracy": _f(base, 4) if base is not None else None}
        drop = (base - acc) if (base is not None and acc is not None) else None
        out["change"] = _f(-drop, 4) if drop is not None else None
        degraded = drop is not None and drop > 0.10
    else:
        a, p = np.asarray(actual, float), np.asarray(predicted, float)
        err = p - a
        mae = float(np.mean(np.abs(err))) if n else None
        base = baseline.get("mae")
        out = {"n": n, "mae": _f(mae), "rmse": _f(float(np.sqrt(np.mean(err ** 2)))) if n else None, "bias": _f(float(np.mean(err))) if n else None,
               "baseline_mae": _f(base) if base is not None else None}
        ratio = (mae / base) if (base and mae is not None) else None
        out["error_ratio"] = _f(ratio, 3) if ratio is not None else None
        degraded = ratio is not None and ratio > 1.5
    if n < MIN_ROWS:
        status, note = "insufficient_outcomes", f"At least {MIN_ROWS} recorded outcomes are needed before a judgement is made; {n} so far."
    elif baseline.get("accuracy") is None and baseline.get("mae") is None:
        status, note = "no_baseline", "No hold-out score was recorded for this version, so there is nothing to compare with."
    else:
        status = "degraded" if degraded else "within_expectation"
        note = "Rule of thumb: accuracy more than 10 points below the hold-out score, or MAE more than 1.5 times the hold-out MAE, is flagged. A flag is a prompt to look, not proof."
    return {**out, "status": status, "note": note}
