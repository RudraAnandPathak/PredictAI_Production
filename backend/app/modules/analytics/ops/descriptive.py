"""Descriptive analytics: central tendency, spread, distributions, frequencies, data quality, aggregation.
Extends (does not replace) the Phase 3 profiler: same value parsing and missing tokens, but richer, filterable and evidence-bearing."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

from ..constants import MAX_CATEGORIES, MAX_GROUPS, MIN_N_GROUP
from ..errors import AnalysisError
from ..plan import OpSpec, P, role_of
from . import common as c
from .common import OpContext, evidence_for, num, py, result

DEFAULT_PERCENTILES = [1.0, 5.0, 25.0, 50.0, 75.0, 95.0, 99.0]
IMBALANCE_DOMINANT_SHARE = 0.9       # a category holding >= 90% of the values is flagged
HIGH_MISSING_PCT = 20.0
OUTLIER_RULE = "values outside Q1 - 1.5*IQR .. Q3 + 1.5*IQR (Tukey fences)"


def _weighted(x: np.ndarray, w: np.ndarray) -> Dict[str, Any]:
    sw = float(w.sum())
    mu = float((w * x).sum() / sw)
    v1, v2 = sw, float((w * w).sum())
    denom = v1 - v2 / v1
    return {"rows": int(len(x)), "weight_sum": num(sw), "mean": num(mu),
            "std": num(math.sqrt(float((w * (x - mu) ** 2).sum() / denom))) if denom > 0 else None,
            "basis": "reliability weights over rows where both the value and the weight are present; the unweighted statistics are unchanged"}


def _numeric_core(x: np.ndarray, *, total: int, invalid: int, percentiles: List[float], bins: Optional[int]) -> Dict[str, Any]:
    present = len(x)
    out: Dict[str, Any] = {"count": int(total), "non_missing": int(present), "missing": int(total - present - invalid) if total >= present + invalid else int(total - present),
                           "invalid": int(invalid), "missing_pct": c.share(total - present, total)}
    if present == 0:
        return out
    var = float(x.var(ddof=1)) if present > 1 else None
    std = math.sqrt(var) if var is not None else None
    q = np.percentile(x, percentiles)
    q1, q3 = np.percentile(x, [25, 75])
    lo, hi, _, _ = c.outlier_fences(x)
    mask = (x < lo) | (x > hi)
    vc = pd.Series(x).value_counts()
    top = vc[vc == vc.max()]
    mad = float(np.median(np.abs(x - np.median(x))))
    modified_z = (0.6745 * (x - np.median(x)) / mad) if mad > 0 else np.zeros(present)
    out.update({
        "mean": num(x.mean()), "median": num(np.median(x)),
        "mode": {"values": [num(v) for v in sorted(top.index.tolist())[:5]], "count": int(vc.max()), "repeated": bool(vc.max() > 1)},
        "sum": num(x.sum()), "min": num(x.min()), "max": num(x.max()), "range": num(x.max() - x.min()),
        "variance": num(var), "std": num(std), "sem": num(std / math.sqrt(present)) if std is not None else None,
        "cv": num(std / abs(x.mean())) if std is not None and x.mean() != 0 else None,
        "iqr": num(q3 - q1), "quantiles": {f"p{int(p) if float(p).is_integer() else p}": num(v) for p, v in zip(percentiles, q)},
        "skewness": num(stats.skew(x, bias=False)) if present > 2 and var and var > 0 else None,
        "excess_kurtosis": num(stats.kurtosis(x, bias=False)) if present > 3 and var and var > 0 else None,
        "outliers": {"rule": OUTLIER_RULE, "lower_fence": num(lo), "upper_fence": num(hi), "count": int(mask.sum()),
                     "pct": c.share(int(mask.sum()), present), "mad_flagged_count": int((np.abs(modified_z) > 3.5).sum()),
                     "mad_rule": "modified z-score > 3.5 (median / MAD)"},
        "histogram": c.histogram(x, bins),
    })
    return out


def _categorical_core(s: pd.Series, total: int, top: int) -> Dict[str, Any]:
    present = s.dropna()
    counts = present.value_counts()
    k, n = len(counts), int(len(present))
    out: Dict[str, Any] = {"count": int(total), "non_missing": n, "missing": int(total - n), "missing_pct": c.share(total - n, total), "distinct": int(k)}
    if n == 0:
        return out
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    shown = ordered[:top]
    out["frequencies"] = [{"value": str(v), "count": int(cnt), "pct": c.share(int(cnt), n), "cumulative_pct": None} for v, cnt in shown]
    run = 0
    for row in out["frequencies"]:
        run += row["count"]
        row["cumulative_pct"] = c.share(run, n)
    if k > top:
        out["other"] = {"distinct": int(k - top), "count": int(n - sum(cnt for _, cnt in shown)), "pct": c.share(int(n - sum(cnt for _, cnt in shown)), n)}
    out["mode"] = {"values": [str(v) for v, cnt in ordered if cnt == ordered[0][1]][:5], "count": int(ordered[0][1])}
    probs = counts.to_numpy() / n
    out["balance"] = {"dominant_share": num(float(probs.max())), "imbalance_ratio": num(float(counts.max() / counts.min())) if k > 1 else None,
                      "normalised_entropy": num(float(-(probs * np.log(probs)).sum() / math.log(k))) if k > 1 else None,
                      "note": "normalised entropy is 1 when categories are equally common and near 0 when one dominates"}
    return out


def _datetime_core(s: pd.Series, total: int) -> Dict[str, Any]:
    present = s.dropna()
    out: Dict[str, Any] = {"count": int(total), "non_missing": int(len(present)), "missing": int(total - len(present)), "missing_pct": c.share(total - len(present), total)}
    if len(present):
        out.update({"min": pd.Timestamp(present.min()).isoformat(), "max": pd.Timestamp(present.max()).isoformat(),
                    "median": pd.Timestamp(present.median()).isoformat(), "span_days": num((present.max() - present.min()) / pd.Timedelta(days=1)),
                    "distinct": int(present.nunique())})
    return out


def column_stats(ctx: OpContext, columns: Optional[List[str]] = None, percentiles: Optional[List[float]] = None, bins: Optional[int] = None,
                 weight: Optional[str] = None, group_by: Optional[str] = None, top: int = 20) -> Dict[str, Any]:
    cols = columns or list(ctx.df.columns)
    percentiles = percentiles or DEFAULT_PERCENTILES
    if any(p < 0 or p > 100 for p in percentiles):
        raise AnalysisError("PARAM_INVALID", "Percentiles must be between 0 and 100.")
    warnings: List[str] = []
    out_cols: List[Dict[str, Any]] = []
    w_all = None
    if weight:
        w_all = ctx.df[weight].astype(float)
        if (w_all.dropna() < 0).any() or not (w_all.dropna() > 0).any():
            warnings.append("Weights must be non-negative with a positive total; weighted statistics were not calculated.")
            w_all = None
    for col in cols:
        ctx.check()
        role = role_of(ctx.types[col])
        s = ctx.df[col]
        invalid = int(len([r for r in ctx.invalid_rows.get(col, []) if r in s.index])) if ctx.invalid_rows.get(col) else 0
        entry: Dict[str, Any] = {"column": col, "type": ctx.types[col], "role": role}
        if role == "numeric":
            x = s.astype(float)
            keep = x.notna()
            entry.update(_numeric_core(x[keep].to_numpy(), total=len(s), invalid=invalid, percentiles=percentiles, bins=bins))
            if w_all is not None and col != weight:
                both = keep & w_all.notna()
                if both.any() and w_all[both].sum() > 0:
                    entry["weighted"] = _weighted(x[both].to_numpy(), w_all[both].to_numpy())
            if group_by and group_by != col:
                g = c.category(ctx, group_by)
                groups = []
                for key, idx in list(g.dropna().groupby(g.dropna()).groups.items())[:MAX_GROUPS * 3]:
                    v = x.loc[idx].dropna().to_numpy()
                    if len(v):
                        groups.append({"group": str(key), "n": int(len(v)), "mean": num(v.mean()), "median": num(np.median(v)),
                                       "std": num(v.std(ddof=1)) if len(v) > 1 else None, "min": num(v.min()), "max": num(v.max())})
                groups.sort(key=lambda r: (-r["n"], r["group"]))
                entry["by_group"] = {"column": group_by, "groups": groups[:MAX_GROUPS], "groups_not_shown": max(0, len(groups) - MAX_GROUPS)}
        elif role == "datetime":
            entry.update(_datetime_core(s, len(s)))
        else:
            entry.update(_categorical_core(c.category(ctx, col), len(s), top))
            if invalid:
                entry["invalid"] = invalid
        out_cols.append(entry)
    n = len(ctx.df)
    ev = evidence_for(ctx, columns=cols + ([weight] if weight else []) + ([group_by] if group_by else []), n=n, method="column statistics",
                      formula="analytics.ops.descriptive.column_stats; quantiles: linear interpolation (numpy default, 'type 7'); variance/std: n-1 denominator; "
                              "skewness/kurtosis: bias-corrected (scipy.stats)",
                      null_policy="missing values are excluded from each column's own statistics; missing is counted and reported per column",
                      limitations=["Statistics describe the rows in scope exactly; they are not estimates for a wider population unless the rows are a random sample of it.",
                                   "Column types come from a heuristic (90% rule) applied when the file was read."])
    return result("ok", {"columns": out_cols, "percentiles": percentiles}, ev, warnings)


def dataset_summary(ctx: OpContext) -> Dict[str, Any]:
    df = ctx.df
    n, k = len(df), df.shape[1]
    dup_mask = df.duplicated(keep="first") if n else pd.Series(dtype=bool)
    missing_by_col = df.isna().sum()
    cells = n * k
    types_count: Dict[str, int] = {}
    for t in ctx.types.values():
        types_count[t] = types_count.get(t, 0) + 1
    worst = sorted(((str(col), int(m)) for col, m in missing_by_col.items() if m), key=lambda kv: (-kv[1], kv[0]))[:10]
    data = {"rows_in_scope": int(n), "rows_in_dataset": int(ctx.rows_total), "columns": int(k), "column_types": dict(sorted(types_count.items())),
            "duplicate_rows": {"count": int(dup_mask.sum()), "pct": c.share(int(dup_mask.sum()), n), "first_row_numbers": [int(i) for i in df.index[dup_mask][:20]],
                               "definition": "a row identical in every column to an earlier row in scope"},
            "missing_cells": {"count": int(missing_by_col.sum()), "pct": c.share(int(missing_by_col.sum()), cells)},
            "columns_with_most_missing": [{"column": col, "missing": m, "pct": c.share(m, n)} for col, m in worst],
            "complete_rows": int(df.notna().all(axis=1).sum()) if n else 0}
    ev = evidence_for(ctx, columns=list(df.columns), n=n, method="dataset summary", formula="analytics.ops.descriptive.dataset_summary",
                      null_policy="no rows are dropped; missing cells are counted", limitations=["Counts describe the rows in scope; a filter narrows the scope."])
    return result("ok", data, ev)


def missing_values(ctx: OpContext, columns: Optional[List[str]] = None, top: int = 50) -> Dict[str, Any]:
    cols = columns or list(ctx.df.columns)
    n = len(ctx.df)
    rows = sorted(({"column": col, "missing": int(ctx.df[col].isna().sum()), "pct": c.share(int(ctx.df[col].isna().sum()), n)} for col in cols),
                  key=lambda r: (-r["missing"], r["column"]))
    ev = evidence_for(ctx, columns=cols, n=n, method="missing-value count", formula="analytics.ops.descriptive.missing_values",
                      null_policy="no rows are dropped; the missing cells of each column are counted",
                      limitations=["The following tokens count as missing: empty, NA, N/A, NaN, null, none, nil, -, --, ?, #N/A, missing."])
    return result("ok", {"columns": rows[:top], "columns_with_any_missing": sum(1 for r in rows if r["missing"]), "columns_total": len(rows)}, ev)


def frequency(ctx: OpContext, column: str, top: int = 20, bins: Optional[int] = None) -> Dict[str, Any]:
    s = ctx.df[column]
    n = int(s.notna().sum())
    if role_of(ctx.types[column]) == "numeric" and bins:
        h = c.histogram(s.dropna().astype(float).to_numpy(), bins)
        rows = [{"label": f"[{h['edges'][i]}, {h['edges'][i + 1]}{']' if i == len(h['counts']) - 1 else ')'}", "lo": h["edges"][i], "hi": h["edges"][i + 1], "count": cnt,
                 "pct": c.share(cnt, n)} for i, cnt in enumerate(h["counts"])]
        data = {"column": column, "kind": "binned", "rows": rows, "binning": h.get("rule")}
        method = f"frequency in {len(rows)} equal-width bins"
    else:
        core = _categorical_core(c.category(ctx, column), len(s), top)
        data = {"column": column, "kind": "categorical", "rows": core.get("frequencies", []), "other": core.get("other"), "distinct": core.get("distinct")}
        method = "frequency distribution"
    ev = evidence_for(ctx, columns=[column], n=n, method=method, formula="analytics.ops.descriptive.frequency",
                      null_policy="missing values are excluded from the percentages and counted separately", extra={"binning": data.get("binning")})
    return result("ok", {**data, "missing": int(len(s) - n)}, ev)


def distribution_compare(ctx: OpContext, measure: str, group: str) -> Dict[str, Any]:
    x = ctx.df[measure].astype(float)
    g = c.category(ctx, group)
    frame = pd.DataFrame({"x": x, "g": g}).dropna()
    sizes = frame.groupby("g").size().sort_values(ascending=False)
    kept = [k for k in sizes.index[:8] if sizes[k] >= MIN_N_GROUP]
    warnings: List[str] = []
    if len(sizes) > 8:
        warnings.append(f"Only the 8 largest of {len(sizes)} groups are compared.")
    if len(sizes) > len(kept) and len(sizes) <= 8:
        warnings.append(f"Groups with fewer than {MIN_N_GROUP} values were left out of the comparison.")
    if len(kept) < 2:
        return c.not_enough(ctx, [measure, group], int(len(frame)), 2 * MIN_N_GROUP, "A distribution comparison", "two-sample Kolmogorov-Smirnov")
    summary, vals = [], {}
    for k in kept:
        v = frame.loc[frame.g == k, "x"].to_numpy()
        vals[k] = v
        q1, q3 = np.percentile(v, [25, 75])
        summary.append({"group": str(k), "n": int(len(v)), "mean": num(v.mean()), "median": num(np.median(v)), "std": num(v.std(ddof=1)),
                        "q1": num(q1), "q3": num(q3), "skewness": num(stats.skew(v, bias=False)) if len(v) > 2 and v.std() > 0 else None})
    pairs, ps = [], []
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            ks = stats.ks_2samp(vals[kept[i]], vals[kept[j]])
            pairs.append({"a": str(kept[i]), "b": str(kept[j]), "ks_d": num(ks.statistic), "p_value": num(ks.pvalue),
                          "median_difference": num(np.median(vals[kept[i]]) - np.median(vals[kept[j]]))})
            ps.append(ks.pvalue)
    for pr, adj in zip(pairs, c.benjamini_hochberg(ps)):
        pr["p_adjusted_bh"] = adj
    n = int(len(frame))
    ev = evidence_for(ctx, columns=[measure, group], n=n, method="two-sample Kolmogorov-Smirnov between groups", formula="scipy.stats.ks_2samp; BH adjustment across pairs",
                      uncertainty={"available": True, "kind": "p-value", "note": "D is the largest gap between the two groups' cumulative distributions (0 = identical, 1 = no overlap)."},
                      assumptions=[{"name": "independent observations", "status": "not_assessable", "detail": "Cannot be checked from the data."}],
                      limitations=["KS detects any difference in shape, location or spread; it does not say which.", "With many rows tiny differences become 'significant'; read D (the effect size) first.",
                                   stats_note()], practical=f"D between groups ranges {min(p['ks_d'] for p in pairs)}-{max(p['ks_d'] for p in pairs)}")
    return result("ok", {"measure": measure, "group": group, "groups": summary, "pairs": pairs}, ev, warnings)


def stats_note() -> str:
    from ..constants import P_VALUE_NOTE

    return P_VALUE_NOTE


def data_quality(ctx: OpContext) -> Dict[str, Any]:
    df = ctx.df
    n = len(df)
    issues: List[Dict[str, Any]] = []

    def add(code, severity, columns, count, message, method, rows=None, pct=None):
        issues.append({"code": code, "severity": severity, "columns": columns, "count": int(count), "pct": pct, "message": message, "method": method,
                       "row_numbers": [int(r) for r in (list(rows) if rows is not None else [])][:20]})

    for col in df.columns:
        ctx.check()
        s = df[col]
        miss = int(s.isna().sum())
        if n and miss / n * 100 >= HIGH_MISSING_PCT:
            add("MISSING_HIGH", "high" if miss / n >= 0.5 else "warning", [col], miss, f"{round(100 * miss / n, 1)}% of '{col}' is missing.",
                f"share of missing cells >= {HIGH_MISSING_PCT}%", pct=c.share(miss, n))
        bad_rows = [r for r in ctx.invalid_rows.get(col, []) if r in s.index]
        if bad_rows:
            add("INVALID_VALUES", "warning", [col], len(bad_rows), f"{len(bad_rows)} value(s) in '{col}' do not fit its {ctx.types[col]} type.",
                "present but not parseable as the column's type", rows=bad_rows, pct=c.share(len(bad_rows), n))
        present = s.dropna()
        if len(present) > 1 and present.nunique() == 1:
            add("CONSTANT_COLUMN", "info", [col], len(present), f"'{col}' has a single value, so it cannot explain any variation.", "one distinct non-missing value")
        role = role_of(ctx.types[col])
        if role == "numeric" and len(present) >= 8 and present.nunique() > 1:
            x = present.astype(float).to_numpy()
            lo, hi, _, _ = c.outlier_fences(x)
            mask = (x < lo) | (x > hi)
            if mask.any():
                share_out = mask.mean() * 100
                add("OUTLIERS", "warning" if share_out > 5 else "info", [col], mask.sum(), f"{int(mask.sum())} value(s) of '{col}' lie outside the usual range ({OUTLIER_RULE}).",
                    OUTLIER_RULE, rows=present.index[mask][:20], pct=c.share(int(mask.sum()), len(x)))
        if role == "categorical" and len(present) >= 20:
            counts = present.astype(str).value_counts()
            if len(counts) > 1 and counts.iloc[0] / len(present) >= IMBALANCE_DOMINANT_SHARE:
                add("IMBALANCED_CATEGORY", "warning", [col], int(counts.iloc[0]), f"'{col}' is dominated by one value ({round(100 * counts.iloc[0] / len(present))}%).",
                    f"largest category share >= {int(IMBALANCE_DOMINANT_SHARE * 100)}%", pct=c.share(int(counts.iloc[0]), len(present)))
            if ctx.types[col] in ("text", "mixed") and len(counts) / len(present) > 0.5 and len(counts) > 50:
                add("HIGH_CARDINALITY", "info", [col], len(counts), f"'{col}' has {len(counts)} distinct values (more than half the rows), so it may be an identifier.",
                    "distinct values > 50 and > 50% of rows")
    dup = df.duplicated(keep="first") if n else pd.Series(dtype=bool)
    if dup.any():
        add("DUPLICATE_ROWS", "warning", list(df.columns), int(dup.sum()), f"{int(dup.sum())} row(s) repeat an earlier row exactly.", "identical in every column", rows=df.index[dup][:20], pct=c.share(int(dup.sum()), n))
    issues.sort(key=lambda i: ({"high": 0, "warning": 1, "info": 2}[i["severity"]], -i["count"], i["code"], i["columns"]))
    ev = evidence_for(ctx, columns=list(df.columns), n=n, method="rule-based data-quality checks", formula="analytics.ops.descriptive.data_quality",
                      null_policy="no rows are dropped",
                      limitations=["Each check is a rule of thumb with its rule named; a flagged value is a prompt to look, not proof of an error.",
                                   "Invalid-value counts compare each value with the column's inferred type."])
    return result("ok", {"issues": issues, "checked_columns": df.shape[1], "rows_in_scope": n}, ev)


def aggregate(ctx: OpContext, agg: str, measure: Optional[str] = None, group_by: Optional[List[str]] = None, sort: str = "value_desc", top_n: int = 50,
              include_ci: bool = True) -> Dict[str, Any]:
    group_by = group_by or []
    if agg != "count" and agg != "nunique" and not measure:
        raise AnalysisError("PARAM_INVALID", f"'{agg}' needs a numeric measure column.")
    if measure and agg not in ("count", "nunique") and role_of(ctx.types[measure]) != "numeric":
        raise AnalysisError("PARAM_INVALID", f"'{agg}' needs a numeric column, but '{measure[:60]}' is {ctx.types[measure]}. Use count or nunique, or choose a numeric column.")
    cols = ([measure] if measure else []) + group_by
    df = ctx.df
    keys = [c.category(ctx, g) for g in group_by]
    frame = pd.DataFrame({f"g{i}": k for i, k in enumerate(keys)}, index=df.index)
    if measure:
        frame["m"] = df[measure].astype(float) if role_of(ctx.types[measure]) == "numeric" else df[measure]
    excluded_missing_key = int(frame[[f"g{i}" for i in range(len(group_by))]].isna().any(axis=1).sum()) if group_by else 0
    if group_by:
        frame = frame.dropna(subset=[f"g{i}" for i in range(len(group_by))])
    gnames = [f"g{i}" for i in range(len(group_by))]

    def one(sub: pd.DataFrame) -> Dict[str, Any]:
        if agg == "count":
            n = int(sub["m"].notna().sum()) if measure else int(len(sub))
            return {"n": n, "value": n}
        m = sub["m"].dropna()
        nn = int(len(m))
        if agg == "nunique":
            return {"n": nn, "value": int(m.nunique())}
        x = m.astype(float).to_numpy()
        if nn == 0:
            return {"n": 0, "value": None}
        value = {"mean": x.mean(), "sum": x.sum(), "median": np.median(x), "min": x.min(), "max": x.max(), "std": x.std(ddof=1) if nn > 1 else None}[agg]
        out = {"n": nn, "value": num(value)}
        if agg == "mean" and include_ci and nn > 1:
            half = stats.t.ppf(0.975, nn - 1) * x.std(ddof=1) / math.sqrt(nn)
            out["ci"] = [num(x.mean() - half), num(x.mean() + half)]
        return out

    rows: List[Dict[str, Any]] = []
    if gnames:
        for key, sub in frame.groupby(gnames, sort=True):
            key = key if isinstance(key, tuple) else (key,)
            r = one(sub)
            r["key"] = {group_by[i]: str(key[i]) for i in range(len(group_by))}
            rows.append(r)
        total_groups = len(rows)
        if sort == "value_desc":
            rows.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0), str(r["key"])))
        elif sort == "value_asc":
            rows.sort(key=lambda r: (r["value"] is None, (r["value"] or 0), str(r["key"])))
        elif sort == "label":
            rows.sort(key=lambda r: str(r["key"]))
        shown = rows[:top_n]
    else:
        total_groups, shown = 1, []
    overall = one(frame)
    n_used = int(overall["n"])
    is_mean = agg == "mean" and include_ci and overall.get("ci") is not None
    unc = ({"available": True, "kind": "confidence interval", "confidence": 0.95, "method": "t interval for the mean", "overall": overall.get("ci")}
           if is_mean else {"available": False, "reason": "An exact aggregate of the rows in scope; it has no sampling error unless the rows are a sample of something larger."})
    ev = evidence_for(ctx, columns=cols, n=n_used, method=f"{agg}" + (f" of {measure}" if measure else "") + (f" by {', '.join(group_by)}" if group_by else ""),
                      formula="analytics.ops.descriptive.aggregate", uncertainty=unc,
                      null_policy="rows with a missing measure are excluded from the measure's aggregate" + ("; rows with a missing group key are excluded" if group_by else ""),
                      limitations=["Group differences shown here are descriptive; use the statistical tests to ask whether they are larger than chance.",
                                   "Small groups give unstable averages: check n beside each value."])
    warnings = []
    if excluded_missing_key:
        warnings.append(f"{excluded_missing_key} row(s) with a missing group value were excluded.")
    if total_groups > len(shown) and group_by:
        warnings.append(f"Showing {len(shown)} of {total_groups} groups.")
    return result("ok", {"agg": agg, "measure": measure, "group_by": group_by, "overall": overall, "groups": shown, "groups_total": total_groups,
                         "excluded_missing_key": excluded_missing_key}, ev, warnings)


SPECS = [
    OpSpec("dataset_summary", "Dataset overview", {}, dataset_summary),
    OpSpec("column_stats", "Column statistics", {
        "columns": P("columns", role="any"), "percentiles": P("floats", lo=0, hi=100, max_items=15), "bins": P("int", lo=2, hi=200),
        "weight": P("column", role="numeric"), "group_by": P("column", role="categorical"), "top": P("int", default=20, lo=1, hi=200)}, column_stats),
    OpSpec("missing_values", "Missing values", {"columns": P("columns"), "top": P("int", default=50, lo=1, hi=500)}, missing_values),
    OpSpec("frequency", "Frequency distribution", {"column": P("column", required=True), "top": P("int", default=20, lo=1, hi=200), "bins": P("int", lo=2, hi=200)}, frequency),
    OpSpec("distribution_compare", "Distribution comparison", {"measure": P("column", required=True, role="numeric"), "group": P("column", required=True, role="categorical")},
           distribution_compare, inferential=True),
    OpSpec("data_quality", "Data-quality checks", {}, data_quality),
    OpSpec("aggregate", "Aggregate", {
        "agg": P("enum", required=True, choices=("mean", "sum", "count", "median", "min", "max", "std", "nunique")),
        "measure": P("column"), "group_by": P("columns", role="categorical", max_items=2),
        "sort": P("enum", default="value_desc", choices=("value_desc", "value_asc", "label", "none")),
        "top_n": P("int", default=50, lo=1, hi=1000), "include_ci": P("bool", default=True)}, aggregate),
]
