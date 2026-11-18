"""Explainers for the Phase 4 analytics operations, the Phase 5 mixed-variable operation, insights and assistant answers.
Each reads the operation's own structured result and writes plain English beside it. Nothing here changes a result."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .core import CAUSATION, Draft, Say, Unverifiable, explain, explainer, terms_for

TYPE_GLOSS = {"float": "decimal numbers", "integer": "whole numbers", "text": "words or categories", "boolean": "yes/no values", "date": "dates", "datetime": "dates and times", "mixed": "a mixture of numbers and text"}


def _q(say: Say, name: Any) -> str:
    return f"'{say.lit(name)}'"


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return one if n == 1 else (many or one + "s")


def _strength_word(value: Optional[float], kind: str = "r") -> str:
    """Rule-of-thumb words for an association strength (Cohen-style conventions); stated as a convention, never as a finding."""
    if value is None:
        return "an unquantified"
    v = abs(value)
    cuts = (0.1, 0.3, 0.5) if kind in ("r", "v") else (0.1, 0.3, 0.5)
    if v < cuts[0]:
        return "a negligible"
    if v < cuts[1]:
        return "a weak"
    if v < cuts[2]:
        return "a moderate"
    return "a strong"


def _direction(word: Optional[str]) -> str:
    return {"positive": "positive (they rise together)", "negative": "negative (as one rises the other falls)"}.get(word or "", "")


# ------------------------------------------------------------------------------------------------ descriptive
@explainer("op:dataset_summary")
def _dataset_summary(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    rows, cols = say.count(d["rows_in_scope"]), say.count(d["columns"])
    points: List[str] = []
    if d["rows_in_scope"] != d["rows_in_dataset"]:
        points.append(f"Filters or sampling leave {rows} of the dataset's {say.count(d['rows_in_dataset'])} rows in scope; every figure below is about those rows only.")
    kinds = [f"{say.count(n)} {say.lit(t)} ({TYPE_GLOSS.get(t, 'other values')})" for t, n in d["column_types"].items()]
    if kinds:
        points.append("The columns are made up of " + ", ".join(kinds) + ".")
    mc = d["missing_cells"]
    points.append("No cell is empty." if mc["count"] == 0 else
                  f"{say.count(mc['count'])} cells ({say.pct(mc['pct'])} of all cells) are empty, and {say.count(d['complete_rows'])} rows have no empty cell at all.")
    dup = d["duplicate_rows"]
    points.append("No row is an exact copy of an earlier row." if dup["count"] == 0 else
                  f"{say.count(dup['count'])} rows ({say.pct(dup['pct'])}) are exact copies of an earlier row, which can make results look more certain than they are.")
    return Draft(f"The data in scope has {rows} rows and {cols} columns.", points)


@explainer("op:missing_values")
def _missing_values(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    any_missing = d["columns_with_any_missing"]
    if any_missing == 0:
        return Draft("No column has missing values.", ["Every calculation can use every row."])
    summary = f"{say.count(any_missing)} of {say.count(d['columns_total'])} columns {_plural(any_missing, 'has', 'have')} missing values."
    worst = [c for c in d["columns"] if c["missing"] > 0][:3]
    points = [f"{_q(say, c['column'])} is empty in {say.count(c['missing'])} rows ({say.pct(c['pct'])})." for c in worst]
    points.append("A calculation that uses a column leaves out the rows where that column is empty (see the null policy in the evidence).")
    return Draft(summary, points, cautions=["If the empty rows differ from the rest in some systematic way, results based on the non-empty rows can be biased."])


@explainer("op:data_quality")
def _data_quality(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    issues = d["issues"]
    checked = say.count(d["checked_columns"])
    if not issues:
        return Draft(f"None of the checks flagged a problem across {checked} columns.", ["The checks look for constant columns, duplicate rows, outliers, mixed types and similar issues."])
    n = len(issues)
    warnings = sum(1 for i in issues if i.get("severity") == "warning")
    summary = (f"The checks across {checked} columns flagged {say.derived(n, from_values=[d['checked_columns']], digits=0)} {_plural(n, 'item')}"
               + (f", {say.derived(warnings, from_values=[d['checked_columns']], digits=0)} of them warnings." if warnings else "."))
    points = [say.quote(i["message"]) for i in issues[:5]]
    return Draft(summary, points, cautions=["A flag is a prompt to look, not proof of an error: an outlier or a repeated value may be perfectly genuine."])


@explainer("op:column_stats")
def _column_stats(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    cols = [c for c in r["data"]["columns"] if c.get("mean") is not None]
    if not cols:
        return None
    points: List[str] = []
    for c in cols[:4]:
        line = (f"{_q(say, c['column'])}: the middle value (median) is {say.num(c['median'])} and the average (mean) is {say.num(c['mean'])}; "
                f"values run from {say.num(c['min'])} to {say.num(c['max'])}, and the middle half spans {say.num(c['iqr'])} (IQR).")
        sk = c.get("skewness")
        if isinstance(sk, (int, float)) and abs(sk) >= 1 and c.get("non_missing", 0) >= 30:
            line += (" A few unusually large values pull the average above the median, so the median is the better 'typical' value." if sk > 0 else
                     " A few unusually small values pull the average below the median, so the median is the better 'typical' value.")
        out = c.get("outliers") or {}
        if out.get("count"):
            line += f" {say.count(out['count'])} {_plural(out['count'], 'value')} ({say.pct(out['pct'], 2)}) {_plural(out['count'], 'lies', 'lie')} outside the usual range."
        points.append(line)
    summary = f"{say.items(len(cols))} numeric {_plural(len(cols), 'column')} summarised: where the typical value sits and how widely values spread."
    return Draft(summary, points, terms_for("mean", "median", "IQR", "skewness"),
                 ["These figures describe this dataset's rows. They are not estimates about a wider population unless the rows are a fair sample of it."])


@explainer("op:frequency")
def _frequency(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    rows = d["rows"]
    if not rows:
        return None
    top = rows[0]
    summary = f"{_q(say, d['column'])} has {say.count(d['distinct'])} distinct {_plural(d['distinct'], 'value')}; the most common is {_q(say, top['value'])} ({say.count(top['count'])} rows, {say.pct(top['pct'])})."
    points = [f"{_q(say, x['value'])}: {say.count(x['count'])} rows ({say.pct(x['pct'])})." for x in rows[1:4]]
    if d.get("missing"):
        points.append(f"{say.count(d['missing'])} rows have no value in this column and are not counted above.")
    return Draft(summary, points)


@explainer("op:distribution_compare")
def _distribution_compare(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    groups = [g for g in d["groups"] if g.get("median") is not None]
    if len(groups) < 2:
        return None
    hi, lo = max(groups, key=lambda g: g["median"]), min(groups, key=lambda g: g["median"])
    summary = f"{_q(say, d['measure'])} is compared across {say.items(len(groups))} groups of {_q(say, d['group'])}."
    points = [f"The group with the highest median is {_q(say, hi['group'])} ({say.num(hi['median'])}, n = {say.count(hi['n'])}); the lowest is {_q(say, lo['group'])} ({say.num(lo['median'])}, n = {say.count(lo['n'])})."]
    pairs = [p for p in d.get("pairs", []) if p.get("p_adjusted_bh") is not None]
    if pairs:
        flagged = sum(1 for p in pairs if p["p_adjusted_bh"] < 0.05)
        points.append(f"Comparing the shape of the distributions pair by pair, {say.derived(flagged, from_values=[pairs[0]['p_adjusted_bh']], digits=0)} of {say.items(len(pairs))} pairs differ "
                      f"after correcting for repeated testing (adjusted p below {say.const(0.05, 'conventional threshold')}, the usual convention).")
    return Draft(summary, points, terms_for("median", "p-value"), ["A difference between groups is a pattern in these rows; it does not explain why the groups differ."])


@explainer("op:aggregate")
def _aggregate(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    ov = d.get("overall") or {}
    measure = d.get("measure")
    what = f"the {say.lit(d['agg'])} of {_q(say, measure)}" if measure else f"the {say.lit(d['agg'])}"
    summary = f"Overall, {what} is {say.num(ov['value'])} across {say.count(ov['n'])} rows." if ov.get("value") is not None else f"{what.capitalize()} was calculated."
    points: List[str] = []
    groups = [g for g in d.get("groups", []) if g.get("value") is not None]
    if groups:
        hi, lo = max(groups, key=lambda g: g["value"]), min(groups, key=lambda g: g["value"])
        label = lambda g: ", ".join(f"{say.lit(v)}" for v in (g.get("key") or {}).values()) or "all"  # noqa: E731
        points.append(f"Across {say.count(d['groups_total'])} {_plural(d['groups_total'], 'group')}, the highest is {_q(say, label(hi))} ({say.num(hi['value'])}) and the lowest is {_q(say, label(lo))} ({say.num(lo['value'])}).")
    if ov.get("ci"):
        points.append(f"The overall figure carries a confidence interval from {say.num(ov['ci'][0])} to {say.num(ov['ci'][1])}: the true average for a wider population is plausibly in that range if these rows are a fair sample.")
    return Draft(summary, points, terms_for("confidence interval"))


# ------------------------------------------------------------------------------------------------ relationships
@explainer("op:correlation")
def _correlation(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    pairs = d["pairs"]
    if not pairs:
        return None

    def primary(p):
        use = p.get("spearman") if p.get("primary_measure") == "spearman" else p.get("pearson")
        if not use:
            return None
        return ("Spearman rho", use.get("rho"), use) if p.get("primary_measure") == "spearman" else ("Pearson r", use.get("r"), use)

    scored = [(abs(m[1]), p, m) for p in pairs for m in [primary(p)] if m and m[1] is not None]
    scored.sort(key=lambda t: -t[0])
    points: List[str] = []
    cautions = [CAUSATION, "Words like 'weak' or 'strong' follow a common rule of thumb; what counts as strong depends on the field."]
    for _abs, p, (name, value, use) in scored[:3]:
        ci = use.get("ci")
        ci_text = f", {say.conf()} CI {say.num(ci[0])} to {say.num(ci[1])}" if ci and ci[0] is not None and ci[1] is not None else ""
        gloss = (f", so higher {_q(say, p['x'])} tends to go with higher {_q(say, p['y'])}" if p.get("direction") == "positive" else
                 f", so higher {_q(say, p['x'])} tends to go with lower {_q(say, p['y'])}" if p.get("direction") == "negative" else "")
        label = "positive " if p.get("direction") == "positive" else "negative " if p.get("direction") == "negative" else ""
        points.append(f"{_q(say, p['x'])} and {_q(say, p['y'])}: {_strength_word(value)} {label}relationship{gloss} ({name} = {say.num(value)}{ci_text}, n = {say.count(p['n'])}).")
        if p.get("pearson_caution"):
            note = say.quote(p["pearson_caution"], cap=True)
            if note not in cautions:
                cautions.append(note)
    if not points:
        return None
    total = d["pairs_total"]
    shown = len(points)
    summary = f"{say.count(total)} {_plural(total, 'pair')} of numeric columns {_plural(total, 'was', 'were')} compared; the {'strongest' if shown == 1 else say.items(shown) + ' strongest'} {_plural(shown, 'is', 'are')} listed below."
    return Draft(summary, points, terms_for("correlation", "Spearman rho", "Pearson r", "confidence interval"), cautions)


@explainer("op:group_compare")
def _group_compare(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    groups = [g for g in d["groups"] if g.get("mean") is not None]
    if len(groups) < 2:
        return None
    hi, lo = max(groups, key=lambda g: g["mean"]), min(groups, key=lambda g: g["mean"])
    summary = f"{_q(say, d['measure'])} is summarised for {say.count(d['groups_total'])} groups of {_q(say, d['group'])}, with {_q(say, d['reference_group'])} as the reference group." if d.get("reference_group") else \
        f"{_q(say, d['measure'])} is summarised for {say.count(d['groups_total'])} groups of {_q(say, d['group'])}."
    points = [f"The highest average is in {_q(say, hi['group'])} ({say.num(hi['mean'])}, n = {say.count(hi['n'])}); the lowest is in {_q(say, lo['group'])} ({say.num(lo['mean'])}, n = {say.count(lo['n'])})."]
    small = [g for g in groups if g.get("small_group")]
    if small:
        points.append(f"{say.derived(len(small), from_values=[groups[0]['n']], digits=0)} {_plural(len(small), 'group')} {_plural(len(small), 'is', 'are')} small, so {_plural(len(small), 'its average is', 'their averages are')} less reliable.")
    return Draft(summary, points, terms_for("mean", "standard deviation", "confidence interval"), ["A gap between group averages may be due to chance; the statistical test (a separate analysis) is what addresses that."])


@explainer("op:contingency")
def _contingency(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    chi = d.get("chi_square") or {}
    v = chi.get("cramers_v_corrected")
    summary = f"{_q(say, d['row'])} and {_q(say, d['column'])} are cross-tabulated across {say.count(d['n'])} rows."
    points: List[str] = []
    if v is not None:
        points.append(f"{_strength_word(v, 'v').capitalize()} association (Cramér's V = {say.num(v)}; zero means unrelated, one means perfectly related).")
    if chi.get("p_value") is not None and chi.get("valid"):
        points.append(f"The chi-square test gives {say.p(chi['p_value'])}: how surprising a table this lopsided would be if the two columns were really unrelated.")
    elif chi.get("p_value") is not None:
        points.append("The chi-square test is not reliable here because some table cells have too few rows, so lean on the counts and Cramér's V rather than on the p-value.")
    return Draft(summary, points, terms_for("Cramér's V", "chi-square test", "p-value"), [CAUSATION])


@explainer("op:discover")
def _discover(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    pats = d["patterns"]
    summary = f"{say.count(d['pairs_examined'])} column pairs were screened; {say.count(d['patterns_total'])} showed a pattern above the minimum strength."
    if not pats:
        return Draft(summary, ["No pattern was strong enough to report. That does not prove nothing is there, only that nothing stood out in these rows."])
    points = []
    for p in pats[:3]:
        stab = p.get("stability")
        folds = p.get("stability_folds")
        held = f" It held up in {say.derived(round(stab * folds), from_values=[stab, folds], digits=0)} of {say.count(folds)} random subsets of the rows." if stab is not None and folds else ""
        points.append(f"{_q(say, p['x'])} and {_q(say, p['y'])}: {say.quote(p['practical_relevance'])} {say.lit(p['measure'])} = {say.num(p['strength'])}, n = {say.count(p['n'])}.{held}")
    return Draft(summary, points, terms_for("correlation", "eta (correlation ratio)", "Cramér's V"),
                 [CAUSATION, "Screening many pairs at once makes some chance patterns likely, so treat these as leads to check, not conclusions."])


# ------------------------------------------------------------------------------------------------ hypothesis tests
def _decision_text(say: Say, res: Mapping[str, Any], alpha: float) -> str:
    dec = res["decision"]
    pt = say.p(res["p_value"])
    if dec["reject_null"]:
        return (f"The test gives {pt}, which is below the chosen threshold of {say.num(alpha)}. The data would be hard to explain if there were really no effect, "
                "so the result counts as statistically significant.")
    return f"The test gives {pt}, which is not below the chosen threshold of {say.num(alpha)}. The data do not rule out 'no effect', so there is not enough evidence of one."


@explainer("op:hypothesis")
def _hypothesis(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    h = d["hypothesis"]
    sel = d.get("selected_test") or {}
    if not d.get("executed"):
        return Draft("This is a test plan, not a result: no test has been run yet.",
                     [f"The question being asked: {say.quote(h['statement'])}", f"The planned method is the {say.lit(sel.get('name', 'selected test'))} because {say.quote(sel.get('why', ''), cap=False)}".rstrip() + ".",
                      "Check the assumptions listed with the plan, then run the test."], cautions=["Nothing about the data has been concluded yet."])
    res = d["result"]
    alpha = h["alpha"]
    points = [f"Method: the {say.lit(res['test'])} was chosen because {say.quote(sel.get('why', 'it fits the data'), cap=False)}".rstrip(".") + "."]
    points.append(_decision_text(say, res, alpha))
    es = res.get("effect_size") or {}
    if es.get("value") is not None:
        ci = es.get("ci")
        ci_text = f" ({say.conf()} CI {say.num(ci[0])} to {say.num(ci[1])})" if ci and ci[0] is not None and ci[1] is not None else ""
        points.append(f"How big is the effect? The {say.lit(es['name'])} is {say.num(es['value'])}{ci_text}. " + (f"{say.quote(res['decision']['practical_relevance'], cap=True)}" if res["decision"].get("practical_relevance") else ""))
    est = res.get("estimate")
    if est and est.get("value") is not None and est is not es:
        ci = est.get("ci")
        points.append(f"The estimate ({say.lit(est['name'])}) is {say.num(est['value'])}" + (f", with a {say.conf()} confidence interval from {say.num(ci[0])} to {say.num(ci[1])}." if ci and ci[0] is not None else "."))
    failed_checks = [c for c in d.get("assumption_checks", []) if c.get("status") == "not_met"]
    cautions = [CAUSATION]
    if failed_checks:
        cautions.append(f"{say.derived(len(failed_checks), from_values=[res['p_value']], digits=0)} assumption {_plural(len(failed_checks), 'check')} {_plural(len(failed_checks), 'was', 'were')} not met (see the checks above); the method chosen is designed to cope with that, but treat the result with care.")
    mt = d.get("multiple_testing") or {}
    if mt.get("family_size", 1) > 1:
        cautions.append(f"This was one of {say.count(mt['family_size'])} tests, so a small p-value is less convincing than it looks; compare the adjusted figure.")
    summary = (f"{say.quote(h['statement'])} " + ("The data support this." if res["decision"]["reject_null"] else "The data do not clearly support this.")).strip()
    return Draft(summary, points, terms_for("p-value", "effect size", "confidence interval"), cautions)


# ------------------------------------------------------------------------------------------------ time
@explainer("op:trend")
def _trend(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    tr = d["trend"]
    summary = f"The {say.lit(d['agg'])} of {_q(say, d['measure'])} is tracked over {say.count(d['periods'])} {say.lit(d['freq'])}-long periods."
    points = []
    if d.get("highest") and d.get("lowest"):
        points.append(f"It was highest in the period starting {say.lit(d['highest']['period_start'])} ({say.num(d['highest']['value'])}) and lowest in the period starting {say.lit(d['lowest']['period_start'])} ({say.num(d['lowest']['value'])}).")
    if d.get("change_first_to_last_pct") is not None:
        points.append(f"From the first to the last period it changed by {say.pct(d['change_first_to_last_pct'])}.")
    if tr.get("slope_p_value") is not None and tr.get("conclusion") != "Not enough evidence.":
        points.append(f"A straight-line trend test finds a {say.lit(tr['direction'])} trend of {say.num(tr['slope_per_period'])} per period ({say.p(tr['slope_p_value'])}).")
    else:
        points.append("A straight-line trend test does not show a clear upward or downward trend: not enough evidence that the movement is more than ordinary variation.")
    return Draft(summary, points, terms_for("p-value"), ["A trend in past data does not predict the future, and a straight line cannot capture seasonal swings."])


@explainer("op:temporal_correlation")
def _temporal_correlation(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    best = d.get("strongest")
    if not best:
        return None
    summary = f"{_q(say, d['first'])} and {_q(say, d['second'])} were compared at several time offsets; the strongest link is at offset {say.count(abs(best['lag_periods']))} {say.lit(d['freq'])}(s)."
    points = [f"At that offset the correlation is r = {say.num(best['r'])} (n = {say.count(best['n'])}): {say.quote(best['reading'])}."]
    return Draft(summary, points, terms_for("correlation"), ["This is exploratory. Two series that both trend upward will correlate whatever their relationship, and picking the best of several offsets flatters the result.", CAUSATION])


# ------------------------------------------------------------------------------------------------ charts
@explainer("op:chart")
def _chart(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    kind = d.get("type")
    title = say.quote(d.get("title", "")) if d.get("title") else "This chart"
    if kind == "histogram":
        bars = d["bars"]
        top = max(bars, key=lambda b: b["count"])
        ov = d.get("overlays") or {}
        points = [f"The most populated range runs from {say.num(top['lo'])} to {say.num(top['hi'])} ({say.count(top['count'])} rows, {say.pct(top['pct'])})."]
        if ov.get("mean") is not None and ov.get("median") is not None:
            if abs(ov["mean"] - ov["median"]) > 0.05 * max(abs(ov["median"]), 1e-9):
                points.append(f"The average ({say.num(ov['mean'])}) differs from the median ({say.num(ov['median'])}), which means the distribution is lopsided: " +
                              ("a few large values stretch the right-hand tail." if ov["mean"] > ov["median"] else "a few small values stretch the left-hand tail."))
            else:
                points.append(f"The average ({say.num(ov['mean'])}) and the median ({say.num(ov['median'])}) are close, so the distribution is roughly balanced around its centre.")
        return Draft(f"{title}: the chart shows how the values are spread across {say.items(len(bars))} ranges.", points, terms_for("mean", "median"))
    if kind == "bar":
        bars = d["bars"]
        if not bars:
            return None
        hi, lo = max(bars, key=lambda b: b["value"]), min(bars, key=lambda b: b["value"])
        points = [f"{_q(say, hi['label'])} is highest ({say.num(hi['value'])}, n = {say.count(hi['n'])}) and {_q(say, lo['label'])} is lowest ({say.num(lo['value'])}, n = {say.count(lo['n'])})."]
        if d.get("categories_not_shown"):
            points.append(f"{say.count(d['categories_not_shown'])} further categories are not shown.")
        return Draft(f"{title}: each bar is the value for one category.", points, cautions=["Bars with few rows (small n) can swing a lot from one sample to the next."])
    if kind == "scatter":
        tl = (d.get("overlays") or {}).get("trend_line") or {}
        points = []
        if tl.get("slope") is not None:
            points.append(f"The fitted straight line rises by {say.num(tl['slope'])} in the vertical variable for each one-unit increase in the horizontal one, on average." if tl["slope"] > 0 else
                          f"The fitted straight line falls by {say.num(abs(tl['slope']))} in the vertical variable for each one-unit increase in the horizontal one, on average.")
            points.append(f"The line explains {say.share(tl['r_squared'])} of the variation (R² = {say.num(tl['r_squared'])}); rank correlation (Spearman rho) is {say.num(tl['spearman_rho'])} across {say.count(tl['n'])} points.")
        return Draft(f"{title}: each dot is one row.", points, terms_for("R²", "Spearman rho"), [CAUSATION])
    if kind == "heatmap":
        cells = [c for c in d.get("cells", []) if c["x"] != c["y"] and c.get("value") is not None]
        if not cells:
            return Draft(f"{title}: colours show how strongly each pair is related.", [])
        best = max(cells, key=lambda c: abs(c["value"]))
        return Draft(f"{title}: colours show how strongly each pair is related.",
                     [f"The strongest relationship shown is between {_q(say, best['x'])} and {_q(say, best['y'])} ({say.num(best['value'])})."], terms_for("correlation"), [CAUSATION])
    if kind == "timeseries":
        tr = d.get("trend") or {}
        points = []
        if tr.get("direction"):
            points.append(f"The overall trend reads as: {say.lit(tr['direction'])}" + (f" ({say.p(tr['slope_p_value'])})." if tr.get("slope_p_value") is not None and tr.get("conclusion") != "Not enough evidence." else "; there is not enough evidence of a trend beyond ordinary variation."))
        rng = d.get("date_range") or {}
        if rng:
            points.append(f"The series runs from {say.lit(rng['from'])} to {say.lit(rng['to'])}.")
        return Draft(f"{title}: values are plotted period by period.", points, terms_for("p-value"), ["A line through past data does not predict the future."])
    return None


# ------------------------------------------------------------------------------------------------ mixed-variable association (Phase 5)
@explainer("op:mixed_association")
def _mixed(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    d = r["data"]
    m = d["measure"]
    x, y = _q(say, d["x"]), _q(say, d["y"])
    pair = d["pair_type"]
    pair_text = {"numeric_numeric": "two numeric columns", "categorical_numeric": "a category and a numeric column", "categorical_categorical": "two category columns"}[pair]
    ci = m.get("ci")
    ci_text = f" ({say.conf()} CI {say.num(ci[0])} to {say.num(ci[1])})" if ci and ci[0] is not None and ci[1] is not None else ""
    summary = f"{x} and {y} ({pair_text}) show {_strength_word(m['value'])} association: {say.lit(m['name'])} = {say.num(m['value'])}{ci_text}, based on {say.count(d['n'])} complete rows."
    points = [f"The measure was chosen automatically for {pair_text}. {say.quote(d['why'])}"]
    t = d.get("test") or {}
    if t.get("p_value") is not None:
        points.append(f"The {say.lit(t['name'])} gives {say.p(t['p_value'])}. " + ("A small p-value says the association is unlikely to be chance alone; it does not say the association is large or important." if t.get("significant") else "The data do not clearly rule out chance."))
    if d.get("missing_excluded"):
        points.append(f"{say.count(d['missing_excluded'])} rows were left out because one of the two columns was empty.")
    unmet = [c for c in d.get("assumption_checks", []) if c.get("status") == "not_met"]
    cautions = [CAUSATION]
    if unmet:
        cautions.append("Some assumptions were not met (listed in the technical result); the method selected is the one that copes with that, but results deserve extra care.")
    if d.get("small_sample"):
        cautions.append(f"With only {say.count(d['n'])} complete rows the uncertainty is large; the interval shows how much.")
    return Draft(summary, points, terms_for("correlation", "eta (correlation ratio)", "Cramér's V", "p-value", "confidence interval"), cautions)


# ------------------------------------------------------------------------------------------------ insights and assistant
@explainer("insight")
def _insight(say: Say, ins: Mapping[str, Any]) -> Optional[Draft]:
    cat = ins.get("category")
    se = ins.get("supporting_evidence") or {}
    v = se.get("values") or {}
    aff = ins.get("affected") or {}
    cols = aff.get("columns") or []
    caution = list(ins.get("limitations") or [])[:2]
    if cat == "descriptive" and "rows" in v:
        return Draft(f"The dataset has {say.count(v['rows'])} rows and {say.count(v['columns'])} columns, and {say.pct(v['missing_pct'], 2)} of all cells are empty.", ["This is a plain description of the table, not a finding about the world."])
    if cat == "descriptive" and "skewness" in v:
        return Draft(f"The values of {_q(say, cols[0])} are lopsided: the average ({say.num(v['mean'])}) and the median ({say.num(v['median'])}) differ noticeably.",
                     ["When a few extreme values stretch one tail, the median is the better description of a typical row."], terms_for("skewness", "mean", "median"))
    if cat == "data_quality":
        pts = [f"{say.count(aff['rows_affected'])} rows are affected." if aff.get("rows_affected") is not None else ""]
        return Draft("A data-quality issue was found that can affect later analysis.", pts + ["Decide whether to clean it with the data-cleaning tools or to keep it and interpret results with it in mind."])
    if cat == "anomaly":
        pts = []
        if v.get("lower_fence") is not None:
            pts.append(f"Values below {say.num(v['lower_fence'])} or above {say.num(v['upper_fence'])} count as unusual under the standard rule.")
        return Draft(f"{say.count(v.get('count', aff.get('rows_affected', 0)))} unusually extreme value(s) were found in {_q(say, cols[0])}.", pts + ["Extremes may be errors or genuine; they are left in every calculation unless you filter them."])
    if cat in ("relationship", "group_difference"):
        what = "differs between groups" if cat == "group_difference" else "are related"
        pts = [f"The pattern held up in a stability check on random subsets of the rows (agreement {say.num(v['stability'])})." if v.get("stability") is not None else ""]
        if v.get("p_adjusted_bh") is not None:
            pts.append(f"After allowing for the many pairs screened, the adjusted p-value is {say.num(v['p_adjusted_bh'])}.")
        return Draft(f"{' and '.join(_q(say, c) for c in cols[:2])} {what}: strength {say.num(v['strength'])} ({say.quote(v['practical_relevance'])}), based on {say.count(v['n'])} rows.", pts, terms_for("correlation", "p-value"), [CAUSATION])
    if cat == "trend":
        return Draft("A consistent rise or fall over time was detected.", ["The direction is statistically distinguishable from random wobble in this period.", "A past trend does not guarantee the future."], terms_for("p-value"))
    if cat == "statistical":
        return Draft(f"A statistical test supports this pattern ({say.p(v['p_value'])}).", ["Because many pairs were screened before this one was chosen, treat a single small p-value cautiously."] , terms_for("p-value", "effect size"), [CAUSATION])
    return None


@explainer("assistant_answer")
def _assistant(say: Say, ctx: Mapping[str, Any]) -> Optional[Draft]:
    primary = ctx.get("primary_explanation")
    ver = ctx.get("verification") or {}
    if not primary or primary.get("status") != "available":
        return None
    points: List[str] = []
    if ver.get("checked"):
        points.append(f"As a cross-check, {say.count(ver['matched'])} of {say.count(ver['checked'])} figures in the answer were recalculated by a second, independent method and matched.")
    elif ver.get("status") == "NOT_CHECKED":
        points.append("The figures could not be cross-checked independently this time.")
    steps = ctx.get("steps") or []
    if steps:
        points.append("How the question was read: " + say.quote("; ".join(str(s) for s in steps[:3])) + ".")
    for p in primary["points"][:3]:
        points.append(say.quote(p))
    return Draft(say.quote(primary["summary"]), points, [{"term": t["term"], "meaning": t["meaning"]} for t in primary.get("terms", [])], list(primary.get("cautions", [])))


# ------------------------------------------------------------------------------------------------ Phase 3 profile
@explainer("profile:dataset")
def _profile(say: Say, p: Mapping[str, Any]) -> Optional[Draft]:
    d = p["dataset"]
    points = []
    points.append("No cell is empty." if d["missing_cells"] == 0 else f"{say.count(d['missing_cells'])} cells ({say.pct(d['missing_pct'], 2)}) are empty; {say.count(d['complete_rows'])} rows are complete.")
    points.append("No row repeats an earlier row exactly." if d["duplicate_rows"] == 0 else f"{say.count(d['duplicate_rows'])} rows repeat an earlier row exactly.")
    worst = sorted((c for c in p.get("columns", []) if c.get("missing")), key=lambda c: -c["missing"])[:2]
    for c in worst:
        points.append(f"{_q(say, c['name'])} has the most empty values ({say.count(c['missing'])}).")
    return Draft(f"The profile describes {say.count(d['rows'])} rows and {say.count(d['columns'])} columns exactly as they are stored.", points,
                 cautions=["A profile reports what is in the file. It does not judge whether the data are right or representative."])


@explainer("profile:document")
def _profile_doc(say: Say, p: Mapping[str, Any]) -> Optional[Draft]:
    d = p["document"]
    pages, words = d["pages"]["value"], d["words"]["value"]
    pts = [f"That is about {say.num(d['words_per_page'], 1)} words per page."]
    if d.get("empty_pages"):
        pts.append(f"{say.items(len(d['empty_pages']))} page(s) contain no readable text.")
    return Draft(f"The document has {say.count(pages)} pages and {say.count(words)} words.", pts)


# ------------------------------------------------------------------------------------------------ attaching to stored runs
def explain_op(result: Mapping[str, Any]) -> Dict[str, Any]:
    """Explanation for one operation result of an analysis run."""
    if result.get("status") == "failed":
        err = (result.get("error") or {}).get("message")
        from .core import Say as _Say, finish, Draft as _Draft
        say = _Say(result)
        return finish("op:failed", say, _Draft("This calculation did not complete, so there is nothing to interpret.",
                                               [say.quote(err)] if err else [], cautions=["Do not draw conclusions from a calculation that did not run."]))
    if result.get("status") == "not_run":
        msg = (result.get("data") or {}).get("reason")
        from .core import Say as _Say, finish, Draft as _Draft
        say = _Say(result)
        return finish("op:not_run", say, _Draft("There was not enough evidence to run this calculation, so no estimate is shown.",
                                                [say.quote(msg)] if msg else [], cautions=["No estimate is better than a misleading one."]))
    return explain(f"op:{result.get('op')}", result)


def attach_to_run(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Adds an `explanation` beside every operation result of a stored run document. `data` and the result fingerprint are untouched."""
    for res in doc.get("results", []) or []:
        res["explanation"] = explain_op(res)
    return doc
