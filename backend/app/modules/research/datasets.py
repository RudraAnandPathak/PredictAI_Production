"""Dataset research: answering a question about the person's own datasets by CALCULATION, never by language-model guesswork.

What happens for each selected dataset (all through the owner's own principal, via the same services the Phase 4 and Phase 5 screens use):
  1. the dataset's schema and profile are read (`AnalyticsService.schema`, `ProfileService.get`);
  2. the question decides which verified operations run (see `intents.py`): dataset summary, data-quality checks, missing values, relationship discovery (with the outcome the question
     names as the target), correlation, column statistics, group comparisons, frequency tables, the dataset's trained models and their importance and evaluation (Phase 5), and earlier
     saved results;
  3. every result is turned into sentences by the fixed templates below. A sentence is a function of the stored result and nothing else: the citation records which run, which
     operation and which template produced it, so `regenerate()` can rebuild the sentence from the stored result at any time and the citation check compares it with what was quoted;
  4. only aggregates ever reach the answer: no rows, no individual values (group labels and column names are the person's own words for their own data).
A language model never sees these results unless the person asks for an AI wording, and even then every sentence of that wording is checked against them. External sources never change a
calculated result; the report places them beside it.

Pure functions (`claims_*`, `regenerate`) take plain dictionaries so they can be tested without a database."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ...core.errors import AppError
from ..identity.authorization import Principal

ASSOCIATION_CAVEAT = "This is an association in your data. It does not show that one thing causes the other."
MODEL_CAVEAT = "A model's score and importance describe how well and how it predicts on your data. They are estimates, not observed facts, and importance is not causation."
MAX_DATASETS = 3
MAX_NUMERIC = 8
ID_NAME = re.compile(r"(^|[_\-\s])(id|uuid|guid|index|idx|rowid)$|^unnamed: ?0$", re.I)
ALPHA = 0.05


def is_identifier(name: str) -> bool:
    """A column that only numbers or names the rows (row_id, customer_id, index) carries no relationship worth reporting."""
    return bool(ID_NAME.search(str(name).strip()))
SAMPLE_ROWS = 100_000


@dataclass
class Claim:
    section: str
    key: str
    text: str
    caveats: List[str] = field(default_factory=list)
    facts: Dict[str, Any] = field(default_factory=dict)


def num(x: Any, nd: int = 3) -> str:
    if x is None:
        return "n/a"
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    if f == int(f) and abs(f) < 1e15:
        return f"{int(f):,}"
    if abs(f) >= 1000:
        return f"{f:,.0f}"                                                              # never "1.2e+04": a person reads 11,985
    if abs(f) >= 100:
        return f"{f:.1f}"
    if 0 < abs(f) < 0.001:
        return f"{f:.2e}"
    return f"{f:.{nd}g}"


def pval(p: Any) -> str:
    if p is None:
        return "p not available"
    return "p < 0.001" if float(p) < 0.001 else f"p = {float(p):.3f}"


def q(name: Any) -> str:
    return f"'{name}'"


def _col(name: str) -> str:
    return re.sub(r"[_\-]+", " ", name).lower()


# ====================================================================== Phase 4 results -> sentences
def claims_dataset_summary(d: Mapping[str, Any], ctx: Mapping[str, Any]) -> List[Claim]:
    types = ", ".join(f"{n} {t}" for t, n in sorted((d.get("column_types") or {}).items(), key=lambda kv: -kv[1]))
    miss, dup = d.get("missing_cells") or {}, d.get("duplicate_rows") or {}
    text = (f"{ctx['name']} has {num(d.get('rows_in_dataset'))} rows and {num(d.get('columns'))} columns ({types}). {num(miss.get('count'))} cells ({num(miss.get('pct'))}%) are missing, "
            f"{num(d.get('complete_rows'))} rows are complete, and {num(dup.get('count'))} rows are exact duplicates of an earlier row.")
    return [Claim("overview", "summary", text, [], {"rows": d.get("rows_in_dataset"), "columns": d.get("columns"), "missing_cells": miss.get("count"), "duplicates": dup.get("count")})]


def claims_data_quality(d: Mapping[str, Any], ctx: Mapping[str, Any]) -> List[Claim]:
    issues = [i for i in d.get("issues") or [] if isinstance(i, dict)]
    rank = {"high": 0, "warning": 1, "medium": 1, "info": 2, "low": 2}
    issues.sort(key=lambda i: (rank.get(i.get("severity"), 3), -(i.get("count") or 0)))
    out = [Claim("quality", f"issue:{i}", f"Data check ({it.get('severity', 'info')}): {it.get('message')}", [], {"code": it.get("code"), "columns": it.get("columns"), "count": it.get("count")})
           for i, it in enumerate(issues[:8]) if it.get("message")]
    if not out:
        out.append(Claim("quality", "none", f"The automatic data checks on {ctx['name']} ({num(d.get('checked_columns'))} columns checked) found no problems such as constant columns, outliers or duplicates.", [], {"issues": 0}))
    return out


def claims_missing(d: Mapping[str, Any], ctx: Mapping[str, Any]) -> List[Claim]:
    cols = [c for c in d.get("columns") or [] if isinstance(c, dict) and (c.get("missing") or 0) > 0]
    if not cols:
        return [Claim("quality", "missing:none", f"No column of {ctx['name']} has missing values.", [], {"columns_with_missing": 0})]
    cols.sort(key=lambda c: -(c.get("pct") or 0))
    top = ", ".join(f"{q(c['column'])} ({num(c['pct'])}% missing)" for c in cols[:4])
    return [Claim("quality", "missing:some", f"{num(d.get('columns_with_any_missing', len(cols)))} of {num(d.get('columns_total'))} columns have missing values; the most affected are {top}.", [],
                  {"columns_with_missing": len(cols), "worst": cols[0]["column"], "worst_pct": cols[0].get("pct")})]


def _pattern_sentence(p: Mapping[str, Any], target: Optional[str]) -> str:
    x, y = p.get("x"), p.get("y")
    strength = f"{p.get('measure')} = {num(p.get('strength'))}" if p.get("strength") is not None else f"{p.get('measure')}"
    direction = f", {p['direction']} direction" if p.get("direction") in ("positive", "negative") else ""
    adj = p.get("p_adjusted_bh")
    return (f"{q(x)} and {q(y)} are associated ({strength}{direction}; {p.get('practical_relevance', 'unrated')} strength; {pval(adj if adj is not None else p.get('p_value'))} after adjusting for "
            f"the number of pairs compared; n = {num(p.get('n'))}).")


def claims_discover(d: Mapping[str, Any], ctx: Mapping[str, Any]) -> List[Claim]:
    target = ctx.get("target")
    pats = [p for p in d.get("patterns") or [] if isinstance(p, dict) and p.get("x") and p.get("y") and not is_identifier(p["x"]) and not is_identifier(p["y"])]
    out: List[Claim] = []
    for i, p in enumerate(pats[: (8 if target else 6)]):
        out.append(Claim("relationships", f"pattern:{i}", _pattern_sentence(p, target), [ASSOCIATION_CAVEAT],
                         {"x": p["x"], "y": p["y"], "measure": p.get("measure"), "strength": p.get("strength"), "direction": p.get("direction"), "n": p.get("n"), "p_adjusted": p.get("p_adjusted_bh"),
                          "relevance": p.get("practical_relevance"), "target": target}))
    if not out:
        out.append(Claim("relationships", "pattern:none", f"No relationship between the columns of {ctx['name']} stood out after adjusting for the number of pairs compared.", [], {"patterns": 0}))
    return out


def claims_correlation(d: Mapping[str, Any], ctx: Mapping[str, Any]) -> List[Claim]:
    pairs = [p for p in d.get("pairs") or [] if isinstance(p, dict)]
    pairs.sort(key=lambda p: -abs((p.get("spearman") or {}).get("rho") or (p.get("pearson") or {}).get("r") or 0))
    out: List[Claim] = []
    for i, p in enumerate(pairs[:5]):
        sp, pe = p.get("spearman") or {}, p.get("pearson") or {}
        primary = sp if p.get("primary_measure") == "spearman" and sp else (pe or sp)
        label = "Spearman rho" if primary is sp else "Pearson r"
        val = primary.get("rho") if primary is sp else primary.get("r")
        adj = primary.get("p_adjusted_bh", primary.get("p_value"))
        if val is None or p.get("magnitude") == "negligible" or adj is None or float(adj) >= ALPHA or is_identifier(p.get("x", "")) or is_identifier(p.get("y", "")):
            continue
        out.append(Claim("relationships", f"corr:{i}", f"{q(p['x'])} and {q(p['y'])} have a {p.get('direction', 'unclear')} {p.get('magnitude', '')} relationship ({label} = {num(val)}, n = {num(p.get('n'))}; "
                         f"{pval(primary.get('p_adjusted_bh', primary.get('p_value')))} after adjustment).".replace("  ", " "), [ASSOCIATION_CAVEAT],
                         {"x": p["x"], "y": p["y"], "measure": label, "strength": val, "direction": p.get("direction"), "n": p.get("n")}))
    return out


def claims_column_stats(d: Mapping[str, Any], ctx: Mapping[str, Any]) -> List[Claim]:
    out: List[Claim] = []
    for i, c in enumerate([c for c in d.get("columns") or [] if isinstance(c, dict) and c.get("mean") is not None][:MAX_NUMERIC]):
        skew = c.get("skewness")
        shape = f" The distribution is {'right' if skew > 0 else 'left'}-skewed (skewness {num(skew, 2)})." if isinstance(skew, (int, float)) and abs(skew) >= 1 else ""
        outl = c.get("outliers") or {}
        outliers = f" {num(outl.get('count'))} value(s) lie outside the usual range." if outl.get("count") else ""
        out.append(Claim("distribution", f"stats:{c['column']}", f"{q(c['column'])}: mean {num(c['mean'])}, median {num(c.get('median'))}, standard deviation {num(c.get('std'))}, "
                         f"range {num(c.get('min'))} to {num(c.get('max'))}, {num(c.get('missing'))} missing.{shape}{outliers}", [],
                         {"column": c["column"], "mean": c["mean"], "median": c.get("median"), "std": c.get("std"), "min": c.get("min"), "max": c.get("max"), "skewness": skew, "outliers": outl.get("count")}))
    return out


def claims_frequency(d: Mapping[str, Any], ctx: Mapping[str, Any]) -> List[Claim]:
    rows = [r for r in d.get("rows") or [] if isinstance(r, dict)]
    if not rows or d.get("kind") != "categorical":
        return []
    parts = ", ".join(f"{r['value']} {num(r['pct'])}%" for r in rows[:6])
    return [Claim("distribution", f"freq:{d.get('column')}", f"The values of {q(d.get('column'))} split as: {parts} ({num(d.get('distinct'))} distinct values, {num(d.get('missing'))} missing).", [],
                  {"column": d.get("column"), "top": rows[0]["value"], "top_pct": rows[0]["pct"], "distinct": d.get("distinct")})]


def claims_group_compare(d: Mapping[str, Any], ctx: Mapping[str, Any]) -> List[Claim]:
    groups = [g for g in d.get("groups") or [] if isinstance(g, dict)]
    if len(groups) < 2:
        return []
    ref = d.get("reference_group")
    means = ", ".join(f"{g['group']} = {num(g.get('mean'))} (n = {num(g.get('n'))})" for g in groups[:6])
    out = [Claim("groups", "means", f"Average {q(d['measure'])} by {q(d['group'])}: {means}.", [], {"measure": d["measure"], "group": d["group"], "groups": len(groups)})]
    for i, g in enumerate([g for g in groups if g.get("vs_reference")][:3]):
        v = g["vs_reference"]
        ci = v.get("difference_ci") or [None, None]
        p_adj = v.get("welch_p_holm", v.get("welch_p"))
        clear = "" if p_adj is None or float(p_adj) < ALPHA else " The difference is within what chance alone could produce, so it is not statistically clear."
        out.append(Claim("groups", f"diff:{i}", f"{q(d['measure'])} is {v.get('direction', 'different')} in {q(d['group'])} = {g['group']} than in {ref} by {num(abs(v.get('mean_difference') or 0))} "
                         f"(95% interval {num(ci[0])} to {num(ci[1])}; effect size Hedges g = {num(v.get('hedges_g'), 2)}; {pval(p_adj)} after adjustment).{clear}", [ASSOCIATION_CAVEAT],
                         {"measure": d["measure"], "group": d["group"], "level": g["group"], "reference": ref, "difference": v.get("mean_difference"), "hedges_g": v.get("hedges_g"), "direction": v.get("direction"),
                          "x": d["measure"], "y": d["group"]}))
    return out


CLAIM_FUNCS: Dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], List[Claim]]] = {
    "dataset_summary": claims_dataset_summary, "data_quality": claims_data_quality, "missing_values": claims_missing, "discover": claims_discover, "correlation": claims_correlation,
    "column_stats": claims_column_stats, "frequency": claims_frequency, "group_compare": claims_group_compare}


# ====================================================================== Phase 5 results -> sentences
def claims_model(model: Mapping[str, Any], report: Optional[Mapping[str, Any]], ctx: Mapping[str, Any]) -> List[Claim]:
    name = model.get("name")
    cv = f" (cross-validated {num(model.get('cv_score'))})" if model.get("cv_score") is not None else ""
    out = [Claim("prediction", "model", f"A {model.get('algorithm_label') or model.get('algorithm')} model named {q(name)} was trained to predict {q(model.get('target'))} ({str(model.get('problem_type', '')).replace('_', ' ')}) "
                 f"from {ctx['name']}; on held-out rows its {model.get('primary_label')} was {num(model.get('test_score'))}{cv}.", [MODEL_CAVEAT],
                 {"model": name, "target": model.get("target"), "metric": model.get("primary_label"), "test_score": model.get("test_score"), "cv_score": model.get("cv_score")})]
    ev = ((report or {}).get("evaluation") or {})
    m = ev.get("metrics") or {}
    keys = [("accuracy", "accuracy"), ("precision", "precision"), ("recall", "recall"), ("f1", "F1")] if "accuracy" in m else [("r2", "R²"), ("rmse", "RMSE"), ("mae", "MAE")]
    shown = [(label, m[k]) for k, label in keys if isinstance(m.get(k), (int, float))]
    if shown and ev.get("n"):
        out.append(Claim("prediction", "evaluation", f"On {num(ev.get('n'))} held-out rows the model {q(name)} scored " + ", ".join(f"{label} {num(v)}" for label, v in shown) + ".", [MODEL_CAVEAT],
                         {"model": name, "n": ev.get("n"), **{label: v for label, v in shown}}))
    imp = (((report or {}).get("importance") or {}).get("permutation") or {})
    feats = [f for f in imp.get("features") or [] if isinstance(f, dict) and (f.get("importance") or 0) > 0][:4]
    if imp.get("available") and feats:
        reliable = [f for f in feats if f.get("reliable")]
        text = (f"For the model {q(name)}, shuffling " + ", ".join(f"{q(f['feature'])} (share {num((f.get('share') or 0) * 100)}%)" for f in feats[:3]) + " hurts its predictions most; "
                + (f"{', '.join(q(f['feature']) for f in reliable)} {'is' if len(reliable) == 1 else 'are'} well above run-to-run noise." if reliable else "none of these is clearly above run-to-run noise."))
        out.append(Claim("prediction", "importance", text, [MODEL_CAVEAT], {"model": name, "features": [f["feature"] for f in feats[:3]], "reliable": [f["feature"] for f in reliable]}))
    return out


# ====================================================================== comparing two datasets
def claims_compare(items: Sequence[Mapping[str, Any]]) -> List[Claim]:
    """items: [{"name", "summary": dataset_summary data, "stats": {column: column_stats row}}] for two datasets."""
    if len(items) < 2:
        return []
    a, b = items[0], items[1]
    sa, sb = a["summary"], b["summary"]
    cols_a, cols_b = set(a.get("columns") or []), set(b.get("columns") or [])
    shared = sorted(cols_a & cols_b)
    out = [Claim("compare_datasets", "size", f"{a['name']} has {num(sa.get('rows_in_dataset'))} rows and {num(sa.get('columns'))} columns; {b['name']} has {num(sb.get('rows_in_dataset'))} rows and "
                 f"{num(sb.get('columns'))} columns. They share {len(shared)} column name(s)" + (f": {', '.join(q(c) for c in shared[:8])}." if shared else "."), [],
                 {"a": a["name"], "b": b["name"], "rows_a": sa.get("rows_in_dataset"), "rows_b": sb.get("rows_in_dataset"), "shared": len(shared)})]
    for col in [c for c in shared if c in (a.get("stats") or {}) and c in (b.get("stats") or {})][:5]:
        x, y = a["stats"][col], b["stats"][col]
        if x.get("mean") is None or y.get("mean") is None:
            continue
        out.append(Claim("compare_datasets", f"col:{col}", f"{q(col)}: mean {num(x['mean'])} in {a['name']} versus {num(y['mean'])} in {b['name']} (median {num(x.get('median'))} versus {num(y.get('median'))}). "
                         "This describes the two files; it is not a test of whether they differ by more than chance.", [], {"column": col, "mean_a": x["mean"], "mean_b": y["mean"], "x": col, "y": col}))
    return out


# ====================================================================== rebuilding a sentence from a stored result (the citation check)
class RegenerateError(Exception):
    pass


def regenerate(calc: Mapping[str, Any], load_run: Callable[[str], Mapping[str, Any]], load_model: Optional[Callable[[str], Tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]]] = None) -> str:
    """The sentence that `calc` (a citation's recipe) produces from the stored results right now. Raises RegenerateError if the stored result is gone, has changed or cannot produce it."""
    def op_data(ref: Mapping[str, Any]) -> Mapping[str, Any]:
        run = load_run(ref["run_id"])
        res = run.get("result") or {}
        if res.get("result_hash") != ref.get("result_hash"):
            raise RegenerateError("The stored result of this calculation has changed.")
        results = res.get("results") or []
        i = ref.get("op_index")
        if not isinstance(i, int) or not 0 <= i < len(results) or results[i].get("op") != ref.get("op") or results[i].get("status") != "ok":
            raise RegenerateError("The calculation's stored result no longer contains this operation.")
        return results[i].get("data") or {}
    fn = calc.get("fn")
    if fn in CLAIM_FUNCS:
        claims = CLAIM_FUNCS[fn](op_data(calc["run"]), calc.get("ctx") or {})
    elif fn == "compare":
        items = []
        for it in calc.get("inputs") or []:
            stats = {c["column"]: c for c in (op_data(it["stats"]).get("columns") or [])} if it.get("stats") else {}
            items.append({"name": it["name"], "summary": op_data(it["summary"]), "stats": stats, "columns": it.get("columns") or []})
        claims = claims_compare(items)
    elif fn == "model":
        if load_model is None:
            raise RegenerateError("Models cannot be checked here.")
        model, report = load_model(calc["model_id"])
        claims = claims_model(model, report, calc.get("ctx") or {})
    else:
        raise RegenerateError("Unknown calculation.")
    found = next((c for c in claims if c.key == calc.get("claim")), None)
    if found is None:
        raise RegenerateError("The stored result no longer produces this statement.")
    return str(calc.get("prefix") or "") + found.text


# ====================================================================== the researcher
def detect_target(question: str, columns: Sequence[Mapping[str, Any]], models: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """The column the question is about: one it names (preferring the one after 'with/of/for/predict/affect'), else the target of the dataset's trained model, else None."""
    low = " " + re.sub(r"[_\-]+", " ", question.lower()) + " "
    named: List[Tuple[int, str]] = []
    for c in columns:
        words = _col(str(c["name"])).strip()
        if not words:
            continue
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(words)}(?:s|es)?(?![a-z0-9])", low):
            named.append((m.start(), c["name"]))
            break
    named.sort()
    if not named:
        return models[0].get("target") if models else None
    after = [n for pos, n in named if re.search(r"(with|of|for|predict\w*|affect\w*|influenc\w*|driv\w*|determin\w*|explain\w*|on|about)\s+(?:the\s+)?$", low[max(0, pos - 24):pos])]
    return (after or [n for _, n in named])[0]


def _error_text(e: Exception) -> str:
    return str(getattr(e, "message", None) or e)[:160]


class DatasetResearcher:
    """Runs the verified analytics for a question and returns candidates ready for `extract_evidence` (each already assigned to the dataset subtopic that asked for it)."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws

    # ---- one dataset
    def analyse(self, principal: Principal, project_id: str, plan: Mapping[str, Any], question: str, dataset_ids: Sequence[str], progress: Callable[[str, float, str], None]) -> Dict[str, Any]:
        sections = {st["intent"]: st["id"] for st in plan["subtopics"] if st.get("scope") == "dataset"}
        candidates: List[Dict[str, Any]] = []
        calculations: List[Dict[str, Any]] = []
        notes: List[str] = []
        compare_inputs: List[Dict[str, Any]] = []
        for n, aid in enumerate(list(dataset_ids)[:MAX_DATASETS]):
            progress("calculating", 0.1 + 0.15 * n / max(1, len(dataset_ids)), "Calculating on your dataset")
            try:
                asset = dict(self.ws.assets.get_owned(principal, aid))
                if asset["project_id"] != project_id or asset["kind"] != "dataset":
                    continue
                got = self._one(principal, asset, plan, question, sections, notes)
            except AppError as e:
                notes.append(f"A dataset could not be analysed: {_error_text(e)}")
                continue
            candidates += got["candidates"]
            calculations += got["calculations"]
            if got.get("compare"):
                compare_inputs.append(got["compare"])
        if "compare_datasets" in sections and len(compare_inputs) >= 2:
            cands = self._compare(compare_inputs, sections["compare_datasets"])
            candidates += cands
        elif "compare_datasets" in sections:
            notes.append("Comparing datasets needs at least two selected datasets that could be analysed.")
        return {"candidates": candidates, "calculations": calculations, "notes": notes}

    def _one(self, principal: Principal, asset: Mapping[str, Any], plan: Mapping[str, Any], question: str, sections: Mapping[str, str], notes: List[str]) -> Dict[str, Any]:
        an = self.ws.analytics
        schema = an.schema(principal, asset["id"])
        columns = schema["columns"]
        models: List[Dict[str, Any]] = []
        try:
            models = self.ws.predictive.list_models(principal, asset["id"], limit=5)["items"]
        except AppError:
            models = []
        target = detect_target(question, columns, models)
        roles = {c["name"]: c["role"] for c in columns}
        numeric = [c["name"] for c in columns if c["role"] == "numeric" and not is_identifier(c["name"])]
        label = f"Research: {question[:70]}"
        name = asset["name"]
        ctx = {"name": name, "target": target}
        src = {"key": f"calc:{asset['id']}", "kind": "analysis", "origin": "calculated", "title": f"Calculations on {name}", "asset_id": asset["id"], "ref_id": asset.get("current_version_id")}
        out: List[Dict[str, Any]] = []
        calcs: List[Dict[str, Any]] = []
        compare: Dict[str, Any] = {"name": name, "columns": [c["name"] for c in columns]}

        def run(kind: str, ops: List[Dict[str, Any]]) -> Optional[Mapping[str, Any]]:
            try:
                try:
                    r = an.run(principal, asset["id"], kind=kind, operations=ops, label=label)
                except AppError as e:
                    if str(getattr(e, "code", "")).endswith("TOO_LARGE"):
                        r = an.run(principal, asset["id"], kind=kind, operations=ops, label=label, sample_rows=SAMPLE_ROWS, seed=20240601)
                        notes.append(f"{name} is large, so a random sample of up to {SAMPLE_ROWS:,} rows was used for these calculations (stated in each result).")
                    else:
                        raise
            except AppError as e:
                notes.append(f"A calculation on {name} could not be run ({', '.join(o['op'] for o in ops)}): {_error_text(e)}")
                return None
            calcs.append({"asset_id": asset["id"], "dataset": name, "run_id": r["id"], "kind": r["kind"], "operations": [o["op"] for o in ops], "result_hash": r["result_hash"], "rows_used": r.get("rows_used"),
                          "evidence_status": r.get("evidence_status")})
            return r

        def add(r: Mapping[str, Any], index: int, claim_ctx: Mapping[str, Any], skip: Optional[Callable[[Claim], bool]] = None) -> None:
            res = r["result"]["results"][index]
            if res.get("status") != "ok":
                notes.append(f"The calculation '{res['op']}' on {name} did not complete: {res.get('error', {}).get('message') if isinstance(res.get('error'), dict) else res.get('status')}.")
                return
            for c in CLAIM_FUNCS[res["op"]](res.get("data") or {}, claim_ctx):
                if skip is not None and skip(c):
                    continue
                sid = sections.get(c.section) or (sections.get("overview") if c.section == "distribution" else None)
                if sid is None:
                    continue
                ref = {"run_id": r["id"], "op": res["op"], "op_index": index, "result_hash": r["result_hash"]}
                out.append({"kind": "analysis", "origin": "calculated", "text": c.text, "score": 1.0, "subtopic": sid, "caveats": c.caveats, "facts": {**c.facts, "dataset": name},
                            "citation": {"kind": "calculation", "asset_id": asset["id"], "document": name, "run_id": r["id"], "op": res["op"], "result_hash": r["result_hash"], "claim": c.key,
                                         "calc": {"fn": res["op"], "claim": c.key, "run": ref, "ctx": dict(claim_ctx)}}, "source": src})

        wants = set(sections)
        # ---- A. the dataset in numbers and its data-quality checks
        ops_a: List[Dict[str, Any]] = []
        if wants & {"overview", "compare_datasets"}:
            ops_a.append({"op": "dataset_summary", "params": {}})
        if "quality" in wants:
            ops_a += [{"op": "data_quality", "params": {}}, {"op": "missing_values", "params": {}}]
        ra = run("dashboard", ops_a) if ops_a else None
        if ra is not None:
            for i, o in enumerate(ops_a):
                add(ra, i, ctx)
            if ops_a[0]["op"] == "dataset_summary" and ra["result"]["results"][0].get("status") == "ok":
                compare["summary_ref"] = {"run_id": ra["id"], "op": "dataset_summary", "op_index": 0, "result_hash": ra["result_hash"]}
                compare["summary"] = ra["result"]["results"][0]["data"]
        # ---- B. relationships (towards the outcome the question names, if it names one)
        discovered: List[Mapping[str, Any]] = []
        if wants & {"relationships", "groups"} and len(columns) >= 2:
            params: Dict[str, Any] = {"top_k": 12}
            if target:
                params["target"] = target
            ops_b = [{"op": "discover", "params": params}]
            usable = [c for c in numeric if c != target][:12]
            if "relationships" in wants and len(usable) + (1 if target in numeric else 0) >= 2:
                ops_b.append({"op": "correlation", "params": {"columns": ([target] if target in numeric else []) + usable[:11], "top": 20}})
            rb = run("relationships", ops_b)
            if rb is not None:
                if rb["result"]["results"][0].get("status") == "ok":
                    discovered = [p for p in rb["result"]["results"][0]["data"].get("patterns") or [] if isinstance(p, dict)]
                seen_pairs = {frozenset((p.get("x"), p.get("y"))) for p in discovered}
                if "relationships" in wants:
                    for i, o in enumerate(ops_b):
                        # a pair `discover` already reported is not repeated by the correlation table
                        add(rb, i, ctx, skip=(lambda c: frozenset((c.facts.get("x"), c.facts.get("y"))) in seen_pairs) if o["op"] == "correlation" else None)
        # ---- C. distributions (numeric columns first that the question or the findings point at)
        focus_cols = [c for c in dict.fromkeys(([target] if target else []) + [p["x"] for p in discovered] + [p["y"] for p in discovered]) if roles.get(c) == "numeric"]
        limit = MAX_NUMERIC if "distribution" in wants else 4                          # an overview shows a few; a question about distributions shows more
        stats_cols = (focus_cols + [c for c in numeric if c not in focus_cols])[:limit]
        ops_c: List[Dict[str, Any]] = []
        if stats_cols and wants & {"distribution", "overview", "compare_datasets"}:
            ops_c.append({"op": "column_stats", "params": {"columns": stats_cols}})
        if target and roles.get(target) == "categorical" and wants & {"distribution", "overview"}:
            ops_c.append({"op": "frequency", "params": {"column": target}})
        if ops_c:
            rc = run("descriptive", ops_c)
            if rc is not None:
                for i, _ in enumerate(ops_c):
                    add(rc, i, ctx)
                if ops_c[0]["op"] == "column_stats" and rc["result"]["results"][0].get("status") == "ok":
                    compare["stats"] = {c["column"]: c for c in rc["result"]["results"][0]["data"].get("columns") or []}
                    compare["stats_ref"] = {"run_id": rc["id"], "op": "column_stats", "op_index": 0, "result_hash": rc["result_hash"]}
        # ---- D. group differences
        if "groups" in wants:
            group_col, measures = self._group_plan(question, columns, roles, target, discovered)
            ops_d = [{"op": "group_compare", "params": {"measure": m, "group": group_col}} for m in measures[:3]] if group_col else []
            if ops_d:
                rd = run("compare", ops_d)
                if rd is not None:
                    for i, _ in enumerate(ops_d):
                        add(rd, i, ctx)
            else:
                notes.append(f"No group comparison was made on {name}: the question and the data did not identify a numeric column and a column that splits the rows into groups.")
        # ---- E. trained models (Phase 5)
        if "prediction" in wants:
            relevant = [m for m in models if not target or m.get("target") == target] or models
            if not relevant:
                notes.append(f"{name} has no trained model yet, so nothing can be said about prediction. Train one in the Predictive section and ask again.")
            for m in relevant[:2]:
                report = None
                try:
                    report = self.ws.predictive.report(principal, m["id"])
                except AppError:
                    report = None
                for c in claims_model(m, report, ctx):
                    sid = sections.get(c.section)
                    if sid is None:
                        continue
                    out.append({"kind": "model", "origin": "model_prediction", "text": c.text, "score": 0.9, "subtopic": sid, "caveats": c.caveats, "facts": {**c.facts, "dataset": name},
                                "citation": {"kind": "model", "asset_id": asset["id"], "document": name, "model_id": m["id"], "claim": c.key, "calc": {"fn": "model", "model_id": m["id"], "claim": c.key, "ctx": dict(ctx)}},
                                "source": {"key": f"model:{m['id']}", "kind": "model", "origin": "model_prediction", "title": f"Model '{m.get('name')}'", "asset_id": asset["id"], "ref_id": m["id"]}})
        # ---- F0. findings already saved for this dataset (Phase 4 insights)
        if wants & {"explain", "overview", "relationships"}:
            try:
                saved = [i for i in self.ws.insights.list(principal, asset["id"], limit=8)["items"] if i.get("statement")][:3]
            except (AppError, AttributeError):
                saved = []
            sid = sections.get("explain") or sections.get("relationships") or sections.get("overview")
            for i in saved:
                out.append({"kind": "analysis", "origin": "calculated", "text": f"Saved finding: {i['statement']} (evidence status: {i.get('evidence_status', 'unknown')})"[:1400], "score": 0.8, "subtopic": sid,
                            "caveats": [], "facts": {"dataset": name},
                            "citation": {"asset_id": asset["id"], "document": name, "kind": "insight", "insight_id": i.get("id")}, "source": {**src, "key": f"analysis:{asset['id']}", "title": f"Saved findings from {name}"}})
        # ---- F. results already in the project
        if "explain" in wants:
            try:
                prior = [r for r in an.list_runs(principal, asset["id"], limit=6)["items"] if r["status"] == "completed" and r.get("label", "") != label and not str(r.get("label") or "").startswith("Research:")][:3]
            except AppError:
                prior = []
            if not prior:
                notes.append(f"There are no earlier statistical results saved for {name}; run an analysis first, or ask a broader question.")
            for pr in prior:
                try:
                    full = an.get_run(principal, pr["id"])
                except AppError:
                    continue
                for i, res in enumerate((full.get("result") or {}).get("results") or []):
                    if res.get("status") != "ok" or res.get("op") not in CLAIM_FUNCS:
                        continue
                    for c in CLAIM_FUNCS[res["op"]](res.get("data") or {}, ctx):
                        sid = sections.get("explain")
                        ref = {"run_id": full["id"], "op": res["op"], "op_index": i, "result_hash": full["result"]["result_hash"]}
                        out.append({"kind": "analysis", "origin": "calculated", "text": f"Earlier result ({full.get('label') or full['kind']}): {c.text}", "score": 0.9, "subtopic": sid, "caveats": c.caveats,
                                    "facts": {**c.facts, "dataset": name}, "citation": {"kind": "calculation", "asset_id": asset["id"], "document": name, "run_id": full["id"], "op": res["op"],
                                                                                    "result_hash": full["result"]["result_hash"], "claim": c.key, "calc": {"fn": res["op"], "claim": c.key, "run": ref, "ctx": dict(ctx), "prefix": f"Earlier result ({full.get('label') or full['kind']}): "}},
                                    "source": src})
        return {"candidates": out, "calculations": calcs, "compare": compare if "summary" in compare else None}

    @staticmethod
    def _group_plan(question: str, columns: Sequence[Mapping[str, Any]], roles: Mapping[str, str], target: Optional[str], discovered: Sequence[Mapping[str, Any]]) -> Tuple[Optional[str], List[str]]:
        """(grouping column, numeric measures): from the columns the question names, else the outcome and the strongest numeric relationships found."""
        low = " " + re.sub(r"[_\-]+", " ", question.lower()) + " "
        named = [c["name"] for c in columns if re.search(rf"(?<![a-z0-9]){re.escape(_col(c['name']))}", low)]
        cats = [c for c in named if roles.get(c) == "categorical"]
        nums = [c for c in named if roles.get(c) == "numeric"]
        group = (target if target and roles.get(target) == "categorical" else None) or (cats[0] if cats else None)
        if group is None:
            for p in discovered:
                pair = [p.get("x"), p.get("y")]
                cat = next((c for c in pair if roles.get(c) == "categorical"), None)
                if cat and p.get("kind") == "num_cat":
                    group = cat
                    break
        if group is None:
            return None, []
        measures = [c for c in nums if c != group]
        for p in discovered:
            for c in (p.get("x"), p.get("y")):
                if roles.get(c) == "numeric" and c not in measures and (p.get("x") == group or p.get("y") == group):
                    measures.append(c)
        if not measures:
            measures = [c["name"] for c in columns if c["role"] == "numeric"][:3]
        return group, measures

    # ---- two datasets side by side
    def _compare(self, inputs: Sequence[Mapping[str, Any]], subtopic: str) -> List[Dict[str, Any]]:
        a, b = inputs[0], inputs[1]
        items = [{"name": x["name"], "summary": x["summary"], "stats": x.get("stats") or {}, "columns": x["columns"]} for x in (a, b)]
        calc_inputs = [{"name": x["name"], "summary": x["summary_ref"], "stats": x.get("stats_ref"), "columns": x["columns"]} for x in (a, b)]
        out = []
        for c in claims_compare(items):
            out.append({"kind": "analysis", "origin": "calculated", "text": c.text, "score": 1.0, "subtopic": subtopic, "caveats": c.caveats, "facts": c.facts,
                        "citation": {"kind": "calculation", "asset_id": None, "document": f"{a['name']} and {b['name']}", "claim": c.key, "calc": {"fn": "compare", "claim": c.key, "inputs": calc_inputs}},
                        "source": {"key": "calc:compare:" + ":".join(sorted(x["name"] for x in (a, b))), "kind": "analysis", "origin": "calculated", "title": f"Comparison of {a['name']} and {b['name']}",
                                   "compared": [a["name"], b["name"]]}})
        return out
