"""Relationship discovery: correlations, group comparisons, contingency tables and ranked pattern discovery.
Everything reports direction, magnitude, sample size, missingness treatment, effect size, intervals where supported and the
columns and filters involved. Associations are never described as causes."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from ..constants import CORRELATION_NOTE, MAX_CATEGORIES, MAX_GROUPS, MIN_N_CORR, MIN_N_GROUP
from ..errors import AnalysisError
from ..plan import OpSpec, P, role_of
from . import common as c
from .common import OpContext, evidence_for, num, result
from .hypothesis import chi_square_core, cramers_v_corrected, independence_check

RANK_WEIGHTS = {"strength": 0.40, "coverage": 0.20, "stability": 0.25, "practical": 0.15}
PRACTICAL_SCORE = {"negligible": 0.0, "small": 1 / 3, "medium": 2 / 3, "large": 1.0}
MIN_N_DISCOVER = 20
STABILITY_FOLDS = 5


# ------------------------------------------------------------------------------------------------ correlation
def _pair_correlations(x: np.ndarray, y: np.ndarray, methods: List[str]) -> Dict[str, Any]:
    n = len(x)
    out: Dict[str, Any] = {"n": int(n)}
    if n < MIN_N_CORR or x.std() == 0 or y.std() == 0:
        return {**out, "note": "not enough variation or observations"}
    if "pearson" in methods:
        r = stats.pearsonr(x, y)
        ci = r.confidence_interval(0.95)
        out["pearson"] = {"r": num(r.statistic), "p_value": num(r.pvalue), "ci": [num(ci.low), num(ci.high)], "df": n - 2}
    if "spearman" in methods:
        s = stats.spearmanr(x, y)
        ci = c.fisher_ci(float(s.statistic), n, se_scale=math.sqrt(1 + float(s.statistic) ** 2 / 2))
        out["spearman"] = {"rho": num(s.statistic), "p_value": num(s.pvalue), "ci": [num(ci[0]), num(ci[1])] if ci else None}
    if "kendall" in methods:
        k = stats.kendalltau(x, y)
        out["kendall"] = {"tau": num(k.statistic), "p_value": num(k.pvalue)}
    return out


def correlation(ctx: OpContext, columns: List[str], methods: Optional[List[str]] = None, top: int = 200) -> Dict[str, Any]:
    methods = methods or ["pearson", "spearman"]
    if len(columns) < 2:
        raise AnalysisError("PARAM_INVALID", "Choose at least two numeric columns.")
    df = ctx.df[columns].astype(float)
    if ctx.null_policy == "listwise":
        df = df.dropna()
        policy = "listwise: only rows complete in every selected column are used for every pair"
    else:
        policy = "pairwise: each pair uses the rows where both of its columns are present (n differs by pair)"
    pairs: List[Dict[str, Any]] = []
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            ctx.check()
            a, b = columns[i], columns[j]
            both = df[[a, b]].dropna()
            res = _pair_correlations(both[a].to_numpy(), both[b].to_numpy(), methods)
            primary = res.get("spearman", {}).get("rho") if "spearman" in res else res.get("pearson", {}).get("r")
            entry = {"x": a, "y": b, **res}
            if "pearson" in res:
                xs, ys = both[a].to_numpy(), both[b].to_numpy()
                hints = []
                if max(abs(stats.skew(xs)), abs(stats.skew(ys))) > 1:
                    hints.append("a column is strongly skewed")
                lo1, hi1, _, _ = c.outlier_fences(xs)
                lo2, hi2, _, _ = c.outlier_fences(ys)
                if ((xs < lo1) | (xs > hi1)).any() or ((ys < lo2) | (ys > hi2)).any():
                    hints.append("outliers are present")
                entry["pearson_caution"] = ("Pearson's r may mislead because " + " and ".join(hints) + "; compare with Spearman." if hints else None)
            if primary is not None:
                entry["direction"] = "positive" if primary > 0 else "negative" if primary < 0 else "none"
                entry["magnitude"] = c.magnitude(primary)
                entry["primary_measure"] = "spearman" if "spearman" in res else "pearson"
            pairs.append(entry)
    for key in [m for m in methods if m in ("pearson", "spearman", "kendall")]:
        adj = c.benjamini_hochberg([p.get(key, {}).get("p_value") for p in pairs])
        for p, a in zip(pairs, adj):
            if key in p:
                p[key]["p_adjusted_bh"] = a
    pairs.sort(key=lambda p: -abs((p.get("spearman", {}).get("rho") if "spearman" in p else p.get("pearson", {}).get("r")) or 0))
    primary_method = "spearman" if "spearman" in methods else methods[0]
    matrix = {"columns": columns, "method": primary_method, "values": [[None] * len(columns) for _ in columns]}
    for i in range(len(columns)):
        matrix["values"][i][i] = 1.0
    index = {col: k for k, col in enumerate(columns)}
    for p in pairs:
        v = (p.get(primary_method) or {}).get("rho" if primary_method == "spearman" else "r" if primary_method == "pearson" else "tau")
        matrix["values"][index[p["x"]]][index[p["y"]]] = v
        matrix["values"][index[p["y"]]][index[p["x"]]] = v
    n_used = int(len(df.dropna())) if ctx.null_policy == "listwise" else int(min((p["n"] for p in pairs), default=0))
    ev = evidence_for(ctx, columns=columns, n=n_used, method="correlation (" + ", ".join(methods) + ")", formula="scipy.stats.pearsonr / spearmanr / kendalltau; BH adjustment across pairs",
                      null_policy=policy, uncertainty={"available": True, "kind": "95% confidence intervals and Benjamini-Hochberg adjusted p-values", "pairs": len(pairs)},
                      assumptions=[{"name": "independent observations", "status": "not_assessable", "detail": "Cannot be checked from the data."},
                                   {"name": "Pearson: linear relationship, no dominating outliers", "status": "see per-pair caution", "detail": "Spearman is shown beside it for comparison."}],
                      limitations=[CORRELATION_NOTE, "Many pairs are tested at once, so some small p-values arise by chance; use the adjusted p-value.", "Spearman CIs use an approximation.",
                                   "A coefficient near zero does not rule out a non-monotonic relationship."], practical="Judge the size of r/rho (magnitude bands are conventions), not only its p-value.")
    return result("ok", {"pairs": pairs[:top], "pairs_total": len(pairs), "matrix": matrix, "causation_note": CORRELATION_NOTE}, ev)


# ------------------------------------------------------------------------------------------------ group comparison (descriptive + effect sizes)
def group_compare(ctx: OpContext, measure: str, group: str, reference_group: Optional[str] = None) -> Dict[str, Any]:
    frame = pd.DataFrame({"x": ctx.df[measure].astype(float), "g": c.category(ctx, group)}).dropna()
    sizes = frame.groupby("g").size().sort_values(ascending=False)
    n = int(len(frame))
    shown = list(sizes.index[:MAX_GROUPS])
    warnings = []
    if len(sizes) > MAX_GROUPS:
        warnings.append(f"Showing the {MAX_GROUPS} largest of {len(sizes)} groups.")
    if len(sizes) < 2:
        return c.not_enough(ctx, [measure, group], n, 2 * MIN_N_GROUP, "A group comparison", "group comparison")
    ref = reference_group if reference_group in shown else shown[0]
    if reference_group and reference_group not in shown:
        warnings.append("The reference group named is not among the groups shown; the largest group is the reference.")
    ref_vals = frame.loc[frame.g == ref, "x"].to_numpy()
    rows = []
    for k in shown:
        v = frame.loc[frame.g == k, "x"].to_numpy()
        row: Dict[str, Any] = {"group": str(k), "n": int(len(v)), "mean": num(v.mean()), "median": num(np.median(v)), "std": num(v.std(ddof=1)) if len(v) > 1 else None,
                               "reference": k == ref, "small_group": bool(len(v) < MIN_N_GROUP)}
        if len(v) > 1:
            half = stats.t.ppf(0.975, len(v) - 1) * v.std(ddof=1) / math.sqrt(len(v))
            row["mean_ci"] = [num(v.mean() - half), num(v.mean() + half)]
        if k != ref and len(v) >= 2 and len(ref_vals) >= 2:
            t = stats.ttest_ind(v, ref_vals, equal_var=False)
            ci = t.confidence_interval(0.95)
            g = c.hedges_g(v, ref_vals)
            row["vs_reference"] = {"mean_difference": num(v.mean() - ref_vals.mean()), "difference_ci": [num(ci.low), num(ci.high)], "hedges_g": (g or {}).get("g"),
                                   "g_ci": (g or {}).get("ci"), "welch_p": num(t.pvalue), "direction": "higher" if v.mean() > ref_vals.mean() else "lower" if v.mean() < ref_vals.mean() else "same"}
        rows.append(row)
    ps = [r["vs_reference"]["welch_p"] for r in rows if "vs_reference" in r]
    for r, adj in zip([r for r in rows if "vs_reference" in r], c.holm(ps)):
        r["vs_reference"]["welch_p_holm"] = adj
    ev = evidence_for(ctx, columns=[measure, group], n=n, method="group comparison against a reference group", formula="analytics.ops.relationships.group_compare; scipy.stats.ttest_ind(equal_var=False); Hedges' g; Holm adjustment",
                      null_policy="rows missing the measure or the group are excluded",
                      uncertainty={"available": True, "kind": "95% confidence intervals (t) for means and differences; Holm-adjusted p-values across comparisons"},
                      assumptions=[{"name": "independent observations", "status": "not_assessable", "detail": "Cannot be checked from the data."},
                                   {"name": "approximate normality of group means", "status": "not_checked_here", "detail": "Use the Statistical Analysis section for a full assumption check and test selection."}],
                      limitations=["Exploratory: this comparison does not choose a test for you or check assumptions; use the hypothesis test before drawing conclusions.", CORRELATION_NOTE],
                      practical="Compare Hedges' g with what matters in your context; conventions: 0.2 small, 0.5 medium, 0.8 large.")
    return result("ok", {"measure": measure, "group": group, "reference_group": str(ref), "groups": rows, "groups_total": int(len(sizes))}, ev, warnings)


# ------------------------------------------------------------------------------------------------ contingency
def contingency(ctx: OpContext, row: str, column: str, top: int = 15) -> Dict[str, Any]:
    a, b = c.category(ctx, row), c.category(ctx, column)
    frame = pd.DataFrame({"a": a, "b": b}).dropna()
    n = int(len(frame))
    warnings: List[str] = []
    if n < 2:
        return c.not_enough(ctx, [row, column], n, 20, "A contingency table", "contingency table")

    def lump(s: pd.Series, name: str) -> pd.Series:
        counts = s.value_counts()
        if len(counts) <= top:
            return s
        keep = set(counts.index[:top])
        warnings.append(f"'{name}' has {len(counts)} categories; the {top} most common are shown and the rest are combined as '(other)'.")
        return s.where(s.isin(keep), "(other)")

    frame["a"], frame["b"] = lump(frame["a"], row), lump(frame["b"], column)
    tab = pd.crosstab(frame["a"], frame["b"])
    table = tab.to_numpy().astype(float)
    data: Dict[str, Any] = {"row": row, "column": column, "rows": [str(x) for x in tab.index], "columns": [str(x) for x in tab.columns], "counts": table.astype(int).tolist(),
                            "row_pct": [[num(100 * v / max(1, table[i].sum()), 6) for v in table[i]] for i in range(table.shape[0])],
                            "column_pct": [[num(100 * table[i, j] / max(1, table[:, j].sum()), 6) for j in range(table.shape[1])] for i in range(table.shape[0])],
                            "row_totals": table.sum(axis=1).astype(int).tolist(), "column_totals": table.sum(axis=0).astype(int).tolist(), "n": n}
    if table.shape[0] >= 2 and table.shape[1] >= 2:
        core = chi_square_core(table)
        chi2, p, dof, _ = stats.chi2_contingency(table, correction=False)
        std = (table - core["expected"]) / np.sqrt(core["expected"])
        data["expected"] = [[num(v, 6) for v in r] for r in core["expected"]]
        data["standardised_residuals"] = [[num(v, 4) for v in r] for r in std]
        data["chi_square"] = {"statistic": num(chi2), "df": int(dof), "p_value": num(p) if core["valid"] else None, "valid": core["valid"],
                              "cramers_v_corrected": num(cramers_v_corrected(table)),
                              "note": None if core["valid"] else "Too many sparse cells for the chi-square approximation; p-value withheld. Combine categories or use the Statistical Analysis test."}
        if not core["valid"]:
            warnings.append("Chi-square assumptions are not met for this table; no p-value is reported.")
    ev = evidence_for(ctx, columns=[row, column], n=n, method="contingency table with chi-square summary", formula="pandas.crosstab; scipy.stats.chi2_contingency(correction=False)",
                      null_policy="rows missing either column are excluded", assumptions=independence_check(ctx, [row, column]),
                      uncertainty={"available": bool(data.get("chi_square") and data["chi_square"]["p_value"] is not None), "kind": "p-value (only where chi-square is valid)"},
                      limitations=[CORRELATION_NOTE, "Row and column percentages answer different questions; check which one you need.", "Rare categories are fragile."])
    return result("ok", data, ev, warnings)


# ------------------------------------------------------------------------------------------------ discovery
def _strength_numnum(x: np.ndarray, y: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(x) < MIN_N_DISCOVER or x.std() == 0 or y.std() == 0:
        return None, None, None
    s = stats.spearmanr(x, y)
    return float(s.statistic), float(s.pvalue), float(stats.pearsonr(x, y).statistic)


def _eta(x: np.ndarray, g: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    labels, inv = np.unique(g, return_inverse=True)
    if len(labels) < 2 or len(x) < MIN_N_DISCOVER:
        return None, None
    groups = [x[inv == i] for i in range(len(labels)) if (inv == i).sum() >= 2]
    if len(groups) < 2:
        return None, None
    allv = np.concatenate(groups)
    grand = allv.mean()
    sst = ((allv - grand) ** 2).sum()
    if sst == 0:
        return None, None
    ssb = sum(len(gr) * (gr.mean() - grand) ** 2 for gr in groups)
    return float(math.sqrt(ssb / sst)), float(stats.kruskal(*groups).pvalue)


def _cat_cat(a: np.ndarray, b: np.ndarray) -> Tuple[Optional[float], Optional[float], bool]:
    if len(a) < MIN_N_DISCOVER:
        return None, None, False
    tab = pd.crosstab(a, b).to_numpy().astype(float)
    if tab.shape[0] < 2 or tab.shape[1] < 2:
        return None, None, False
    core = chi_square_core(tab)
    v = cramers_v_corrected(tab)
    p = float(stats.chi2_contingency(tab, correction=False)[1]) if core["valid"] else None
    return (None if not np.isfinite(v) else float(v)), p, core["valid"]


def _measure(kind: str, a: np.ndarray, b: np.ndarray):
    if kind == "num_num":
        rho, p, r = _strength_numnum(a.astype(float), b.astype(float))
        return rho, p, True, {"pearson_r": num(r)}
    if kind == "num_cat":
        eta, p = _eta(a.astype(float), b)
        return eta, p, True, {}
    v, p, valid = _cat_cat(a, b)
    return v, p, valid, {}


def discover(ctx: OpContext, columns: Optional[List[str]] = None, target: Optional[str] = None, top_k: int = 25, min_strength: float = 0.1) -> Dict[str, Any]:
    cols = list(columns or [col for col in ctx.df.columns])
    skipped: List[Dict[str, str]] = []
    use: List[str] = []
    for col in cols:
        s = ctx.df[col]
        role = role_of(ctx.types[col])
        if s.notna().sum() < MIN_N_DISCOVER:
            skipped.append({"column": col, "reason": "too few values"})
        elif s.dropna().nunique() < 2:
            skipped.append({"column": col, "reason": "constant"})
        elif role == "datetime":
            skipped.append({"column": col, "reason": "date column (use the time-trend analysis)"})
        elif role == "categorical" and s.dropna().nunique() > MAX_CATEGORIES:
            skipped.append({"column": col, "reason": f"more than {MAX_CATEGORIES} categories (identifier-like)"})
        elif role == "numeric" and ctx.types[col] == "integer" and s.dropna().nunique() == s.notna().sum() and s.notna().sum() > 50:
            skipped.append({"column": col, "reason": "all values unique (identifier-like)"})
        else:
            use.append(col)
    if target and target not in use:
        raise AnalysisError("PARAM_INVALID", "The target column cannot be analysed (it is constant, has too few values or is identifier-like).")
    if len(use) > 40:
        skipped += [{"column": col, "reason": "beyond the 40-column limit for discovery"} for col in use[40:]]
        use = use[:40]
    total_rows = max(1, len(ctx.df))
    perm = ctx.rng(5).permutation(len(ctx.df))
    folds = np.array_split(perm, STABILITY_FOLDS)
    found: List[Dict[str, Any]] = []
    pairs = [(use[i], use[j]) for i in range(len(use)) for j in range(i + 1, len(use))]
    if target:
        pairs = [(t, o) for (t, o) in [(target, u) for u in use if u != target]]
    for a, b in pairs:
        ctx.check()
        ra, rb = role_of(ctx.types[a]), role_of(ctx.types[b])
        if ra == "numeric" and rb == "numeric":
            kind, (x, y) = "num_num", (a, b)
        elif ra == "numeric" and rb == "categorical":
            kind, (x, y) = "num_cat", (a, b)
        elif ra == "categorical" and rb == "numeric":
            kind, (x, y) = "num_cat", (b, a)
        else:
            kind, (x, y) = "cat_cat", (a, b)
        frame = ctx.df[[x, y]]
        if kind == "num_cat":
            frame = pd.DataFrame({x: ctx.df[x].astype(float), y: c.category(ctx, y)})
        elif kind == "cat_cat":
            frame = pd.DataFrame({x: c.category(ctx, x), y: c.category(ctx, y)})
        else:
            frame = frame.astype(float)
        frame = frame.dropna()
        n = len(frame)
        if n < MIN_N_DISCOVER:
            continue
        va, vb = frame[x].to_numpy(), frame[y].to_numpy()
        strength, p, valid, extra = _measure(kind, va, vb)
        if strength is None:
            continue
        # stability: the same measure on disjoint random fifths; counts folds that agree in direction and keep >= half the strength
        agree, assessable = 0, 0
        pos = {r: i for i, r in enumerate(ctx.df.index)}
        member = np.zeros(len(ctx.df), dtype=int)
        for f, idx in enumerate(folds):
            member[idx] = f
        fold_of = np.array([member[pos[r]] for r in frame.index])
        for f in range(STABILITY_FOLDS):
            sel = fold_of == f
            if sel.sum() < 10:
                continue
            s_f, _, _, _ = _measure(kind, va[sel], vb[sel])
            if s_f is None:
                continue
            assessable += 1
            same_dir = (np.sign(s_f) == np.sign(strength)) if kind == "num_num" else True
            if same_dir and abs(s_f) >= 0.5 * abs(strength):
                agree += 1
        stability = (agree / assessable) if assessable >= 3 else None
        mag = c.magnitude(strength)
        found.append({"x": x, "y": y, "kind": kind, "measure": {"num_num": "Spearman rho", "num_cat": "correlation ratio (eta)", "cat_cat": "Cramer's V (bias-corrected)"}[kind],
                      "strength": num(strength), "abs_strength": num(abs(strength)), "direction": ("positive" if strength > 0 else "negative") if kind == "num_num" else "not applicable",
                      "p_value": num(p), "p_valid": bool(valid and p is not None), "n": int(n), "coverage": num(n / total_rows), "stability": num(stability), "stability_folds": int(assessable),
                      "practical_relevance": mag, "evidence_op": {"op": "hypothesis", "kind": {"num_num": "correlation", "num_cat": "compare_groups", "cat_cat": "association"}[kind],
                                                                  "outcome": x if kind != "cat_cat" else x, "group" if kind != "num_num" else "second": y},
                      **extra})
    adj = c.benjamini_hochberg([f["p_value"] for f in found])
    for f, a in zip(found, adj):
        f["p_adjusted_bh"] = a
    excluded = []
    kept = []
    for f in found:
        if f["abs_strength"] < min_strength:
            excluded.append({**{k: f[k] for k in ("x", "y")}, "reason": f"strength {f['abs_strength']} is below the {min_strength} threshold"})
        elif f["stability"] is None:
            excluded.append({**{k: f[k] for k in ("x", "y")}, "reason": "too few rows to assess stability"})
        elif f["p_adjusted_bh"] is not None and f["p_adjusted_bh"] >= 0.05:
            excluded.append({**{k: f[k] for k in ("x", "y")}, "reason": "not distinguishable from chance after adjusting for the number of pairs examined"})
        elif f["p_valid"] is False:
            excluded.append({**{k: f[k] for k in ("x", "y")}, "reason": "the significance test's assumptions are not met (sparse table)"})
        else:
            f["score"] = num(RANK_WEIGHTS["strength"] * min(f["abs_strength"] / 0.5, 1.0) + RANK_WEIGHTS["coverage"] * f["coverage"]
                             + RANK_WEIGHTS["stability"] * f["stability"] + RANK_WEIGHTS["practical"] * PRACTICAL_SCORE[f["practical_relevance"]])
            f["score_components"] = {"strength": num(min(f["abs_strength"] / 0.5, 1.0)), "coverage": f["coverage"], "stability": f["stability"], "practical": num(PRACTICAL_SCORE[f["practical_relevance"]])}
            kept.append(f)
    kept.sort(key=lambda f: (-f["score"], f["x"], f["y"]))
    ev = evidence_for(ctx, columns=use + ([target] if target else []), n=int(len(ctx.df)), method="ranked pairwise pattern discovery",
                      formula="analytics.ops.relationships.discover; Spearman / correlation ratio / bias-corrected Cramer's V; BH adjustment; 5-fold agreement for stability",
                      null_policy="each pair uses rows where both columns are present (pairwise)",
                      uncertainty={"available": True, "kind": "Benjamini-Hochberg adjusted p-values over all pairs examined", "pairs_examined": len(found)},
                      assumptions=[{"name": "independent observations", "status": "not_assessable", "detail": "Cannot be checked from the data."}],
                      limitations=[CORRELATION_NOTE, f"{len(found)} pairs were examined; patterns can arise by chance, which is why only adjusted-significant, stable, non-trivial ones are listed.",
                                   "The ranking weights (strength 40%, coverage 20%, stability 25%, practical relevance 15%) are a design choice, shown in each score_components.",
                                   "Stability is agreement across five random subsets of the same data, not an independent replication."],
                      practical="Practical relevance bands use Cohen-style conventions on the strength measure.", extra={"seed": ctx.seed})
    data = {"patterns": kept[:top_k], "patterns_total": len(kept), "pairs_examined": len(found), "excluded": excluded[:50], "excluded_total": len(excluded), "skipped_columns": skipped,
            "ranking": {"weights": RANK_WEIGHTS, "min_strength": min_strength}, "causation_note": CORRELATION_NOTE}
    if not kept:
        data["message"] = "Not enough evidence."
    return result("ok", data, ev, [])


SPECS = [
    OpSpec("correlation", "Correlation", {"columns": P("columns", required=True, role="numeric", max_items=30),
                                          "methods": P("enums", choices=("pearson", "spearman", "kendall")), "top": P("int", default=200, lo=1, hi=1000)}, correlation, inferential=True),
    OpSpec("group_compare", "Group comparison", {"measure": P("column", required=True, role="numeric"), "group": P("column", required=True, role="categorical"),
                                                 "reference_group": P("str")}, group_compare, inferential=True),
    OpSpec("contingency", "Contingency table", {"row": P("column", required=True, role="categorical"), "column": P("column", required=True, role="categorical"),
                                                "top": P("int", default=15, lo=2, hi=MAX_CATEGORIES)}, contingency, inferential=True),
    OpSpec("discover", "Relationship discovery", {"columns": P("columns", max_items=60), "target": P("column"), "top_k": P("int", default=25, lo=1, hi=100),
                                                  "min_strength": P("float", default=0.1, lo=0.0, hi=0.9)}, discover, inferential=True),
]
