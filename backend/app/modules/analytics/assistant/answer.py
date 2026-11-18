"""Turns stored analysis results into an answer: a list of FACTS (each number, with where it came from and how to check it) and plain text
that is assembled only from those facts. Anything an optional language model writes is checked against the same facts."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..constants import CORRELATION_NOTE, NOT_ENOUGH_EVIDENCE, P_VALUE_NOTE
from .language import Interpretation

MAX_FACTS = 24


def fmt(x: Any, places: int = 2) -> str:
    if isinstance(x, bool) or x is None:
        return str(x)
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        if x != 0 and abs(x) < 10 ** -places:
            return f"{x:.2e}"
        return f"{x:,.{places}f}" if abs(x) < 1000 else f"{x:,.0f}"
    return str(x)


def pfmt(p: Any) -> str:
    return "p < 0.001" if isinstance(p, (int, float)) and p < 0.001 else f"p = {fmt(p, 3)}"


def fact(fid: str, label: str, value: Any, *, n: Optional[int] = None, check: Optional[Dict[str, Any]] = None, source: Optional[Dict[str, Any]] = None, unit: Optional[str] = None) -> Dict[str, Any]:
    return {"id": fid, "label": label, "value": value, "n": n, "unit": unit, "check": check, "source": source or {}}


def _res(run: Mapping[str, Any], op: str, nth: int = 0) -> Optional[Tuple[int, Dict[str, Any]]]:
    seen = 0
    for i, r in enumerate(run["result"]["results"]):
        if r["op"] == op:
            if seen == nth:
                return i, r
            seen += 1
    return None


def _failed(run: Mapping[str, Any]) -> Optional[str]:
    for r in run["result"]["results"]:
        if r["status"] == "failed":
            return (r.get("error") or {}).get("message") or "A calculation could not be completed."
    return None


def compose(interp: Interpretation, run: Mapping[str, Any]) -> Dict[str, Any]:
    """Returns {text, facts, caveats, tables}. Never produces a number that is not in `facts`."""
    focus = interp.focus
    kind = focus.get("kind")
    facts: List[Dict[str, Any]] = []
    lines: List[str] = []
    caveats: List[str] = []
    problem = _failed(run)
    src = lambda op, i: {"run_id": run["id"], "operation_index": i, "operation": op}  # noqa: E731
    if kind == "aggregate" or kind == "best":
        got = _res(run, "aggregate")
        if not got:
            return {"text": problem or NOT_ENOUGH_EVIDENCE, "facts": [], "caveats": [], "tables": []}
        i, r = got
        d = r["data"]
        agg, measure, dim = focus["agg"], focus.get("measure"), focus.get("dimension")
        name = {"mean": "average", "sum": "total", "median": "median", "max": "maximum", "min": "minimum", "count": "count", "std": "standard deviation"}.get(agg, agg)
        what = f"{name} of '{measure}'" if measure else "number of rows"
        if not dim:
            ov = d["overall"]
            facts.append(fact("value", what, ov["value"], n=ov["n"], check={"kind": "agg", "agg": agg, "column": measure} if measure else {"kind": "rows"}, source=src("aggregate", i)))
            ci = ov.get("ci")
            lines.append(f"The {what} is {fmt(ov['value'])}" + (f" (95% CI {fmt(ci[0])} to {fmt(ci[1])})" if ci else "") + f", based on {fmt(ov['n'])} rows" + (" with a value." if measure else "."))
        else:
            groups = d["groups"][:12]
            for k, g in enumerate(groups):
                key = g["key"][dim]
                facts.append(fact(f"g{k}", f"{what} for {dim} = {key}", g["value"], n=g["n"], check={"kind": "group_agg", "agg": agg, "column": measure, "group": dim, "key": str(key)}, source=src("aggregate", i)))
            if kind == "best" and groups:
                top = groups[0]
                word = "lowest" if focus.get("ascending") else "highest"
                lines.append(f"{dim} = {top['key'][dim]} has the {word} {what}: {fmt(top['value'])} (n = {fmt(top['n'])}).")
                if len(groups) > 1:
                    lines.append(f"Next is {groups[1]['key'][dim]} at {fmt(groups[1]['value'])}.")
                lines.append("This ranks groups in the data; it does not test whether the difference is larger than chance. Ask to compare the groups for a test.")
                small = [g for g in groups[:3] if g["n"] < 10]
                if small:
                    caveats.append("At least one of the top groups has fewer than 10 rows, so its value is unstable.")
            else:
                lines.append(f"The {what} by {dim}: " + "; ".join(f"{g['key'][dim]} {fmt(g['value'])} (n = {fmt(g['n'])})" for g in groups[:8]) + ".")
            if d.get("groups_total", 0) > len(groups):
                caveats.append(f"{d['groups_total'] - len(groups)} more groups are in the full result.")
    elif kind == "missing":
        got = _res(run, "missing_values")
        if not got:
            return {"text": problem or NOT_ENOUGH_EVIDENCE, "facts": [], "caveats": [], "tables": []}
        i, r = got
        cols = [c for c in r["data"]["columns"] if c["missing"] > 0]
        if not cols:
            lines.append("No column has missing values.")
            facts.append(fact("none", "columns with missing values", 0, check=None, source=src("missing_values", i)))
        else:
            cols.sort(key=lambda c: (-c["missing"], c["column"]))
            for k, c in enumerate(cols[:10]):
                facts.append(fact(f"m{k}", f"missing values in '{c['column']}'", c["missing"], n=None, check={"kind": "missing", "column": c["column"]}, source=src("missing_values", i)))
            lines.append("Columns with the most missing values: " + "; ".join(f"'{c['column']}' {fmt(c['missing'])} ({fmt(c['pct'], 1)}%)" for c in cols[:6]) + ".")
    elif kind == "relationship":
        a, b = focus["pair"]
        corr = _res(run, "correlation")
        hyp = _res(run, "hypothesis")
        gc = _res(run, "group_compare")
        if corr:
            i, r = corr
            pr = r["data"]["pairs"][0] if r["data"].get("pairs") else None
            if pr:
                for method, key, sym in (("spearman", "rho", "Spearman rho"), ("pearson", "r", "Pearson r")):
                    if pr.get(method) and pr[method].get(key) is not None:
                        facts.append(fact(method, f"{sym} between '{a}' and '{b}'", pr[method][key], n=pr["n"], check={"kind": method, "x": a, "y": b}, source=src("correlation", i)))
                prim = pr.get("primary_measure", "spearman")
                v = pr[prim]["rho" if prim == "spearman" else "r"]
                ci = pr[prim].get("ci")
                lines.append(f"'{a}' and '{b}' show a {pr['direction']} association of {pr['magnitude']} size ({'Spearman rho' if prim == 'spearman' else 'Pearson r'} = {fmt(v)}"
                             + (f", 95% CI {fmt(ci[0])} to {fmt(ci[1])}" if ci else "") + f", n = {fmt(pr['n'])}).")
                if pr.get("pearson_caution"):
                    caveats.append(pr["pearson_caution"])
        elif gc:
            i, r = gc
            groups = r["data"]["groups"]
            for k, g in enumerate(groups[:12]):
                facts.append(fact(f"g{k}", f"mean of '{r['data']['measure']}' for {r['data']['group']} = {g['group']}", g["mean"], n=g["n"],
                                  check={"kind": "group_agg", "agg": "mean", "column": r["data"]["measure"], "group": r["data"]["group"], "key": str(g["group"])}, source=src("group_compare", i)))
            lines.append(f"Mean of '{r['data']['measure']}' by '{r['data']['group']}': " + "; ".join(f"{g['group']} {fmt(g['mean'])} (n = {fmt(g['n'])})" for g in groups[:8]) + ".")
        else:
            ct = _res(run, "contingency")
            if ct:
                i, r = ct
                chi = r["data"].get("chi_square") or {}
                facts.append(fact("n", f"rows with both '{a}' and '{b}'", r["data"]["n"], n=r["data"]["n"], check={"kind": "pairs", "x": a, "y": b}, source=src("contingency", i)))
                if chi.get("statistic") is not None:
                    lines.append(f"Cross-tabulating '{a}' and '{b}' ({fmt(r['data']['n'])} rows): chi-square = {fmt(chi['statistic'])}, df = {chi['df']}, {pfmt(chi.get('p_value'))}.")
        if hyp:
            i, r = hyp
            d = r["data"]
            if r["status"] != "ok" or not d.get("executed") or not d.get("result"):
                caveats.append("The significance test was not run because its assumptions could not be established: " + "; ".join(
                    c["detail"] for c in d.get("assumption_checks", []) if c.get("status") in ("violated", "not_met", "failed"))[:300] if d else NOT_ENOUGH_EVIDENCE)
                lines.append(NOT_ENOUGH_EVIDENCE + " A formal test could not be run reliably on this data.")
            else:
                res = d["result"]
                es = res.get("effect_size") or {}
                facts.append(fact("p", f"p-value of the {res['test']}", res["p_value"], n=(r.get("evidence") or {}).get("sample_size"), source=src("hypothesis", i)))
                if es.get("value") is not None:
                    facts.append(fact("effect", f"effect size ({es.get('name', 'effect')})", es["value"], source=src("hypothesis", i)))
                lines.append(f"{res['test']}: {pfmt(res['p_value'])}" + (f", effect size {fmt(es['value'])}" if es.get("value") is not None else "")
                             + (". The data are unlikely if there were no relationship." if res["decision"]["reject_null"] else ". The data do not rule out there being no relationship."))
                caveats.append(P_VALUE_NOTE)
        caveats.append(CORRELATION_NOTE)
    elif kind == "compare":
        gc = _res(run, "group_compare")
        if not gc:
            return {"text": problem or NOT_ENOUGH_EVIDENCE, "facts": [], "caveats": [], "tables": []}
        i, r = gc
        d = r["data"]
        for k, g in enumerate(d["groups"][:12]):
            facts.append(fact(f"g{k}", f"mean of '{d['measure']}' for {d['group']} = {g['group']}", g["mean"], n=g["n"],
                              check={"kind": "group_agg", "agg": "mean", "column": d["measure"], "group": d["group"], "key": str(g["group"])}, source=src("group_compare", i)))
        lines.append(f"Mean of '{d['measure']}' by '{d['group']}': " + "; ".join(f"{g['group']} {fmt(g['mean'])} (n = {fmt(g['n'])})" for g in d["groups"][:8]) + ".")
        lines.append("These are differences in the data. To test whether they exceed chance, ask: 'is there a relationship between %s and %s?'" % (d["measure"], d["group"]))
    elif kind == "trend":
        got = _res(run, "trend")
        if not got:
            return {"text": problem or NOT_ENOUGH_EVIDENCE, "facts": [], "caveats": [], "tables": []}
        i, r = got
        d = r["data"]
        tr = d["trend"]
        what = f"{d['agg']} of '{d['measure']}'" if d.get("measure") else "number of rows"
        if d.get("highest"):
            facts.append(fact("high", f"highest {what} (period starting {d['highest']['period_start']})", d["highest"]["value"], source=src("trend", i)))
            facts.append(fact("low", f"lowest {what} (period starting {d['lowest']['period_start']})", d["lowest"]["value"], source=src("trend", i)))
        lines.append(f"Per {d['freq']}, the {what} ranged from {fmt(d['lowest']['value'])} to {fmt(d['highest']['value'])} over {d['periods']} periods.")
        if tr.get("conclusion") == NOT_ENOUGH_EVIDENCE or tr.get("slope_p_value") is None:
            lines.append("Not enough evidence of a trend: the slope is not distinguishable from chance.")
        else:
            facts.append(fact("slope", f"trend slope per {d['freq']}", tr["slope_per_period"], source=src("trend", i)))
            lines.append(f"The fitted trend is {tr['direction']}: {fmt(tr['slope_per_period'])} per {d['freq']} (95% CI {fmt(tr['slope_ci'][0])} to {fmt(tr['slope_ci'][1])}, {pfmt(tr['slope_p_value'])}).")
        caveats.append("A trend in past data does not predict the future.")
    elif kind == "outliers":
        dq = _res(run, "data_quality")
        issues = [x for x in (dq[1]["data"]["issues"] if dq else []) if x["code"] == "OUTLIERS"]
        if not issues:
            lines.append("No unusual values were flagged by the standard rule (values beyond 1.5 times the interquartile range).")
        else:
            for k, x in enumerate(issues[:10]):
                facts.append(fact(f"o{k}", f"unusual values in '{x['columns'][0]}'", x["count"], source=src("data_quality", dq[0])))
            lines.append("Unusual values were flagged in: " + "; ".join(f"'{x['columns'][0]}' ({fmt(x['count'])} value{'s' if x['count'] != 1 else ''}, rows {', '.join(map(str, x['row_numbers'][:5]))})" for x in issues[:6]) + ".")
            caveats.append("Flagged values may be errors or genuine extremes. They are not removed from any calculation.")
    elif kind == "target":
        got = _res(run, "discover")
        if not got:
            return {"text": problem or NOT_ENOUGH_EVIDENCE, "facts": [], "caveats": [], "tables": []}
        i, r = got
        pats = r["data"]["patterns"]
        t = focus["target"]
        if not pats:
            lines.append(NOT_ENOUGH_EVIDENCE + f" No other column has a stable, non-trivial association with '{t}' after adjusting for the number of columns examined.")
        else:
            for k, p in enumerate(pats[:8]):
                other = p["y"] if p["x"] == t else p["x"]
                facts.append(fact(f"a{k}", f"association strength of '{other}' with '{t}' ({p['measure']})", p["strength"], n=p["n"], source=src("discover", i)))
            lines.append(f"Columns most strongly associated with '{t}': " + "; ".join(f"'{p['y'] if p['x'] == t else p['x']}' ({p['measure']} {fmt(p['strength'])}, {p['practical_relevance']})" for p in pats[:6]) + ".")
        caveats += [CORRELATION_NOTE, "Strength measures differ by column type, so values are comparable only roughly across rows."]
    elif kind == "distribution":
        cs = _res(run, "column_stats")
        fr = _res(run, "frequency")
        col = focus["column"]
        if cs:
            i, r = cs
            c = r["data"]["columns"][0]
            for key, lab in (("mean", "mean"), ("median", "median"), ("min", "minimum"), ("max", "maximum")):
                if c.get(key) is not None:
                    facts.append(fact(key, f"{lab} of '{col}'", c[key], n=c["non_missing"], check={"kind": "agg", "agg": key, "column": col}, source=src("column_stats", i)))
            lines.append(f"'{col}': mean {fmt(c['mean'])}, median {fmt(c['median'])}, range {fmt(c['min'])} to {fmt(c['max'])}, n = {fmt(c['non_missing'])}; skewness {fmt(c.get('skewness'))}.")
        elif fr:
            lines.append(f"Frequency table for '{col}' is in the result details.")
    elif kind == "summary":
        got = _res(run, "dataset_summary")
        if not got:
            return {"text": problem or NOT_ENOUGH_EVIDENCE, "facts": [], "caveats": [], "tables": []}
        i, r = got
        d = r["data"]
        facts.append(fact("rows", "rows in scope", d["rows_in_scope"], check={"kind": "rows"}, source=src("dataset_summary", i)))
        facts.append(fact("cols", "columns", d["columns"], source=src("dataset_summary", i)))
        lines.append(f"The data in scope has {fmt(d['rows_in_scope'])} rows and {fmt(d['columns'])} columns; {fmt(d['missing_cells']['pct'])}% of cells are empty.")
    if problem and not lines:
        lines.append(problem)
    for w in run["result"]["evidence"]["access"].get("warnings", []):
        if w not in caveats and len(caveats) < 8:
            caveats.append(w)
    return {"text": " ".join(lines) if lines else NOT_ENOUGH_EVIDENCE, "facts": facts[:MAX_FACTS], "caveats": caveats[:8], "tables": []}


NUM = re.compile(r"(?<![\w.])-?\d[\d,]*\.?\d*(?:e[+-]?\d+)?")


def allowed_numbers(facts: Sequence[Mapping[str, Any]], extra: Sequence[Any] = ()) -> List[float]:
    out: List[float] = []
    for f in facts:
        for v in (f["value"], f.get("n")):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(float(v))
    out += [float(x) for x in extra if isinstance(x, (int, float))]
    return out


def numbers_supported(text: str, allowed: Sequence[float]) -> List[str]:
    """Numbers in `text` that cannot be traced to a fact (allowing rounding to what a reader would write). Years and list markers are ignored."""
    bad = []
    for m in NUM.finditer(text):
        raw = m.group(0).replace(",", "")
        try:
            x = float(raw)
        except ValueError:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", raw) or x in (0.0, 1.0, 95.0, 5.0):
            continue
        ok = False
        for a in allowed:
            places = len(raw.split(".")[1]) if "." in raw else 0
            if abs(round(a, places) - x) <= 10 ** -places * 0.51 or abs(a - x) <= abs(a) * 0.005 + 1e-9:
                ok = True
                break
        if not ok:
            bad.append(m.group(0))
    return bad
