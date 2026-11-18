"""Statistical methods behind Advanced Intelligence. Pure functions on a pandas frame; every function returns plain numbers plus the wording that is safe to show.

Wording rules enforced by construction: associations are called associations; nothing here says one column causes, drives, leads to or explains another. Where a person might read
cause into a number, a sentence says what the number does and does not show."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

CALC_VERSION = "adv_methods_v1"
ASSOCIATION_NOTE = "An association means the two columns move together in this data. It does not show that one produces the other: a third factor, the order of events, or chance can create the same pattern."
FORBIDDEN_CAUSAL = ("causes", "caused", "because of", "leads to", "due to", "results in", "responsible for", "drives", "is the reason", "effect of")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def is_numeric(s: pd.Series) -> bool:
    if pd.api.types.is_bool_dtype(s):
        return False
    if pd.api.types.is_numeric_dtype(s):
        return s.nunique(dropna=True) > 2 or not set(s.dropna().unique()) <= {0, 1}
    return False


def target_vector(df: pd.DataFrame, target: str):
    """(numeric y, kind) where kind is 'numeric' or 'binary'. Binary targets are coded 0/1 with the coding reported."""
    s = df[target]
    vals = [v for v in pd.unique(s.dropna())]
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and len(vals) > 2:
        return _num(s), "numeric", None
    if len(vals) == 2:
        pos = sorted(vals, key=lambda v: str(v))[-1]
        return (s == pos).astype(float).where(s.notna()), "binary", {"positive": str(pos), "other": str([v for v in vals if v != pos][0])}
    raise ValueError("The outcome column must be numeric or have exactly two values.")


def associations(df: pd.DataFrame, target: str, top_k: int = 8, min_group: int = 10) -> Dict[str, Any]:
    from scipy import stats
    y, kind, coding = target_vector(df, target)
    rows: List[Dict[str, Any]] = []
    tests = 0
    for col in df.columns:
        if col == target:
            continue
        x = df[col]
        mask = y.notna() & x.notna()
        n = int(mask.sum())
        if n < 20 or x[mask].nunique() < 2:
            continue
        miss = round(float(x.isna().mean()), 4)
        if is_numeric(x):
            rho, p = stats.spearmanr(_num(x)[mask], y[mask])
            if not math.isfinite(rho):
                continue
            tests += 1
            rows.append({"column": col, "type": "numeric", "measure": "Spearman rank correlation", "value": round(float(rho), 4), "strength": abs(float(rho)), "direction": "higher values go with higher outcome" if rho > 0 else "higher values go with lower outcome",
                         "p_value": float(p), "n": n, "missing_share": miss})
        elif x[mask].nunique() <= 30:
            groups = [y[mask][x[mask] == lv].values for lv in x[mask].unique() if (x[mask] == lv).sum() >= min_group]
            if len(groups) < 2:
                continue
            f, p = stats.f_oneway(*groups) if kind == "numeric" else stats.kruskal(*groups)
            allv = np.concatenate(groups)
            ss_t = float(((allv - allv.mean()) ** 2).sum())
            ss_b = float(sum(len(g) * (g.mean() - allv.mean()) ** 2 for g in groups))
            eta = ss_b / ss_t if ss_t > 0 else 0.0
            tests += 1
            means = {str(lv): {"mean": round(float(y[mask][x[mask] == lv].mean()), 4), "n": int((x[mask] == lv).sum())} for lv in x[mask].unique() if (x[mask] == lv).sum() >= min_group}
            rows.append({"column": col, "type": "categorical", "measure": "Share of the outcome's variation that differs between groups (eta squared)", "value": round(eta, 4), "strength": math.sqrt(max(eta, 0)),
                         "direction": "groups differ", "p_value": float(p), "n": n, "missing_share": miss, "groups": dict(sorted(means.items(), key=lambda kv: -kv[1]["mean"])[:8])})
    for r in rows:
        r["p_adjusted"] = min(1.0, r["p_value"] * max(tests, 1))                       # Bonferroni: many columns were tested, so some will look related by chance
        r["p_value"] = round(r["p_value"], 6)
        r["p_adjusted"] = round(r["p_adjusted"], 6)
        r["supported"] = r["p_adjusted"] < 0.05
    rows.sort(key=lambda r: -r["strength"])
    top = rows[:top_k]
    overlap = []
    nums = [r["column"] for r in top if r["type"] == "numeric"]
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            m = df[a].notna() & df[b].notna()
            if m.sum() > 20:
                c = float(stats.spearmanr(_num(df[a])[m], _num(df[b])[m])[0])
                if abs(c) >= 0.7:
                    overlap.append({"columns": [a, b], "correlation": round(c, 3), "note": "These two columns carry much the same information, so the data cannot say which one matters."})
    return {"kind": kind, "coding": coding, "tests_run": tests, "associations": top, "overlap": overlap, "rows_used": int(y.notna().sum()), "columns_considered": len(rows)}


def describe_metric(df: pd.DataFrame, column: str, statistic: str, by: Optional[str] = None) -> Dict[str, Any]:
    s = df[column]
    out: Dict[str, Any] = {"column": column, "statistic": statistic, "rows": int(len(s)), "missing": int(s.isna().sum()), "missing_share": round(float(s.isna().mean()), 4)}
    n = _num(s)
    if statistic == "count":
        val = float(s.notna().sum())
    elif statistic == "rate":
        vals = s.dropna()
        pos = sorted(pd.unique(vals), key=str)[-1] if len(pd.unique(vals)) == 2 else None
        if pos is None:
            raise ValueError("A rate needs a column with exactly two values.")
        val = float((vals == pos).mean())
        out["counted_as_yes"] = str(pos)
    else:
        if n.notna().sum() == 0:
            raise ValueError("This column has no numbers.")
        val = float({"mean": n.mean, "median": n.median, "sum": n.sum, "min": n.min, "max": n.max, "std": n.std}[statistic]())
    out["value"] = round(val, 6)
    if n.notna().sum() >= 4:
        q1, q3 = n.quantile(.25), n.quantile(.75)
        iqr = q3 - q1
        out["distribution"] = {"min": float(n.min()), "q1": float(q1), "median": float(n.median()), "q3": float(q3), "max": float(n.max()), "mean": round(float(n.mean()), 4), "std": round(float(n.std()), 4)}
        out["outliers"] = int(((n < q1 - 1.5 * iqr) | (n > q3 + 1.5 * iqr)).sum())
        if statistic in ("mean", "sum") and out["outliers"]:
            out["outlier_effect"] = {"mean_without_outliers": round(float(n[(n >= q1 - 1.5 * iqr) & (n <= q3 + 1.5 * iqr)].mean()), 4)}
    if by and by in df.columns:
        grp = []
        for lv, g in df.groupby(by, dropna=True):
            if len(g) >= 5:
                gn = _num(g[column])
                v = float(gn.mean()) if statistic in ("mean", "sum", "std", "min", "max") and gn.notna().any() else float(gn.median()) if statistic == "median" and gn.notna().any() else float(len(g))
                grp.append({"group": str(lv), "n": int(len(g)), "value": round(v, 4)})
        out["by"] = {"column": by, "groups": sorted(grp, key=lambda g: -g["value"])[:12]}
    return out


def adjusted_difference(df: pd.DataFrame, treatment: str, outcome: str, covariates: Sequence[str], seed: int = 42, boot: int = 300) -> Dict[str, Any]:
    """Unadjusted and covariate-adjusted difference in the outcome between treated and untreated rows. Both are associations."""
    y, kind, coding = target_vector(df, outcome)
    t_raw = df[treatment]
    tv = [v for v in pd.unique(t_raw.dropna())]
    if len(tv) != 2:
        raise ValueError("The treatment column must have exactly two values for this check.")
    pos = sorted(tv, key=str)[-1]
    t = (t_raw == pos).astype(float)
    use = df[[outcome, treatment, *covariates]].copy()
    X = pd.get_dummies(use[list(covariates)], drop_first=True, dtype=float) if covariates else pd.DataFrame(index=use.index)
    X = X.apply(_num)
    ok = y.notna() & t_raw.notna() & X.notna().all(axis=1)
    y, t, X = y[ok].values, t[ok].values, X[ok]
    n1, n0 = int((t == 1).sum()), int((t == 0).sum())
    if min(n1, n0) < 10:
        raise ValueError("Fewer than 10 rows in one treatment group.")
    raw = float(y[t == 1].mean() - y[t == 0].mean())
    A = np.column_stack([np.ones(len(y)), t, X.values]) if X.shape[1] else np.column_stack([np.ones(len(y)), t])
    beta = np.linalg.lstsq(A, y, rcond=None)[0]
    rng = np.random.default_rng(seed)
    raws, adjs = [], []
    idx = np.arange(len(y))
    for _ in range(boot):
        b = rng.choice(idx, len(idx))
        if (t[b] == 1).sum() < 2 or (t[b] == 0).sum() < 2:
            continue
        raws.append(y[b][t[b] == 1].mean() - y[b][t[b] == 0].mean())
        adjs.append(np.linalg.lstsq(A[b], y[b], rcond=None)[0][1])
    ci = lambda v: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)] if v else None  # noqa: E731
    smd = []
    for c in X.columns[:12]:
        a, b = X[c].values[t == 1], X[c].values[t == 0]
        sd = math.sqrt((a.var() + b.var()) / 2) or 1.0
        smd.append({"covariate": c, "standardised_difference": round(float((a.mean() - b.mean()) / sd), 3)})
    return {"treated_value": str(pos), "n_treated": n1, "n_untreated": n0, "unadjusted_difference": round(raw, 4), "unadjusted_interval_95": ci(raws), "adjusted_difference": round(float(beta[1]), 4), "adjusted_interval_95": ci(adjs),
            "balance": sorted(smd, key=lambda r: -abs(r["standardised_difference"])), "outcome_kind": kind, "bootstrap": {"resamples": len(raws), "seed": seed}}


def library_versions() -> Dict[str, str]:
    import sys
    out = {"python": sys.version.split()[0], "calc": CALC_VERSION, "numpy": np.__version__, "pandas": pd.__version__}
    try:
        import scipy
        out["scipy"] = scipy.__version__
    except Exception:
        pass
    return out
