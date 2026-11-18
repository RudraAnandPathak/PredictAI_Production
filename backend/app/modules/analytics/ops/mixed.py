"""Mixed-variable association (Phase 5): ONE operation that looks at the types of two columns and chooses the right family of methods.

    numeric  + numeric      Pearson or Spearman (chosen from normality, outliers and linearity checks) with a Fisher-z interval
    category + numeric      correlation ratio eta (bootstrap interval) with the group test the data allow: Welch t / Mann-Whitney (2 groups),
                            Welch ANOVA / one-way ANOVA / Kruskal-Wallis (3+ groups), plus the usual effect size for that test
    category + category     chi-square (Fisher's exact for sparse 2x2 tables) with bias-corrected Cramer's V and a bootstrap interval

It does not re-implement any statistic: the tests, assumption checks and effect sizes come from `hypothesis.py`, and the plain pair measures
from `relationships.py`; this module only (1) decides the pair type, (2) refuses invalid combinations with a clear message, (3) adds
eta and its interval, and (4) returns every case in one uniform shape. `pair_measure` is also what the predictive workflow uses to screen
features against a target, so the two parts of the product can never disagree."""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..constants import CORRELATION_NOTE, MAX_CATEGORIES, MIN_N_CORR
from ..errors import AnalysisError
from ..plan import OpSpec, P, role_of
from . import common as c
from .common import OpContext, evidence_for, num, result
from .hypothesis import SMALL_SAMPLE, association, compare_groups, correlation_test
from .relationships import _cat_cat, _eta, _strength_numnum

PAIR_TYPES = {("numeric", "numeric"): "numeric_numeric", ("categorical", "numeric"): "categorical_numeric", ("numeric", "categorical"): "categorical_numeric",
              ("categorical", "categorical"): "categorical_categorical"}
WHY = {
    "numeric_numeric": "Both columns are numeric, so a correlation is the right family. Pearson is used when both look roughly normal, free of strong outliers and linear; otherwise rank-based Spearman.",
    "categorical_numeric": "One column is a category and the other numeric, so the question is how much the numbers differ between the categories. Eta measures that share, and the group test is chosen from the data.",
    "categorical_categorical": "Both columns are categories, so the question is whether the mix of one depends on the other: chi-square (Fisher's exact when 2 x 2 counts are sparse) with Cramer's V as the effect size.",
}


# ------------------------------------------------------------------------------------------------ roles, with explicit refusals
def resolve_role(ctx: OpContext, column: str, treat_as: str) -> str:
    natural = role_of(ctx.types[column])
    if natural == "datetime":
        raise AnalysisError("MIXED_INVALID", f"'{column}' is a date column. Dates are not compared directly here: use the time-trend analysis, or analyse a number or category derived from the date.")
    series = ctx.df[column].dropna()
    if treat_as == "numeric":
        if natural != "numeric":
            raise AnalysisError("MIXED_INVALID", f"'{column}' contains text, so it cannot be treated as numeric.")
        return "numeric"
    if treat_as == "categorical":
        if series.nunique() > MAX_CATEGORIES:
            raise AnalysisError("TOO_MANY_CATEGORIES", f"'{column}' has more than {MAX_CATEGORIES} distinct values, which is too many to use as categories. Group the values first or choose another column.")
        return "categorical"
    return natural


def _check_usable(ctx: OpContext, column: str, role: str) -> None:
    series = ctx.df[column].dropna()
    if series.nunique() < 2:
        raise AnalysisError("CONSTANT_COLUMN", f"'{column}' has a single value among the rows in scope, so it cannot be associated with anything.")
    if role == "categorical" and series.nunique() > MAX_CATEGORIES:
        raise AnalysisError("TOO_MANY_CATEGORIES", f"'{column}' has more than {MAX_CATEGORIES} distinct values, which is too many to use as categories. Group the values first or choose another column.")


# ------------------------------------------------------------------------------------------------ pair measure (shared with the predictive workflow)
def _eta_from_codes(x: np.ndarray, codes: np.ndarray, k: int) -> Optional[float]:
    counts = np.bincount(codes, minlength=k).astype(float)
    use = counts > 0
    if use.sum() < 2:
        return None
    sums = np.bincount(codes, weights=x, minlength=k)
    grand = x.mean()
    means = np.divide(sums, counts, out=np.zeros_like(sums), where=use)
    sst = float(((x - grand) ** 2).sum())
    if sst == 0:
        return None
    ssb = float((counts * (means - grand) ** 2).sum())
    return math.sqrt(max(0.0, min(1.0, ssb / sst)))


def eta_with_ci(x: np.ndarray, g: np.ndarray, rng: np.random.Generator, resamples: int = 500, limit: int = 20000) -> Tuple[Optional[float], Optional[List[Optional[float]]]]:
    labels, codes = np.unique(g, return_inverse=True)
    k = len(labels)
    value = _eta_from_codes(x, codes, k)
    if value is None or len(x) > limit:
        return value, None
    n = len(x)
    boots: List[float] = []
    for _ in range(resamples):
        idx = rng.integers(0, n, n)
        b = _eta_from_codes(x[idx], codes[idx], k)
        if b is not None:
            boots.append(b)
    if len(boots) < resamples * 0.9:
        return value, None
    return value, [num(np.percentile(boots, 2.5)), num(np.percentile(boots, 97.5))]


def pair_measure(a: pd.Series, b: pd.Series, a_role: str, b_role: str, rng: np.random.Generator) -> Optional[Dict[str, Any]]:
    """A compact association measure for two aligned series (already complete-case, categories as labels). Returns None when it cannot be computed.
    Used by the predictive workflow to screen features against the target; it applies the same measures as the operation above."""
    n = len(a)
    if n < MIN_N_CORR:
        return None
    kind = PAIR_TYPES.get((a_role, b_role))
    if kind == "numeric_numeric":
        rho, p, pearson = _strength_numnum(a.to_numpy(float), b.to_numpy(float))
        if rho is None:
            return None
        ci = c.fisher_ci(rho, n, se_scale=math.sqrt(1 + rho ** 2 / 2))
        return {"pair_type": kind, "measure": "Spearman rho", "value": num(rho), "abs_value": num(abs(rho)), "ci": [num(ci[0]), num(ci[1])] if ci else None, "p_value": num(p), "n": n}
    if kind == "categorical_numeric":
        num_s, cat_s = (a, b) if a_role == "numeric" else (b, a)
        value, ci = eta_with_ci(num_s.to_numpy(float), cat_s.astype(str).to_numpy(), rng, resamples=200)
        if value is None:
            return None
        _eta_v, p = _eta(num_s.to_numpy(float), cat_s.astype(str).to_numpy())
        return {"pair_type": kind, "measure": "eta (correlation ratio)", "value": num(value), "abs_value": num(value), "ci": ci, "p_value": num(p), "n": n}
    if kind == "categorical_categorical":
        v, p, valid = _cat_cat(a.astype(str).to_numpy(), b.astype(str).to_numpy())
        if v is None:
            return None
        return {"pair_type": kind, "measure": "Cramer's V (bias-corrected)", "value": num(v), "abs_value": num(abs(v)), "ci": None, "p_value": num(p) if valid else None, "n": n, "p_valid": bool(valid)}
    return None


# ------------------------------------------------------------------------------------------------ the operation
def mixed_association(ctx: OpContext, x: str, y: str, x_as: str = "auto", y_as: str = "auto", alpha: float = 0.05, prefer: str = "auto") -> Dict[str, Any]:
    if x == y:
        raise AnalysisError("MIXED_INVALID", "Choose two different columns.")
    rx, ry = resolve_role(ctx, x, x_as), resolve_role(ctx, y, y_as)
    _check_usable(ctx, x, rx)
    _check_usable(ctx, y, ry)
    pair = PAIR_TYPES[(rx, ry)]
    # work on a copy where an explicit "treat as category" turns a numeric column into labels, so the unchanged Phase 4 functions see the intended type
    df, types = ctx.df, dict(ctx.types)
    for col, wanted in ((x, rx), (y, ry)):
        if wanted == "categorical" and role_of(types[col]) == "numeric":
            df = df.copy()
            df[col] = c.category(ctx, col)
            types[col] = "text"
    sub = dataclasses.replace(ctx, df=df, types=types)
    complete = df[[x, y]].dropna()
    n = int(len(complete))
    missing_excluded = int(len(df) - n)
    need = {"numeric_numeric": MIN_N_CORR, "categorical_numeric": 10, "categorical_categorical": 20}[pair]
    if n < need:
        return c.not_enough(ctx, [x, y], n, need, "A mixed-variable association", "mixed-variable association")
    rng = ctx.rng(31)
    detail: Dict[str, Any]
    measure: Dict[str, Any]
    if pair == "numeric_numeric":
        inner = correlation_test(sub, x, y, alpha, "two-sided", prefer, True)
        if inner["status"] != "ok":
            return inner
        data = inner["data"]
        res = data["result"]
        est = res["estimate"]
        measure = {"name": est["name"], "value": est["value"], "ci": est["ci"], "confidence": est.get("confidence"), "ci_method": est.get("ci_method"), "scale": "-1 to +1"}
        both = _strength_numnum(complete[x].to_numpy(float), complete[y].to_numpy(float))
        detail = {"pearson_r": num(both[2]), "spearman_rho": num(both[0])}
        test = {"name": res["test"], "statistic": res["statistic"], "statistic_name": res["statistic_name"], "df": res.get("df"), "p_value": res["p_value"]}
        direction, strength = res.get("direction"), abs(est["value"] or 0.0)
    elif pair == "categorical_numeric":
        numeric_col, group_col = (x, y) if rx == "numeric" else (y, x)
        inner = compare_groups(sub, numeric_col, group_col, alpha, "two-sided", prefer, None, True)
        if inner["status"] != "ok":
            return inner
        data = inner["data"]
        res = data["result"]
        eta, eta_ci = eta_with_ci(complete[numeric_col].to_numpy(float), complete[group_col].astype(str).to_numpy(), rng)
        if eta is None:
            raise AnalysisError("CONSTANT_COLUMN", "The numeric column has no variation, so the correlation ratio is undefined.")
        measure = {"name": "eta (correlation ratio)", "value": num(eta), "ci": eta_ci, "confidence": 0.95 if eta_ci else None,
                   "ci_method": "percentile bootstrap, 500 resamples, fixed seed" if eta_ci else "not computed for samples above 20,000 values", "scale": "0 to 1"}
        detail = {"group_test_effect_size": res.get("effect_size"), "groups": data["design"].get("groups")}
        test = {"name": res["test"], "statistic": res["statistic"], "statistic_name": res["statistic_name"], "df": res.get("df"), "p_value": res["p_value"]}
        direction, strength = None, eta
    else:
        inner = association(sub, x, y, alpha, True)
        if inner["status"] != "ok":
            return inner
        data = inner["data"]
        res = data["result"]
        es = res["effect_size"]
        measure = {"name": es["name"], "value": es["value"], "ci": es["ci"], "confidence": es.get("confidence"), "ci_method": es.get("ci_method"), "scale": "0 to 1"}
        detail = {"table": data.get("table"), "notable_cells": res.get("notable_cells"), "odds_ratio": res.get("odds_ratio")}
        test = {"name": res["test"], "statistic": res["statistic"], "statistic_name": res["statistic_name"], "df": res.get("df"), "p_value": res["p_value"]}
        direction, strength = None, abs(es["value"] or 0.0)
    test["significant"] = bool(test["p_value"] is not None and test["p_value"] < alpha)
    iev = inner.get("evidence") or {}
    ev = evidence_for(ctx, columns=[x, y], n=n, method=f"mixed-variable association ({pair.replace('_', ' + ')}): {test['name']}", formula="analytics.ops.mixed -> " + str(iev.get("formula_ref", "")),
                      uncertainty={"available": True, "kind": "confidence interval and p-value", "estimate_ci": measure["ci"]}, assumptions=iev.get("assumptions", []),
                      limitations=list(iev.get("limitations", [])) + [CORRELATION_NOTE], practical=iev.get("practical_relevance"),
                      null_policy="rows missing either column are excluded (complete cases)", extra={"test_selection": data.get("selected_test"), "seed": ctx.seed})
    out = {"x": x, "y": y, "x_role": rx, "y_role": ry, "pair_type": pair, "why": WHY[pair], "measure": measure, "test": test, "strength": num(strength), "direction": direction,
           "n": n, "missing_excluded": missing_excluded, "small_sample": bool(n < SMALL_SAMPLE), "alpha": alpha, "assumption_checks": data.get("assumption_checks", []),
           "selected_test": data.get("selected_test"), "detail": detail, "inner_decision": res.get("decision"), "causation_note": CORRELATION_NOTE}
    return result("ok", out, ev, list(inner.get("warnings", [])))


SPECS = [
    OpSpec("mixed_association", "Mixed-variable association", {
        "x": P("column", required=True), "y": P("column", required=True),
        "x_as": P("enum", default="auto", choices=("auto", "numeric", "categorical")), "y_as": P("enum", default="auto", choices=("auto", "numeric", "categorical")),
        "alpha": P("float", default=0.05, lo=0.001, hi=0.2), "prefer": P("enum", default="auto", choices=("auto", "parametric", "nonparametric"))}, mixed_association, inferential=True),
]
