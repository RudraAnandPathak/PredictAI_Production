"""Turns structured results into report content. Nothing here invents a fact: every sentence is either a statement the evidence already makes (kept with its evidence references and
caveats), a figure copied from a calculation, or a plain description of how the result was produced (which points at the calculation or research result it describes).

A "pool" maps a section key to what is available for it; a template then chooses and orders the sections. A requested section with nothing behind it is kept and marked incomplete."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..p7 import jload
from . import evidence as ev
from .templates import SECTION_KEYS

KIND_OF = {"stated": "fact", "calculated": "calculation", "forecast": "prediction", "opinion": "interpretation", "uncertain": "interpretation"}


def claim(text: str, evidence: Sequence[str], kind: str = "fact", caveats: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    return {"text": text.strip(), "evidence": list(dict.fromkeys(evidence)), "kind": kind, "caveats": list(caveats or [])}


def para(claims: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "paragraph", "claims": list(claims)}


def table(caption: str, columns: Sequence[str], rows: Sequence[Sequence[Any]], evidence: Sequence[str]) -> Dict[str, Any]:
    return {"type": "table", "caption": caption, "columns": list(columns), "rows": [list(r) for r in rows], "evidence": list(dict.fromkeys(evidence))}


def figure(caption: str, title: str, labels: Sequence[str], values: Sequence[float], evidence: Sequence[str], kind: str = "bar") -> Dict[str, Any]:
    return {"type": "figure", "caption": caption, "chart": {"kind": kind, "title": title, "labels": [str(x) for x in labels], "values": list(values)}, "evidence": list(dict.fromkeys(evidence))}


def note(text: str) -> Dict[str, Any]:
    return {"type": "note", "text": text}


def sec(blocks: Optional[Sequence[Dict[str, Any]]] = None, *, subsections: Optional[Sequence[Dict[str, Any]]] = None, status: str = "complete", why: Optional[str] = None) -> Dict[str, Any]:
    return {"blocks": list(blocks or []), "subsections": list(subsections or []), "status": status, "why": why}


def empty(why: str) -> Dict[str, Any]:
    return sec(status="incomplete", why=why)


# ===================================================================================================================================== research
def research_pool(bundles: Sequence[Mapping[str, Any]], es: ev.EvidenceSet) -> Dict[str, Dict[str, Any]]:
    pool: Dict[str, Dict[str, Any]] = {}
    for b in bundles:
        part = _research_one(b, es)
        for key, val in part.items():
            if key not in pool:
                pool[key] = val
            else:
                pool[key]["blocks"] += val["blocks"]
                pool[key]["subsections"] += val["subsections"]
                if val["status"] == "incomplete" and not pool[key]["blocks"] and not pool[key]["subsections"]:
                    pool[key]["status"] = "incomplete"
    return pool


def _research_one(b: Mapping[str, Any], es: ev.EvidenceSet) -> Dict[str, Dict[str, Any]]:
    emap = ev.from_research_bundle(b, es)
    proc = ev.calculation(es, key=f"result:{b['id']}", title=f"Saved research result: {b['question']}", source_type="research_result", calculation_ref=f"research_bundle:{b['id']}",
                          passage=f"Research result {b['id']} (run {b.get('number')}), status {b['status']}, {b['counts']['sources']} sources, {b['counts']['evidence']} passages.",
                          when=b.get("created_at"), source_id=b["id"], limitation="This describes how the research was done, not a finding.")
    # tie the process reference to the bundle's integrity
    rep = b.get("report") or {}
    expl = rep.get("explanation") or {}
    R = lambda ids: [emap[i] for i in ids if i in emap]  # noqa: E731
    pool: Dict[str, Dict[str, Any]] = {}

    direct = expl.get("direct_answer") or []
    if direct:
        pool["summary"] = sec([para([claim(d["text"], R(d.get("evidence", [])), "fact") for d in direct if R(d.get("evidence", []))])])
    else:
        concl = [c for c in b.get("conclusions", [])][:5]
        pool["summary"] = sec([para([claim(c["text"], R(c["evidence"]), KIND_OF.get(c["kind"], "fact"), c.get("caveats")) for c in concl])]) if concl else empty("The research found nothing to summarise.")

    pool["question"] = sec([para([claim(f"The research asked: “{b['question']}”", [proc], "process")])])
    plan = b.get("plan") or {}
    parts = [s.get("title") for s in plan.get("subtopics", []) if s.get("title")]
    pool["scope"] = sec([para([claim(f"The question was divided into {len(parts)} parts: " + "; ".join(parts) + ".", [proc], "process")])]) if parts else empty("The plan listed no parts.")
    origins = Counter((s.get("origin_label") or s.get("origin") or "source") for s in b.get("sources", []) if s.get("status") == "ok")
    provs = (rep.get("providers") or [])
    method = [f"The saved research result (run {b.get('number')}, level “{b.get('level_label') or b.get('level')}”) read {b['counts']['sources']} source(s) ({', '.join(f'{n} {k}' for k, n in origins.items()) or 'none'}) and quoted {b['counts']['evidence']} passage(s).",
              f"Its own checks gave the verdict “{(b.get('quality') or {}).get('verdict', 'not recorded')}” and the result's status is “{b['status']}”."]
    if provs:
        method.append("Search providers: " + "; ".join(f"{p['provider']} {p['status']}" for p in provs) + ".")
    pool["method"] = sec([para([claim(t, [proc], "process") for t in method])])
    rows = [[s.get("title"), s.get("origin_label") or s.get("origin"), s.get("domain") or "", (s.get("freshness") or {}).get("label", "")] for s in b.get("sources", []) if s.get("status") == "ok"]
    pool["background"] = sec([table("Sources read for this research", ["Source", "Kind", "Domain", "Freshness"], rows, [proc])]) if rows else empty("No source was read.")

    subs = []
    for s in rep.get("sections", []):
        items = s.get("items", [])
        if not items:
            subs.append({"heading": s["title"], "blocks": [note(s.get("note") or "Nothing relevant was found for this part.")], "status": "incomplete", "why": s.get("note")})
            continue
        subs.append({"heading": s["title"], "blocks": [para([claim(i["text"], R(i["evidence"]), KIND_OF.get(i["kind"], "fact"), i.get("caveats")) for i in items if R(i["evidence"])])], "status": "complete", "why": None})
    pool["findings"] = sec(subsections=subs) if subs else empty("The research produced no findings.")

    ana = []
    conn = expl.get("connections") or []
    if conn:
        ana.append(para([claim(c["text"], R(c.get("evidence", [])), "interpretation") for c in conn if R(c.get("evidence", []))]))
    sbs = expl.get("side_by_side") or []
    if sbs:
        ana.append(table("Statements for each subject, side by side", ["Subject", "Statement"], [[e.get("name") or e.get("key"), st["text"]] for e in sbs for st in e.get("statements", [])],
                         [r for e in sbs for st in e.get("statements", []) for r in R(st.get("evidence", []))]))
    timing = expl.get("timing")
    if timing:
        ana.append(note(timing.get("note") or "Statements were sorted into immediate and long-term by their own wording."))
    counts = [(s["title"], len(s.get("items", []))) for s in rep.get("sections", [])]
    if counts:
        ana.append(figure("Quoted passages for each part of the question", "Passages per part", [c[0] for c in counts], [c[1] for c in counts], [proc]))
    pool["analysis"] = sec(ana) if ana else empty("No analysis connecting the findings was produced.")

    conflicts = b.get("conflicts") or []
    disc = [para([claim("Sources disagree: " + str(c.get("summary") or c.get("note") or c.get("text") or c.get("statement") or "see the research result for the conflict.") , [proc], "process") for c in conflicts])] if conflicts else \
           [para([claim("No conflict between sources was detected. That is not proof that the sources agree: most statements rest on a single source.", [proc], "process")])]
    pool["discussion"] = sec(disc)

    lims = list(b.get("limitations") or []) + list((rep.get("uncertainty") or [])[:6])
    pool["limitations"] = sec([para([claim(t, [proc], "process") for t in lims[:12]])]) if lims else empty("No limitation was recorded.")
    unknown = [g.get("note") and f"{g.get('title')}: {g['note']}" or str(g) for g in b.get("gaps", [])] + list(expl.get("unknown") or [])
    pool["unresolved"] = sec([para([claim(t, [proc], "process") for t in unknown])]) if unknown else sec([para([claim("The research recorded no open gap.", [proc], "process")])])
    answered = sum(1 for s in rep.get("sections", []) if s.get("items"))
    total = len(rep.get("sections", []))
    pool["conclusion"] = sec([para([claim(f"The evidence answers {answered} of {total} parts of the question; each statement above keeps its caveat and its source. Parts without evidence are marked incomplete rather than filled in.", [proc], "process")])])
    pool["recommendations"] = empty("Research evidence describes what sources say; it does not by itself support recommendations. Add them in the editor and mark them as suggestions.")
    return pool


# ===================================================================================================================================== dataset analysis
def dataset_pool(runs: Sequence[Mapping[str, Any]], assets: Mapping[str, Mapping[str, Any]], es: ev.EvidenceSet) -> Dict[str, Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    summary: List[Dict[str, Any]] = []
    method: List[Dict[str, Any]] = []
    limits: List[str] = []
    all_refs: List[str] = []
    for run in runs:
        asset = assets.get(run["asset_id"], {})
        name = asset.get("name", "dataset")
        result = run.get("result") or {}
        for r in result.get("results", []):
            if r.get("status") != "ok":
                continue
            data = r.get("data") or {}
            scal = {k: v for k, v in data.items() if isinstance(v, (int, float, str, bool)) and v is not None}
            ref = ev.calculation(es, key=f"run:{run['id']}:{r['op']}", title=f"{r['op'].replace('_', ' ')} of {name}", calculation_ref=f"analysis_run:{run['id']}#{r['op']}",
                                 passage="; ".join(f"{k}={v}" for k, v in list(scal.items())[:12]), dataset_version=run.get("version_id"), when=run.get("created_at"),
                                 limitation=None if run.get("evidence_status") == "VERIFIED" else "The analysis evidence was marked INCOMPLETE.", source_id=run["id"])
            all_refs.append(ref)
            if r["op"] == "dataset_summary" and "rows_in_scope" in data:
                summary.append(claim(f"{name} has {data['rows_in_scope']} rows and {data.get('columns')} columns in scope.", [ref], "calculation"))
                dup = (data.get("duplicate_rows") or {}).get("count")
                if dup is not None:
                    summary.append(claim(f"{dup} duplicate row(s) were found.", [ref], "calculation"))
            if scal:
                findings.append({"heading": f"{r['op'].replace('_', ' ').capitalize()} ({name})", "blocks": [table(f"{r['op'].replace('_', ' ')}: headline figures from the stored calculation", ["Measure", "Value"], [[k, v] for k, v in list(scal.items())[:20]], [ref])], "status": "complete", "why": None})
        method.append(claim(f"Analysis run {run['id'][:8]} on {name} (dataset version {str(run.get('version_id'))[:8]}) used {run.get('rows_used')} rows; plan hash {str(run.get('plan_hash'))[:12]}, calculation version {run.get('calc_version')}, status {run.get('evidence_status')}.",
                            [r for r in all_refs[-1:]], "process"))
        if run.get("evidence_status") != "VERIFIED":
            limits.append(f"Run {run['id'][:8]} has incomplete evidence.")
    pool: Dict[str, Dict[str, Any]] = {}
    pool["summary"] = sec([para(summary)]) if summary else empty("No calculation produced a summary figure.")
    pool["objectives"] = empty("State the objective in the editor; the analysis results do not say why the analysis was run.")
    pool["method"] = sec([para(method)]) if method else empty("No analysis run was selected.")
    pool["findings"] = sec(subsections=findings) if findings else empty("The selected runs produced no figures.")
    pool["analysis"] = sec([note("Interpretation of the figures belongs in the editor. Calculated figures state what is in the data; they do not explain why.")], status="incomplete", why="No interpretation was written.")
    pool["limitations"] = sec([para([claim(t, all_refs[:1], "process") for t in limits or ["Results describe this dataset version only and say nothing about data that was not in it."]])]) if all_refs else empty("No calculation.")
    pool["unresolved"] = sec([note("Causes of the patterns are not established by descriptive analysis.")])
    pool["conclusion"] = empty("Write the conclusion after reviewing the findings.")
    pool["recommendations"] = empty("Recommendations must be written and owned by the author.")
    return pool


# ===================================================================================================================================== model
def model_pool(versions: Sequence[Mapping[str, Any]], es: ev.EvidenceSet) -> Dict[str, Dict[str, Any]]:
    summary, method, subs, limits, refs = [], [], [], [], []
    for v in versions:
        meta, evl = v["metadata"], v["evaluation"]
        label = f"{v.get('name', 'model')} v{v['version']}"
        ref = ev.calculation(es, key=f"model:{v['id']}", title=f"Evaluation of {label}", source_type="model", calculation_ref=f"registry_version:{v['id']}", model_version=label,
                             dataset_version=(meta.get("lineage") or {}).get("dataset_version_id"), when=v["created_at"], passage=f"{meta.get('algorithm_label')} predicting {meta.get('target')}; hold-out {meta.get('primary_metric')} {(evl.get('test') or {}).get('primary_score')}; cross-validation {(evl.get('validation') or {}).get('score')}",
                             limitation="Scores describe the held-out rows of this dataset, not future data.", source_id=v["id"], uncertainty="No confidence interval is stored for the hold-out score.")
        refs.append(ref)
        tm = (evl.get("test") or {}).get("metrics") or {}
        summary.append(claim(f"{label} ({meta.get('algorithm_label')}) predicts “{meta.get('target')}”; its hold-out {meta.get('primary_metric')} is {(evl.get('test') or {}).get('primary_score')} and its cross-validation score is {(evl.get('validation') or {}).get('score')}.", [ref], "calculation"))
        method.append(claim(f"{label} was trained on dataset version {str((meta.get('lineage') or {}).get('dataset_version_id'))[:8]} with seed {meta.get('seed')}; artifact checksum {str(v['artifact']['sha256'])[:12]}; lifecycle state “{v['status']}” (a record inside PredictAI, not a deployment).", [ref], "process"))
        subs.append({"heading": f"{label}: hold-out and validation", "blocks": [
            table("Hold-out metrics (rows never used to fit or choose the model)", ["Metric", "Value"], [[k, tm[k]] for k in tm if tm[k] is not None][:14], [ref]),
            table("Validation (cross-validation on training rows)", ["Metric", "Score", "Folds"], [[(evl.get("validation") or {}).get("metric"), (evl.get("validation") or {}).get("score"), (evl.get("validation") or {}).get("folds")]], [ref])], "status": "complete", "why": None})
        cal = ((evl.get("test") or {}).get("calibration") or {})
        if cal.get("verdict"):
            subs[-1]["blocks"].append(para([claim(f"Calibration check on the hold-out rows: {cal['verdict']} (mean gap {cal.get('mean_gap')}).", [ref], "calculation")]))
        limits += [str(x) for x in evl.get("limits", [])][:4]
    pool = {"summary": sec([para(summary)]), "objectives": empty("State what the model is for in the editor; the registry records how it was built, not why."), "method": sec([para(method)]),
            "findings": sec(subsections=subs), "analysis": sec([note("Explanations (importance, partial dependence) are in the model's report in the Predictions area.")]),
            "limitations": sec([para([claim(t, refs[:1], "process") for t in dict.fromkeys(limits)])]) if limits else empty("No limitation recorded."),
            "conclusion": empty("Write the conclusion after reviewing the results."), "recommendations": empty("Recommendations must be written and owned by the author."), "unresolved": sec([note("Performance on new data has not been measured.")])}
    return pool


# ===================================================================================================================================== generic assembly
def assemble(spec_sections: Sequence[str], pool: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    n = 0
    for key in spec_sections:
        n += 1
        p = pool.get(key) or empty("Nothing in the selected sources supports this section.")
        out.append({"id": f"s{n}", "key": key, "heading": SECTION_KEYS.get(key, key), "status": p["status"], "why": p.get("why"),
                    "blocks": [_bid(b, f"s{n}", i) for i, b in enumerate(p["blocks"])],
                    "subsections": [{"id": f"s{n}.{j + 1}", "heading": s["heading"], "status": s.get("status", "complete"), "why": s.get("why"),
                                     "blocks": [_bid(b, f"s{n}.{j + 1}", i) for i, b in enumerate(s["blocks"])]} for j, s in enumerate(p.get("subsections", []))]})
    return out


def _bid(block: Dict[str, Any], sid: str, i: int) -> Dict[str, Any]:
    return {"id": f"{sid}-b{i + 1}", **block}
