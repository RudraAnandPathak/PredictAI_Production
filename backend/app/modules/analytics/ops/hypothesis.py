"""The statistical testing engine. A hypothesis is turned into a structured plan (H0/H1, design, assumption checks, test choice) and
only then, if asked, executed. A test is never run as though an unchecked assumption held: unmet assumptions change the test
or stop it, and unverifiable ones (independence) are listed as such. Every result carries test statistic, df, p-value,
effect size, confidence interval where supported, sample size, missing-data policy, assumptions and limitations."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import contingency

from ..constants import MAX_CATEGORIES, MAX_GROUPS, MIN_N_CORR, MIN_N_GROUP, NOT_ENOUGH_EVIDENCE, P_VALUE_NOTE
from ..errors import AnalysisError
from ..plan import OpSpec, P, role_of
from . import common as c
from .common import OpContext, evidence_for, num, result

NORMALITY_RULE_LARGE_N = "for n > 500 formal tests flag trivial departures, so normality is judged by |skewness| < 1 and |excess kurtosis| < 3"
SMALL_SAMPLE = 30


def check(name: str, status: str, detail: str, **extra: Any) -> Dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, **{k: v for k, v in extra.items() if v is not None}}


def normality_check(x: np.ndarray, label: str, alpha: float) -> Dict[str, Any]:
    n = len(x)
    if n < 8:
        return check(f"normality of {label}", "not_assessable", f"n = {n}: too few values to assess the shape.")
    if x.std() == 0:
        return check(f"normality of {label}", "not_met", "All values are identical.")
    sk, ku = float(stats.skew(x, bias=False)), float(stats.kurtosis(x, bias=False))
    if n <= 500:
        res = stats.shapiro(x)
        ok = res.pvalue >= alpha
        return check(f"normality of {label}", "met" if ok else "not_met", f"Shapiro-Wilk W = {num(res.statistic, 4)}, p = {num(res.pvalue, 4)} (n = {n}).",
                     statistic=num(res.statistic), p_value=num(res.pvalue), skewness=num(sk), excess_kurtosis=num(ku))
    ok = abs(sk) < 1 and abs(ku) < 3
    return check(f"normality of {label}", "met" if ok else "not_met", f"skewness {num(sk, 3)}, excess kurtosis {num(ku, 3)} (n = {n}); {NORMALITY_RULE_LARGE_N}.",
                 skewness=num(sk), excess_kurtosis=num(ku))


def variance_check(groups: List[np.ndarray], alpha: float) -> Dict[str, Any]:
    if any(len(g) < 2 for g in groups) or all(g.std() == 0 for g in groups):
        return check("equal variances", "not_assessable", "A group has fewer than 2 values or no variation.")
    res = stats.levene(*groups, center="median")
    return check("equal variances", "met" if res.pvalue >= alpha else "not_met",
                 f"Brown-Forsythe/Levene W = {num(res.statistic, 4)}, p = {num(res.pvalue, 4)}.", statistic=num(res.statistic), p_value=num(res.pvalue))


def independence_check(ctx: OpContext, columns: List[str]) -> List[Dict[str, Any]]:
    out = [check("independent observations", "not_assessable", "Cannot be verified from the data. Rows must not be repeated measurements of the same person or unit; "
                 "if they are, these tests overstate certainty. Use the paired test for before/after measurements.")]
    dup = int(ctx.df[columns].duplicated().sum()) if len(ctx.df) else 0
    if dup and len(ctx.df) and dup / len(ctx.df) > 0.05:
        out.append(check("repeated rows", "warning", f"{dup} rows ({round(100 * dup / len(ctx.df), 1)}%) repeat another row exactly in these columns, which may mean duplicated or dependent observations."))
    return out


def _boot_ci(ctx: OpContext, stat, arrays: List[np.ndarray], salt: int, b: int = 1000, limit: int = 20000) -> Optional[List[Optional[float]]]:
    if sum(len(a) for a in arrays) > limit:
        return None
    rng = ctx.rng(salt)
    vals = []
    for _ in range(b):
        ctx.check()
        vals.append(stat([a[rng.integers(0, len(a), len(a))] for a in arrays]))
    vals = np.array([v for v in vals if v is not None and np.isfinite(v)])
    if len(vals) < b * 0.9:
        return None
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return [num(lo), num(hi)]


def _rank_biserial(a: np.ndarray, b: np.ndarray) -> float:
    u = stats.mannwhitneyu(a, b, alternative="two-sided").statistic
    return 2 * u / (len(a) * len(b)) - 1


def _holm_pairs(rows: List[Dict[str, Any]]) -> None:
    for row, adj in zip(rows, c.holm([r["p_value"] for r in rows])):
        row["p_adjusted_holm"] = adj


# ------------------------------------------------------------------------------------------------ group comparison
def _welch_anova(groups: List[np.ndarray]) -> Tuple[float, float, float, float]:
    k = len(groups)
    n = np.array([len(g) for g in groups], dtype=float)
    m = np.array([g.mean() for g in groups])
    v = np.array([g.var(ddof=1) for g in groups])
    w = n / v
    sw = w.sum()
    mw = (w * m).sum() / sw
    a = (w * (m - mw) ** 2).sum() / (k - 1)
    t = ((1 - w / sw) ** 2 / (n - 1)).sum()
    b = 1 + 2 * (k - 2) / (k ** 2 - 1) * t
    f = a / b
    df2 = (k ** 2 - 1) / (3 * t)
    return float(f), float(k - 1), float(df2), float(stats.f.sf(f, k - 1, df2))


def _eta_squared(groups: List[np.ndarray]) -> float:
    allv = np.concatenate(groups)
    grand = allv.mean()
    ssb = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    sst = ((allv - grand) ** 2).sum()
    return float(ssb / sst) if sst > 0 else float("nan")


def compare_groups(ctx: OpContext, outcome: str, group: str, alpha: float, alternative: str, prefer: str, reference_group: Optional[str], execute: bool) -> Dict[str, Any]:
    if role_of(ctx.types[outcome]) != "numeric":
        raise AnalysisError("HYPOTHESIS_INCOMPLETE", "Comparing groups needs a numeric outcome column.")
    g = c.category(ctx, group)
    frame = pd.DataFrame({"x": ctx.df[outcome].astype(float), "g": g}).dropna()
    n_total = int(len(frame))
    sizes = frame.groupby("g").size().sort_values(ascending=False)
    warnings: List[str] = []
    if len(sizes) < 2:
        return c.not_enough(ctx, [outcome, group], n_total, 2 * MIN_N_GROUP, "Comparing groups (two groups with data are needed)", "group comparison")
    usable = [k for k in sizes.index if sizes[k] >= MIN_N_GROUP]
    dropped_small = len(sizes) - len(usable)
    if dropped_small:
        warnings.append(f"{dropped_small} group(s) with fewer than {MIN_N_GROUP} values cannot be tested and were left out.")
    if len(usable) < 2:
        return c.not_enough(ctx, [outcome, group], n_total, 2 * MIN_N_GROUP, "Comparing groups", "group comparison")
    if len(usable) > MAX_GROUPS:
        warnings.append(f"Only the {MAX_GROUPS} largest of {len(usable)} groups were compared.")
        usable = usable[:MAX_GROUPS]
    ref = reference_group if reference_group in usable else usable[0]
    if reference_group and reference_group not in usable:
        warnings.append("The reference group you named has too few values or does not exist; the largest group is the reference instead.")
    order = [ref] + [k for k in usable if k != ref]
    arrays = [frame.loc[frame.g == k, "x"].to_numpy() for k in order]
    sizes_list = [int(len(a)) for a in arrays]
    n = sum(sizes_list)
    checks: List[Dict[str, Any]] = []
    for k, a in zip(order, arrays):
        checks.append(normality_check(a, f"'{outcome}' in group {str(k)[:40]}", alpha))
    checks.append(variance_check(arrays, alpha))
    checks.append(check("sample size", "met" if min(sizes_list) >= SMALL_SAMPLE else "warning",
                        f"smallest group n = {min(sizes_list)}" + ("" if min(sizes_list) >= SMALL_SAMPLE else f"; below {SMALL_SAMPLE}, results are fragile and rely on the data's shape.")))
    ratio = max(sizes_list) / min(sizes_list)
    checks.append(check("group balance", "met" if ratio <= 4 else "warning", f"largest/smallest group = {round(ratio, 1)}" + ("" if ratio <= 4 else "; strongly unequal groups reduce power and make unequal variances matter more.")))
    checks += independence_check(ctx, [outcome, group])
    normal_ok = all(ch["status"] == "met" for ch in checks[:len(arrays)])
    var_ok = checks[len(arrays)]["status"] == "met"
    big = min(sizes_list) >= SMALL_SAMPLE
    skew_heavy = any(abs(stats.skew(a, bias=False)) > 2 for a in arrays if len(a) > 2 and a.std() > 0)
    # ---- test selection
    two = len(arrays) == 2
    if prefer == "parametric":
        param = True
        why = "You asked for a parametric test."
    elif prefer == "nonparametric":
        param = False
        why = "You asked for a rank-based test."
    elif normal_ok or (big and not (skew_heavy and min(sizes_list) < 100)):
        param = True
        why = ("Groups look approximately normal." if normal_ok else f"Every group has at least {SMALL_SAMPLE} values, so the mean comparison is robust to moderate non-normality.")
    else:
        param = False
        why = "A group is small and clearly non-normal or heavily skewed, so a rank-based test is used instead of one that assumes normality."
    if two:
        name = "Welch's t-test" if param else "Mann-Whitney U test"
    else:
        name = ("Welch's ANOVA" if not var_ok else "One-way ANOVA") if param else "Kruskal-Wallis H test"
        if param and not var_ok:
            why += " Variances differ between groups, so Welch's version (which does not assume equal variances) is used."
    if param and not normal_ok and not big:
        warnings.append("A parametric test was chosen although normality is not established in a small group; interpret with care.")
    labels = [str(k) for k in order]
    null = ("The mean of '%s' is the same in every group." % outcome) if param else ("Values of '%s' tend to be neither higher nor lower in one group than another (stochastic equality)." % outcome)
    plan_data: Dict[str, Any] = {
        "hypothesis": {"kind": "compare_groups", "alpha": alpha, "alternative": alternative if two else "two-sided",
                       "statement": f"'{outcome}' differs between the groups of '{group}'.", "null": null,
                       "alternative_text": "The groups differ." if two else "At least one group differs from the others."},
        "design": {"outcome": outcome, "group": group, "groups": [{"name": lab, "n": k} for lab, k in zip(labels, sizes_list)], "reference_group": labels[0],
                   "comparison_direction": f"{labels[1]} minus {labels[0]}" if two else "each group against the reference", "observations": "independent (assumed; not verifiable)"},
        "assumption_checks": checks, "selected_test": {"name": name, "why": why, "alternatives_considered": ["Welch's t-test / ANOVA", "Mann-Whitney / Kruskal-Wallis"]},
        "executed": False, "result": None,
    }
    if two and alternative != "two-sided":
        plan_data["hypothesis"]["alternative_text"] = f"{labels[1]} is {'higher' if alternative == 'greater' else 'lower'} than {labels[0]}."
    limitations = ["Independence of observations cannot be verified from the data.", "Assumption checks are themselves tests; with small samples they are weak, with huge samples they flag trivial departures.",
                   "A statistically detectable difference can be too small to matter: read the effect size.", c_note()]
    if not execute:
        ev = evidence_for(ctx, columns=[outcome, group], n=n, method=f"plan for {name}", formula="analytics.ops.hypothesis.compare_groups (plan only)", assumptions=checks, limitations=limitations,
                          null_policy="rows missing the outcome or the group are excluded", uncertainty={"available": False, "reason": "No test has been run yet."})
        return result("ok", plan_data, ev, warnings)
    # ---- execution
    res_data: Dict[str, Any]
    if two:
        a, b = arrays[1], arrays[0]       # difference = non-reference minus reference
        mean_diff = float(a.mean() - b.mean())
        if param:
            t = stats.ttest_ind(a, b, equal_var=False, alternative=alternative)
            ci = t.confidence_interval(0.95)
            eff = c.hedges_g(a, b)
            res_data = {"test": name, "statistic": num(t.statistic), "statistic_name": "t", "df": num(t.df), "p_value": num(t.pvalue),
                        "estimate": {"name": f"mean({labels[1]}) - mean({labels[0]})", "value": num(mean_diff), "ci": [num(ci.low), num(ci.high)], "confidence": 0.95,
                                     "ci_method": "Welch t interval" + (" (one-sided tests give a one-sided bound)" if alternative != "two-sided" else "")},
                        "effect_size": {"name": "Hedges' g", **(eff or {"g": None})}}
            eff_value = (eff or {}).get("g")
            practical = _band_d(eff_value)
        else:
            u = stats.mannwhitneyu(a, b, alternative=alternative)
            rb = 2 * u.statistic / (len(a) * len(b)) - 1
            ci_rb = _boot_ci(ctx, lambda s: _rank_biserial(s[0], s[1]), [a, b], 11)
            res_data = {"test": name, "statistic": num(u.statistic), "statistic_name": "U", "df": None, "p_value": num(u.pvalue),
                        "estimate": {"name": "probability that a random value in the first group exceeds one in the reference (ties count half)", "value": num(u.statistic / (len(a) * len(b)))},
                        "effect_size": {"name": "rank-biserial correlation", "value": num(rb), "ci": ci_rb, "confidence": 0.95 if ci_rb else None,
                                        "ci_method": "percentile bootstrap, 1000 resamples, fixed seed" if ci_rb else "not computed for samples above 20,000 values"}}
            practical = _band_d(rb * 2 if rb is not None else None, rank=True)
    else:
        if param:
            if var_ok:
                f = stats.f_oneway(*arrays)
                dfn, dfd = len(arrays) - 1, n - len(arrays)
                stat, p = float(f.statistic), float(f.pvalue)
            else:
                stat, dfn, dfd, p = _welch_anova(arrays)
            eta = _eta_squared(arrays)
            ci_eta = _boot_ci(ctx, lambda s: _eta_squared(s), arrays, 12, b=500)
            res_data = {"test": name, "statistic": num(stat), "statistic_name": "F", "df": [num(dfn), num(dfd)], "p_value": num(p),
                        "effect_size": {"name": "eta squared (share of variance explained by group)", "value": num(eta), "ci": ci_eta, "confidence": 0.95 if ci_eta else None,
                                        "ci_method": "percentile bootstrap, 500 resamples within groups, fixed seed" if ci_eta else "not computed for samples above 20,000 values"}}
            practical = _band_eta(eta)
        else:
            h = stats.kruskal(*arrays)
            eps = float(h.statistic * (n + 1) / (n ** 2 - 1))
            res_data = {"test": name, "statistic": num(h.statistic), "statistic_name": "H", "df": num(len(arrays) - 1), "p_value": num(h.pvalue),
                        "effect_size": {"name": "epsilon squared", "value": num(eps), "ci": None, "confidence": None, "ci_method": "not computed"}}
            practical = _band_eta(eps)
        if res_data["p_value"] is not None and res_data["p_value"] < alpha:
            post = []
            for i in range(1, len(arrays)):
                x, y = arrays[i], arrays[0]
                if param:
                    t = stats.ttest_ind(x, y, equal_var=False)
                    post.append({"group": labels[i], "versus": labels[0], "p_value": num(t.pvalue), "difference": num(x.mean() - y.mean()), "effect": num((c.hedges_g(x, y) or {}).get("g")), "effect_name": "Hedges' g"})
                else:
                    u = stats.mannwhitneyu(x, y)
                    post.append({"group": labels[i], "versus": labels[0], "p_value": num(u.pvalue), "difference": num(np.median(x) - np.median(y)), "effect": num(2 * u.statistic / (len(x) * len(y)) - 1), "effect_name": "rank-biserial"})
            _holm_pairs(post)
            res_data["post_hoc"] = {"comparisons": post, "adjustment": "Holm", "note": "Each group is compared with the reference group only; run other comparisons separately and adjust for the number you make."}
    res_data["decision"] = _decision(res_data["p_value"], alpha, practical)
    plan_data["executed"] = True
    plan_data["result"] = res_data
    unc = {"available": True, "kind": "confidence interval and p-value", "effect_size_ci": res_data["effect_size"].get("ci"), "estimate_ci": (res_data.get("estimate") or {}).get("ci")}
    ev = evidence_for(ctx, columns=[outcome, group], n=n, method=name, formula="scipy.stats." + {"Welch's t-test": "ttest_ind(equal_var=False)", "Mann-Whitney U test": "mannwhitneyu", "Welch's ANOVA": "analytics.ops.hypothesis._welch_anova",
                      "One-way ANOVA": "f_oneway", "Kruskal-Wallis H test": "kruskal"}[name], uncertainty=unc, assumptions=checks, limitations=limitations, practical=practical,
                      null_policy="rows missing the outcome or the group are excluded (complete cases)", extra={"test_selection": plan_data["selected_test"], "seed": ctx.seed})
    return result("ok", plan_data, ev, warnings)


def c_note() -> str:
    return P_VALUE_NOTE


def _band_d(g: Optional[float], rank: bool = False) -> Optional[str]:
    if g is None:
        return None
    a = abs(g)
    label = "negligible" if a < 0.2 else "small" if a < 0.5 else "medium" if a < 0.8 else "large"
    return f"{label} effect by Cohen's conventions (|{'d-equivalent' if rank else 'g'}| = {round(a, 2)}); conventions are rough guides, judge against your own context."


def _band_eta(e: Optional[float]) -> Optional[str]:
    if e is None:
        return None
    label = "negligible" if e < 0.01 else "small" if e < 0.06 else "medium" if e < 0.14 else "large"
    return f"{label} effect by Cohen's conventions ({round(e * 100, 1)}% of the variation is associated with group); conventions are rough guides."


def _decision(p: Optional[float], alpha: float, practical: Optional[str]) -> Dict[str, Any]:
    if p is None:
        return {"reject_null": None, "wording": "No p-value could be computed."}
    reject = p < alpha
    return {"reject_null": bool(reject), "alpha": alpha, "p_value": p,
            "wording": (f"p = {p} is below the chosen threshold of {alpha}: the data are unlikely if the null hypothesis were true." if reject
                        else f"p = {p} is not below the chosen threshold of {alpha}: the data do not provide enough evidence against the null hypothesis. This is not evidence that there is no difference."),
            "practical_relevance": practical, "p_value_note": P_VALUE_NOTE}


# ------------------------------------------------------------------------------------------------ association (chi-square)
def cramers_v_corrected(table: np.ndarray) -> float:
    n = table.sum()
    r, k = table.shape
    if n <= 1 or min(r, k) < 2:
        return float("nan")
    chi2 = stats.chi2_contingency(table, correction=False)[0]
    phi2 = chi2 / n
    phi2c = max(0.0, phi2 - (r - 1) * (k - 1) / (n - 1))
    rc, kc = r - (r - 1) ** 2 / (n - 1), k - (k - 1) ** 2 / (n - 1)
    d = min(rc - 1, kc - 1)
    return float(math.sqrt(phi2c / d)) if d > 0 else float("nan")


def chi_square_core(table: np.ndarray) -> Dict[str, Any]:
    """Expected counts and the validity of the chi-square approximation for one contingency table."""
    expected = contingency.expected_freq(table)
    low = float((expected < 5).mean())
    return {"expected": expected, "share_expected_below_5": low, "min_expected": float(expected.min()), "valid": bool(low <= 0.2 and expected.min() >= 1)}


def association(ctx: OpContext, outcome: str, group: str, alpha: float, execute: bool) -> Dict[str, Any]:
    a, b = c.category(ctx, outcome), c.category(ctx, group)
    frame = pd.DataFrame({"a": a, "b": b}).dropna()
    n = int(len(frame))
    if n < 20:
        return c.not_enough(ctx, [outcome, group], n, 20, "A chi-square test of independence", "chi-square test")
    table_df = pd.crosstab(frame["a"], frame["b"])
    r, k = table_df.shape
    if r < 2 or k < 2:
        raise AnalysisError("HYPOTHESIS_INCOMPLETE", "Both columns need at least two different values among the rows in scope.")
    if r > MAX_CATEGORIES or k > MAX_CATEGORIES:
        raise AnalysisError("TOO_MANY_CATEGORIES", f"A column has more than {MAX_CATEGORIES} categories; combine rare categories or choose a column with fewer.")
    table = table_df.to_numpy().astype(float)
    core = chi_square_core(table)
    two_by_two = r == 2 and k == 2
    checks = [check("sample size", "met" if n >= 40 else "warning", f"n = {n}" + ("" if n >= 40 else "; small samples make approximations unreliable.")),
              check("expected counts (chi-square validity)", "met" if core["valid"] else "not_met",
                    f"{round(100 * core['share_expected_below_5'], 1)}% of cells have an expected count below 5 (smallest {num(core['min_expected'], 3)}); the usual rule needs at most 20% below 5 and none below 1.")]
    checks += independence_check(ctx, [outcome, group])
    min_cell = min(table_df.sum(axis=0).min(), table_df.sum(axis=1).min())
    if min_cell / n < 0.05:
        checks.append(check("category balance", "warning", "a category holds under 5% of the rows, so conclusions about it rest on few observations."))
    if two_by_two:
        name = "Chi-square test of independence" if core["valid"] else "Fisher's exact test"
        why = "Expected counts are adequate." if core["valid"] else "Some expected counts are below 5, so the exact test is used instead of the chi-square approximation."
    else:
        name = "Chi-square test of independence"
        why = "Two categorical variables." if core["valid"] else "Chi-square is not valid here (too many sparse cells) and no exact alternative is run for tables larger than 2 x 2."
    plan_data = {"hypothesis": {"kind": "association", "alpha": alpha, "alternative": "two-sided", "statement": f"'{outcome}' and '{group}' are associated.",
                                "null": f"'{outcome}' and '{group}' are independent (the distribution of one does not depend on the other).", "alternative_text": "They are not independent."},
                 "design": {"outcome": outcome, "group": group, "levels": [int(r), int(k)], "observations": "independent (assumed; not verifiable)"},
                 "assumption_checks": checks, "selected_test": {"name": name, "why": why, "alternatives_considered": ["Fisher's exact test (2 x 2)"]},
                 "executed": False, "result": None,
                 "table": {"rows": [str(x) for x in table_df.index], "columns": [str(x) for x in table_df.columns], "counts": table.astype(int).tolist(),
                           "expected": [[num(v, 6) for v in row] for row in core["expected"]]}}
    limitations = ["Association is not causation.", "Independence of observations cannot be verified from the data.", "The chi-square statistic grows with sample size; read the effect size (Cramer's V).", P_VALUE_NOTE]
    if not execute:
        ev = evidence_for(ctx, columns=[outcome, group], n=n, method=f"plan for {name}", formula="analytics.ops.hypothesis.association (plan only)", assumptions=checks, limitations=limitations,
                          uncertainty={"available": False, "reason": "No test has been run yet."}, null_policy="rows missing either column are excluded")
        return result("ok", plan_data, ev)
    if not core["valid"] and not two_by_two:
        plan_data["result"] = {"test": name, "not_run_reason": "ASSUMPTIONS_NOT_MET",
                               "message": "The chi-square test was not run because too many expected counts are small. Combine rare categories, collect more data, or choose columns with fewer categories.",
                               "p_value": None}
        ev = evidence_for(ctx, columns=[outcome, group], n=n, method=name + " (not run)", formula="not run", assumptions=checks, limitations=limitations,
                          uncertainty={"available": False, "reason": "Assumptions were not met, so no p-value is reported."}, null_policy="rows missing either column are excluded")
        return result("not_run", plan_data, ev, ["The test was not run because its assumptions are not met."])
    res: Dict[str, Any] = {"test": name}
    if two_by_two and not core["valid"]:
        odds, p = stats.fisher_exact(table)
        res.update({"statistic": num(odds), "statistic_name": "odds ratio (sample)", "df": None, "p_value": num(p)})
    else:
        chi2, p, dof, _ = stats.chi2_contingency(table, correction=False)
        res.update({"statistic": num(chi2), "statistic_name": "chi-square", "df": int(dof), "p_value": num(p)})
    v = cramers_v_corrected(table)
    rng = ctx.rng(21)
    boots = []
    probs = (table / n).ravel()
    for _ in range(1000):
        sample = rng.multinomial(n, probs).reshape(table.shape).astype(float)
        if (sample.sum(axis=0) > 0).all() and (sample.sum(axis=1) > 0).all():
            boots.append(cramers_v_corrected(sample))
    boots = [x for x in boots if np.isfinite(x)]
    ci = [num(np.percentile(boots, 2.5)), num(np.percentile(boots, 97.5))] if len(boots) >= 900 else None
    res["effect_size"] = {"name": "Cramer's V (bias-corrected)", "value": num(v), "ci": ci, "confidence": 0.95 if ci else None,
                          "ci_method": "multinomial bootstrap of the table, 1000 resamples, fixed seed" if ci else "not computed (unstable resamples)"}
    if two_by_two:
        t = table + (0.5 if (table == 0).any() else 0)
        orr = (t[0, 0] * t[1, 1]) / (t[0, 1] * t[1, 0])
        se = math.sqrt((1 / t).sum())
        res["odds_ratio"] = {"value": num(orr), "ci": [num(math.exp(math.log(orr) - 1.96 * se)), num(math.exp(math.log(orr) + 1.96 * se))], "confidence": 0.95,
                             "ci_method": "Woolf logit interval" + (" with 0.5 added to every cell because a cell was zero" if (table == 0).any() else ""),
                             "orientation": f"odds of '{table_df.columns[0]}' for '{table_df.index[0]}' relative to '{table_df.index[1]}'"}
    std_res = (table - core["expected"]) / np.sqrt(core["expected"])
    cells = [{"row": str(table_df.index[i]), "column": str(table_df.columns[j]), "observed": int(table[i, j]), "expected": num(core["expected"][i, j], 6), "std_residual": num(std_res[i, j], 4)}
             for i in range(r) for j in range(k) if abs(std_res[i, j]) >= 2]
    res["notable_cells"] = {"cells": sorted(cells, key=lambda x: -abs(x["std_residual"]))[:15], "rule": "|standardised residual| >= 2 (cells that depart most from independence)"}
    practical = None if v is None or not np.isfinite(v) else f"{'negligible' if v < 0.1 else 'small' if v < 0.3 else 'medium' if v < 0.5 else 'large'} association by common conventions (V = {round(v, 2)})."
    res["decision"] = _decision(res["p_value"], alpha, practical)
    plan_data["executed"], plan_data["result"] = True, res
    ev = evidence_for(ctx, columns=[outcome, group], n=n, method=name, formula="scipy.stats.chi2_contingency(correction=False)" if res["statistic_name"] == "chi-square" else "scipy.stats.fisher_exact",
                      uncertainty={"available": True, "kind": "confidence interval and p-value", "effect_size_ci": ci}, assumptions=checks, limitations=limitations, practical=practical,
                      null_policy="rows missing either column are excluded (complete cases)", extra={"seed": ctx.seed})
    return result("ok", plan_data, ev)


# ------------------------------------------------------------------------------------------------ correlation and paired
def correlation_test(ctx: OpContext, x_col: str, y_col: str, alpha: float, alternative: str, prefer: str, execute: bool) -> Dict[str, Any]:
    for col in (x_col, y_col):
        if role_of(ctx.types[col]) != "numeric":
            raise AnalysisError("HYPOTHESIS_INCOMPLETE", "A correlation test needs two numeric columns.")
    frame = c.pair_frame(ctx, [x_col, y_col]).astype(float)
    n = int(len(frame))
    if n < MIN_N_CORR:
        return c.not_enough(ctx, [x_col, y_col], n, MIN_N_CORR, "A correlation test", "correlation test")
    x, y = frame[x_col].to_numpy(), frame[y_col].to_numpy()
    if x.std() == 0 or y.std() == 0:
        raise AnalysisError("CONSTANT_COLUMN", "One of the columns has a single value, so a correlation is undefined.")
    checks = [normality_check(x, f"'{x_col}'", alpha), normality_check(y, f"'{y_col}'", alpha)]
    ox = ((x < c.outlier_fences(x)[0]) | (x > c.outlier_fences(x)[1])).mean()
    oy = ((y < c.outlier_fences(y)[0]) | (y > c.outlier_fences(y)[1])).mean()
    checks.append(check("outliers", "met" if max(ox, oy) <= 0.01 else "warning", f"{round(100 * ox, 1)}% / {round(100 * oy, 1)}% of values are outside the Tukey fences; Pearson's r is sensitive to outliers."))
    rho_probe = float(stats.spearmanr(x, y).statistic)
    r_probe = float(np.corrcoef(x, y)[0, 1])
    checks.append(check("linearity / monotonic agreement", "met" if abs(rho_probe - r_probe) < 0.2 else "warning",
                        f"Pearson r = {num(r_probe, 3)} and Spearman rho = {num(rho_probe, 3)}; a large gap suggests outliers or a non-linear (still monotonic) relationship."))
    checks.append(check("sample size", "met" if n >= SMALL_SAMPLE else "warning", f"n = {n}" + ("" if n >= SMALL_SAMPLE else f"; below {SMALL_SAMPLE}, confidence intervals are wide.")))
    checks += independence_check(ctx, [x_col, y_col])
    normal_ok = checks[0]["status"] == "met" and checks[1]["status"] == "met"
    use_pearson = prefer == "parametric" or (prefer == "auto" and normal_ok and max(ox, oy) <= 0.01 and abs(rho_probe - r_probe) < 0.2)
    name = "Pearson correlation test" if use_pearson else "Spearman rank correlation test"
    why = ("Both variables look approximately normal with no strong outliers and the relationship looks linear." if use_pearson and prefer == "auto"
           else "You asked for Pearson." if use_pearson else "You asked for a rank-based test." if prefer == "nonparametric"
           else "Normality, outliers or non-linearity make the rank-based measure safer than Pearson.")
    plan_data = {"hypothesis": {"kind": "correlation", "alpha": alpha, "alternative": alternative, "statement": f"'{x_col}' and '{y_col}' are correlated.",
                                "null": "The population correlation is zero.", "alternative_text": {"two-sided": "It is not zero.", "greater": "It is greater than zero.", "less": "It is less than zero."}[alternative]},
                 "design": {"x": x_col, "y": y_col, "observations": "independent (assumed; not verifiable)"}, "assumption_checks": checks,
                 "selected_test": {"name": name, "why": why, "alternatives_considered": ["Pearson", "Spearman"]}, "executed": False, "result": None}
    limitations = ["Correlation is not causation: it describes how two variables move together, not why.", "Pearson measures straight-line association only; Spearman measures monotonic association.",
                   "Independence of observations cannot be verified; time series and repeated measures break it.", P_VALUE_NOTE]
    if not execute:
        ev = evidence_for(ctx, columns=[x_col, y_col], n=n, method=f"plan for {name}", formula="analytics.ops.hypothesis.correlation_test (plan only)", assumptions=checks, limitations=limitations,
                          uncertainty={"available": False, "reason": "No test has been run yet."}, null_policy="rows missing either column are excluded")
        return result("ok", plan_data, ev)
    if use_pearson:
        res = stats.pearsonr(x, y, alternative=alternative)
        ci = res.confidence_interval(0.95)
        out = {"test": name, "statistic": num(res.statistic), "statistic_name": "r", "df": n - 2, "p_value": num(res.pvalue),
               "estimate": {"name": "Pearson r", "value": num(res.statistic), "ci": [num(ci.low), num(ci.high)], "confidence": 0.95, "ci_method": "Fisher z transformation"},
               "effect_size": {"name": "r squared (share of variance shared)", "value": num(res.statistic ** 2)}}
        r_for_band = res.statistic
    else:
        res = stats.spearmanr(x, y, alternative=alternative)
        ci = c.fisher_ci(float(res.statistic), n, se_scale=math.sqrt(1 + float(res.statistic) ** 2 / 2))
        out = {"test": name, "statistic": num(res.statistic), "statistic_name": "rho", "df": None, "p_value": num(res.pvalue),
               "estimate": {"name": "Spearman rho", "value": num(res.statistic), "ci": [num(ci[0]), num(ci[1])] if ci else None, "confidence": 0.95 if ci else None,
                            "ci_method": "Fisher z with the Bonett-Wright standard error (approximate)"},
               "effect_size": {"name": "rho squared", "value": num(res.statistic ** 2)}}
        r_for_band = res.statistic
    out["direction"] = "positive" if r_for_band > 0 else "negative" if r_for_band < 0 else "none"
    practical = f"{c.magnitude(r_for_band)} association by common conventions (|r| = {round(abs(r_for_band), 2)})."
    out["decision"] = _decision(out["p_value"], alpha, practical)
    out["causation_note"] = "An association in observed data. It does not show that one variable causes the other."
    plan_data["executed"], plan_data["result"] = True, out
    ev = evidence_for(ctx, columns=[x_col, y_col], n=n, method=name, formula="scipy.stats.pearsonr" if use_pearson else "scipy.stats.spearmanr",
                      uncertainty={"available": True, "kind": "confidence interval and p-value", "estimate_ci": out["estimate"]["ci"]}, assumptions=checks, limitations=limitations,
                      practical=practical, null_policy="rows missing either column are excluded (complete cases)", extra={"test_selection": plan_data["selected_test"]})
    return result("ok", plan_data, ev)


def paired_test(ctx: OpContext, first: str, second: str, alpha: float, alternative: str, prefer: str, execute: bool) -> Dict[str, Any]:
    for col in (first, second):
        if role_of(ctx.types[col]) != "numeric":
            raise AnalysisError("HYPOTHESIS_INCOMPLETE", "A paired test needs two numeric columns measured on the same units.")
    frame = c.pair_frame(ctx, [first, second]).astype(float)
    n = int(len(frame))
    if n < 8:
        return c.not_enough(ctx, [first, second], n, 8, "A paired test", "paired test")
    d = (frame[first] - frame[second]).to_numpy()
    if np.all(d == d[0]):
        raise AnalysisError("CONSTANT_DIFFERENCE", "Every pair differs by exactly the same amount, so there is no variation to test.")
    checks = [normality_check(d, "the paired differences", alpha), check("sample size", "met" if n >= SMALL_SAMPLE else "warning", f"n = {n} pairs"),
              check("pairing", "not_assessable", "You are asserting that each row measures the same unit twice (for example before and after). The data cannot confirm this.")]
    skew_heavy = abs(stats.skew(d, bias=False)) > 2
    param = prefer == "parametric" or (prefer == "auto" and (checks[0]["status"] == "met" or (n >= SMALL_SAMPLE and not (skew_heavy and n < 100))))
    name = "Paired t-test" if param else "Wilcoxon signed-rank test"
    why = "The differences look approximately normal (or the sample is large)." if param else "The differences are clearly non-normal in a small sample, so a rank-based test is used."
    plan_data = {"hypothesis": {"kind": "paired", "alpha": alpha, "alternative": alternative, "statement": f"'{first}' and '{second}' differ on the same units.",
                                "null": "The mean difference is zero." if param else "The distribution of differences is symmetric about zero.", "alternative_text": {"two-sided": "It is not zero.", "greater": f"'{first}' is higher than '{second}'.", "less": f"'{first}' is lower than '{second}'."}[alternative]},
                 "design": {"first": first, "second": second, "pairs": n, "difference": f"{first} minus {second}"}, "assumption_checks": checks,
                 "selected_test": {"name": name, "why": why, "alternatives_considered": ["Paired t-test", "Wilcoxon signed-rank"]}, "executed": False, "result": None}
    limitations = ["Pairs must be independent of each other and each pair must be the same unit measured twice.", "A difference over time can reflect anything that changed in between, not only the thing you care about.", P_VALUE_NOTE]
    if not execute:
        ev = evidence_for(ctx, columns=[first, second], n=n, method=f"plan for {name}", formula="analytics.ops.hypothesis.paired_test (plan only)", assumptions=checks, limitations=limitations,
                          uncertainty={"available": False, "reason": "No test has been run yet."}, null_policy="rows missing either column are excluded")
        return result("ok", plan_data, ev)
    if param:
        t = stats.ttest_rel(frame[first], frame[second], alternative=alternative)
        ci = t.confidence_interval(0.95)
        dz = float(d.mean() / d.std(ddof=1))
        se = math.sqrt(1 / n + dz ** 2 / (2 * n))
        out = {"test": name, "statistic": num(t.statistic), "statistic_name": "t", "df": int(t.df), "p_value": num(t.pvalue),
               "estimate": {"name": f"mean({first} - {second})", "value": num(d.mean()), "ci": [num(ci.low), num(ci.high)], "confidence": 0.95, "ci_method": "t interval"},
               "effect_size": {"name": "Cohen's dz", "value": num(dz), "ci": [num(dz - 1.96 * se), num(dz + 1.96 * se)], "confidence": 0.95, "ci_method": "normal approximation"}}
        practical = _band_d(dz)
    else:
        w = stats.wilcoxon(d, alternative=alternative, zero_method="wilcox")
        nz = d[d != 0]
        ranks = stats.rankdata(np.abs(nz))
        rb = float((ranks[nz > 0].sum() - ranks[nz < 0].sum()) / ranks.sum()) if len(nz) else 0.0
        out = {"test": name, "statistic": num(w.statistic), "statistic_name": "W", "df": None, "p_value": num(w.pvalue),
               "estimate": {"name": f"median({first} - {second})", "value": num(np.median(d))},
               "effect_size": {"name": "matched-pairs rank-biserial correlation", "value": num(rb), "ci": None, "confidence": None, "ci_method": "not computed"}}
        practical = _band_d(rb * 2, rank=True)
    out["decision"] = _decision(out["p_value"], alpha, practical)
    plan_data["executed"], plan_data["result"] = True, out
    ev = evidence_for(ctx, columns=[first, second], n=n, method=name, formula="scipy.stats.ttest_rel" if param else "scipy.stats.wilcoxon",
                      uncertainty={"available": True, "kind": "confidence interval and p-value", "estimate_ci": out["estimate"].get("ci")}, assumptions=checks, limitations=limitations,
                      practical=practical, null_policy="pairs with either value missing are excluded")
    return result("ok", plan_data, ev)


def hypothesis(ctx: OpContext, kind: str, outcome: Optional[str] = None, group: Optional[str] = None, second: Optional[str] = None, alpha: float = 0.05,
               alternative: str = "two-sided", prefer: str = "auto", reference_group: Optional[str] = None, family_size: int = 1, execute: bool = True) -> Dict[str, Any]:
    if outcome is None:
        raise AnalysisError("HYPOTHESIS_INCOMPLETE", "Incomplete hypothesis: choose the outcome column.")
    if kind in ("compare_groups", "association") and group is None:
        raise AnalysisError("HYPOTHESIS_INCOMPLETE", "Incomplete hypothesis: choose the grouping column to compare across.")
    if kind in ("correlation", "paired") and second is None:
        raise AnalysisError("HYPOTHESIS_INCOMPLETE", "Incomplete hypothesis: choose the second numeric column.")
    if outcome == (group or second):
        raise AnalysisError("HYPOTHESIS_INCOMPLETE", "Incomplete hypothesis: the two columns must be different.")
    if kind == "compare_groups":
        if role_of(ctx.types[group]) == "numeric" and ctx.df[group].nunique() > MAX_CATEGORIES:
            raise AnalysisError("HYPOTHESIS_INCOMPLETE", "The grouping column has too many distinct numeric values to be a group; choose a category column.")
        out = compare_groups(ctx, outcome, group, alpha, alternative, prefer, reference_group, execute)
    elif kind == "association":
        out = association(ctx, outcome, group, alpha, execute)
    elif kind == "correlation":
        out = correlation_test(ctx, outcome, second, alpha, alternative, prefer, execute)
    elif kind == "paired":
        out = paired_test(ctx, outcome, second, alpha, alternative, prefer, execute)
    else:
        raise AnalysisError("HYPOTHESIS_INCOMPLETE", "Choose a kind of hypothesis.")
    data = out["data"]
    data["p_value_note"] = P_VALUE_NOTE
    res = data.get("result")
    if execute and res and res.get("p_value") is not None and family_size > 1:
        p = res["p_value"]
        data["multiple_testing"] = {"family_size": family_size, "p_bonferroni": num(min(1.0, p * family_size)),
                                    "note": f"Adjusted for {family_size} tests: the chance of at least one false alarm grows with the number of tests, so judge this result against the adjusted p-value."}
        out["warnings"].append(f"Multiple testing: with {family_size} tests the adjusted p-value is {data['multiple_testing']['p_bonferroni']}.")
    elif execute and res:
        data["multiple_testing"] = {"family_size": 1, "note": "A single pre-specified test. If you test many hypotheses on the same data, adjust for the number of tests; the more you try, the more likely a chance 'finding' becomes."}
    return out


SPECS = [
    OpSpec("hypothesis", "Statistical hypothesis test", {
        "kind": P("enum", required=True, choices=("compare_groups", "association", "correlation", "paired")),
        "outcome": P("column"), "group": P("column", role="categorical"), "second": P("column", role="numeric"),
        "alpha": P("float", default=0.05, lo=0.001, hi=0.2), "alternative": P("enum", default="two-sided", choices=("two-sided", "greater", "less")),
        "prefer": P("enum", default="auto", choices=("auto", "parametric", "nonparametric")), "reference_group": P("str"),
        "family_size": P("int", default=1, lo=1, hi=10000), "execute": P("bool", default=True)}, hypothesis, inferential=True),
]
