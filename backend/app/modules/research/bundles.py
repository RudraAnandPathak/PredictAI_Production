"""Evidence bundles: one self-contained, integrity-checked record of a research run. A bundle holds the question, the plan, the sources, the exact passages with their locations,
the citations, when everything was retrieved, reliability indicators, conflicts, conclusions, the quality verdict and the limitations.

Other parts of the platform (the assistant, analytics, predictive modelling, reports, exam research in Phase 7) read bundles through `reusable()`, which keeps every label
(origin, statement type, confidence) attached, so a forecast or a model estimate can never be quoted as a fact by mistake."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..analytics.csvsafe import csv_safe

SCHEMA = "predictai.evidence_bundle/1"
ENGINE = "research-engine/1"
PUBLIC_EVIDENCE_DROP = ("tokens",)
ORIGIN_WORDS = {"user_provided": "your file", "calculated": "calculated from your data", "model_prediction": "model estimate", "external_research": "external source"}
KIND_WORDS = {"stated": "stated as fact by the source", "uncertain": "hedged by the source", "opinion": "opinion or recommendation", "prediction": "forecast or estimate", "computed": "calculated by PredictAI"}
CONFIDENCE_WORDS = {"calculated": "calculated by PredictAI from your data and checked again from the stored result", "well_supported": "several sources say this", "single_source": "one source says this", "contested": "sources may disagree", "uncertain_wording": "the source itself is unsure", "prediction": "a forecast, not an established fact"}
LIMITS = ["Findings are quotations from sources. PredictAI has not independently verified that a source is correct.",
          "A search returns what a provider ranks highly, not everything that exists.",
          "Reliability indicators describe a source; they are not a verdict on whether it is right.",
          "Possible disagreements are found by comparing wording and numbers; subtle conflicts can be missed and some flagged ones may not be real."]


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(content: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json({k: v for k, v in content.items() if k != "integrity"})).hexdigest()


def assemble(*, bundle_id: str, session: Mapping[str, Any], number: int, job_id: Optional[str], plan: Mapping[str, Any], manifest: Mapping[str, Any], sources: Sequence[Mapping[str, Any]],
             evidence: Sequence[Mapping[str, Any]], findings: Sequence[Mapping[str, Any]], conflicts: Sequence[Mapping[str, Any]], conclusions: Sequence[Mapping[str, Any]],
             gaps: Sequence[Mapping[str, Any]], removed: Sequence[Mapping[str, Any]], collection: Mapping[str, Any], web: Mapping[str, Any], now: str, ai: Mapping[str, Any],
             extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    ev = [{k: v for k, v in e.items() if k not in PUBLIC_EVIDENCE_DROP} for e in evidence]
    asset_ids = sorted({s["asset_id"] for s in sources if s.get("asset_id")} | {a for s in sources for a in s.get("inherited_asset_ids", [])})
    content: Dict[str, Any] = {
        "schema": SCHEMA, "engine": ENGINE, "id": bundle_id, "session_id": session["id"], "project_id": session["project_id"], "number": number, "job_id": job_id, "created_at": now,
        "question": plan["question"], "level": plan["level"], "level_label": plan["level_label"], "purpose": session["purpose"],
        "plan": dict(plan), "context": dict(manifest), "sources": [dict(s) for s in sources], "evidence": ev, "findings": [dict(f) for f in findings], "conflicts": [dict(c) for c in conflicts],
        "conclusions": [dict(c) for c in conclusions], "gaps": [dict(g) for g in gaps], "removed": [dict(r) for r in removed],
        "collection": {"searches": collection.get("searches", []), "failures": collection.get("failures", []), "dropped": collection.get("dropped", []), "providers": collection.get("providers", [])},
        "used_web": bool(web.get("used")), "web": dict(web), "ai": dict(ai), "asset_ids": asset_ids, "limitations": [*plan.get("limitations", []), *LIMITS],
        "counts": {"sources": len(sources), "evidence": len(ev), "findings": len(findings), "conflicts": len(conflicts), "conclusions": len(conclusions), "gaps": len(gaps)},
    }
    for k, v in (extra or {}).items():
        content[k] = v                                                                # report, calculations: part of the sealed record, so covered by the integrity hash
    return content


def seal(content: Dict[str, Any], quality: Mapping[str, Any], status: str) -> Dict[str, Any]:
    content["quality"] = dict(quality)
    content["status"] = status
    content["integrity"] = {"algorithm": "sha256", "sha256": _digest(content), "covers": "every field except this one"}
    return content


def verify_integrity(bundle: Mapping[str, Any]) -> bool:
    integ = bundle.get("integrity") or {}
    return integ.get("algorithm") == "sha256" and integ.get("sha256") == _digest(bundle)


def dumps(bundle: Mapping[str, Any]) -> bytes:
    return canonical_json(bundle)


def listing(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {"id": row["id"], "session_id": row["session_id"], "number": row["number"], "question": row["question"], "level": row["level"], "status": row["status"],
            "quality_verdict": row["quality_verdict"], "used_web": bool(row["used_web"]), "source_count": row["source_count"], "evidence_count": row["evidence_count"],
            "finding_count": row["finding_count"], "conflict_count": row["conflict_count"], "label": row["label"], "saved": bool(row["saved"]), "created_at": row["created_at"]}


# ------------------------------------------------------------------ how other modules read a bundle
def cite(e: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> str:
    s = sources.get(e["source"], {})
    loc = e.get("location") or {}
    where = ""
    if loc.get("page_start"):
        where = f", p. {loc['page_start']}" + ("+" if loc.get("page_estimated") else "")
    elif loc.get("url"):
        where = f", {loc['url']}"
    return f"[{e['source']}] {s.get('title', 'source')}{where}"


def reusable(bundle: Mapping[str, Any]) -> Dict[str, Any]:
    """What a consumer may rely on: labelled conclusions with their citations, never bare text."""
    sources = {s["id"]: s for s in bundle["sources"]}
    ev = {e["id"]: e for e in bundle["evidence"]}
    return {"schema": "predictai.evidence_summary/1", "bundle_id": bundle["id"], "session_id": bundle["session_id"], "question": bundle["question"], "status": bundle["status"],
            "quality_verdict": bundle["quality"]["verdict"], "created_at": bundle["created_at"], "used_web": bundle["used_web"], "warnings": bundle["quality"]["warnings"],
            "conclusions": [{"id": c["id"], "text": c["text"], "statement_type": c["kind"], "statement_type_label": KIND_WORDS[c["kind"]], "confidence": c["confidence"],
                             "confidence_label": CONFIDENCE_WORDS[c["confidence"]], "caveats": c["caveats"], "origin": ev[c["evidence"][0]]["origin"] if c["evidence"] and c["evidence"][0] in ev else None,
                             "citations": [cite(ev[i], sources) for i in c["evidence"] if i in ev]} for c in bundle["conclusions"]],
            "conflicts": [{"id": c["id"], "explanation": c["explanation"], "statements": c["statements"]} for c in bundle["conflicts"]], "gaps": bundle["gaps"], "limitations": bundle["limitations"],
            "integrity": bundle["integrity"]["sha256"]}


# ------------------------------------------------------------------ exports
def to_markdown(bundle: Mapping[str, Any], explanation: Optional[Mapping[str, Any]] = None) -> str:
    src = {s["id"]: s for s in bundle["sources"]}
    ev = {e["id"]: e for e in bundle["evidence"]}
    out: List[str] = [f"# Research: {bundle['question']}", "",
                      f"Depth: {bundle['level_label']}. Run {bundle['number']} on {bundle['created_at']}. Result: {bundle['status']}. Quality check: {bundle['quality']['verdict']}.", "",
                      "> Every conclusion below is a quotation from a source, shown with where it came from. PredictAI has not checked that the sources are right.", ""]
    if explanation and explanation.get("status") == "available":
        out += ["## Explanation", "", explanation["summary"], ""] + [f"- {p}" for p in explanation.get("points", [])] + [""]
    if bundle["quality"]["warnings"]:
        out += ["## Warnings", ""] + [f"- {w}" for w in bundle["quality"]["warnings"]] + [""]
    out += ["## Conclusions", ""]
    if not bundle["conclusions"]:
        out += ["Nothing relevant was found to support a conclusion.", ""]
    for c in bundle["conclusions"]:
        out += [f"**{c['id']}.** \"{c['text']}\"", f"  - Type: {KIND_WORDS[c['kind']]}. Confidence: {CONFIDENCE_WORDS[c['confidence']]}."]
        out += [f"  - Caveat: {x}" for x in c["caveats"]]
        out += [f"  - Source: {cite(ev[i], src)}" for i in c["evidence"] if i in ev]
        out.append("")
    if bundle["conflicts"]:
        out += ["## Possible disagreements", ""]
        for c in bundle["conflicts"]:
            out += [f"**{c['id']}.** {c['explanation']}"] + [f"  - [{s['source']}] \"{s['text']}\"" for s in c["statements"]] + [f"  - {c['hint']}", ""]
    if bundle["gaps"]:
        out += ["## Not found", ""] + [f"- {g['title']}: {g['note']}" for g in bundle["gaps"]] + [""]
    out += ["## Sources", ""]
    for s in bundle["sources"]:
        line = f"- **{s['id']}** {s['title']} ({ORIGIN_WORDS.get(s['origin'], s['origin'])})"
        if s.get("url"):
            line += f" <{s['url']}>"
        if s.get("retrieved_at"):
            line += f", read {s['retrieved_at']}"
        if s.get("status") != "ok":
            line += f". NOT READ: {s.get('failure') or s.get('status')}"
        out.append(line)
        if s.get("reliability"):
            out.append(f"  - Reliability signals: {s['reliability']['level']}. " + "; ".join(f"{x['name']} ({x['effect']})" for x in s["reliability"]["signals"]))
    out += ["", "## Limitations", ""] + [f"- {x}" for x in bundle["limitations"]] + ["", f"Integrity (sha-256): {bundle['integrity']['sha256']}", ""]
    return "\n".join(out)


def evidence_csv(bundle: Mapping[str, Any]) -> str:
    src = {s["id"]: s for s in bundle["sources"]}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["evidence", "subtopic", "source", "source_title", "origin", "statement_type", "quote", "location", "url", "retrieved_at", "quote_sha256"])
    for e in bundle["evidence"]:
        s = src.get(e["source"], {})
        loc = e.get("location") or {}
        where = f"page {loc['page_start']}" if loc.get("page_start") else (loc.get("url") or "")
        w.writerow([csv_safe(x) for x in (e["id"], e["subtopic"], e["source"], s.get("title", ""), ORIGIN_WORDS.get(e["origin"], e["origin"]), e["kind"], e["quote"], where, s.get("url") or "", s.get("retrieved_at") or "", e["citation"].get("quote_sha256", ""))])
    return buf.getvalue()


def public_source(s: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in s.items() if k not in ("text", "key", "text_key")}                    # internal storage locations are not part of what people see or export
