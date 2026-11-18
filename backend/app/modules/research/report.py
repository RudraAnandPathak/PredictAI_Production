"""The structured answer: what a person reads first. Built from the finished evidence by fixed rules (no language model), so every line in it is a quotation or a calculated sentence with
the id of the passage it comes from.

  answer       the strongest line from each part of the question ("in short"), each labelled with what kind of evidence it is
  sections     one block per part of the plan: the conclusions found for it, or a plain statement that nothing was found
  dataset      what was calculated on the person's data (which runs, how many rows), and what could not be calculated
  external     which Wikipedia articles, arXiv preprints and web pages were used, labelled as such, and which could not be read
  comparison   calculated findings placed beside outside passages that discuss the same variables, with a plain note on how far the match goes
  uncertainty  everything the reader should weigh: single sources, preprints, an encyclopedia, models, samples, unread pages, gaps

Calculated and external statements are never merged into one sentence: the comparison lists both, side by side, and states that an outside source describes the world in general
while a calculation describes the person's file. An outside passage never changes a calculated number, and nothing here states a cause."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..textkit import stem, tokenize
from .sources import source_class, source_label
from .synthesis import build_explanation

SCHEMA = "predictai.research_report/1"
MAX_ANSWER = 6
MAX_COMPARE = 6
SEPARATION_NOTE = ("Calculated results describe your file and were computed by PredictAI. Outside sources describe what they say in general. They are shown side by side and never combined into one claim; "
                   "an association in your data is not proof that one thing causes another.")
DIRECTION = {"positive": "up", "higher": "up", "negative": "down", "lower": "down"}


def _var_tokens(name: str) -> List[str]:
    return [stem(t) for t in tokenize(str(name).replace("_", " "), stemming=False, drop_stopwords=True, min_len=2)]


def _variables(facts: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for k in ("x", "y", "column", "target", "measure", "group"):
        v = facts.get(k)
        if isinstance(v, str) and v not in out:
            out.append(v)
    return out


def _label(sources: Mapping[str, Mapping[str, Any]], sid: str) -> str:
    s = sources.get(sid) or {}
    return s.get("source_label") or source_label(s)


def build_comparison(evidence: Sequence[Mapping[str, Any]], sources: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    calc = [e for e in evidence if e.get("origin") == "calculated" and e.get("facts")]
    ext = [e for e in evidence if e.get("origin") == "external_research"]
    if not calc or not ext:
        return []
    out: List[Dict[str, Any]] = []
    for c in calc:
        names = [n for n in _variables(c["facts"]) if _var_tokens(n)]
        if not names:
            continue
        scored = []
        for e in ext:
            toks = set(e.get("tokens") or [])
            hit = [n for n in names if all(t in toks for t in _var_tokens(n))]
            if hit:
                scored.append((len(hit), e.get("score", 0), e, hit))
        if not scored:
            continue
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]["id"]))
        top = scored[:2]
        best = top[0]
        relation = "same_variables" if best[0] >= 2 else "same_subject"
        calc_dir = DIRECTION.get(str(c["facts"].get("direction") or ""))
        items = []
        for n, _, e, hit in top:
            flag = (e.get("flags") or {})
            ext_dir = None if flag.get("negated") else flag.get("direction")
            alignment = None
            if calc_dir and ext_dir and relation == "same_variables":
                alignment = "similar_wording" if calc_dir == ext_dir else "different_wording"
            items.append({"evidence": e["id"], "source": e["source"], "label": _label(sources, e["source"]), "text": e["quote"], "matched": hit, "alignment": alignment})
        note = ("The outside passage mentions the same variables as the calculated result. " if relation == "same_variables" else "The outside passage mentions one of the variables in the calculated result. ")
        aligns = {i["alignment"] for i in items if i["alignment"]}
        if aligns == {"similar_wording"}:
            note += "Judged only from its wording, it points the same way as your data. "
        elif aligns == {"different_wording"}:
            note += "Judged only from its wording, it appears to point the other way from your data, which is worth reading closely. "
        elif len(aligns) > 1:
            note += "The outside passages differ in direction from each other. "
        note += "The match is by words, not by meaning: read both passages before drawing a conclusion."
        out.append({"calculated": {"evidence": c["id"], "text": c["quote"], "dataset": (c.get("facts") or {}).get("dataset")}, "external": items, "relation": relation, "note": note})
        if len(out) >= MAX_COMPARE:
            break
    return out


def build_report(*, plan: Mapping[str, Any], sources: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]], conclusions: Sequence[Mapping[str, Any]], conflicts: Sequence[Mapping[str, Any]],
                 gaps: Sequence[Mapping[str, Any]], calculations: Sequence[Mapping[str, Any]], dataset_notes: Sequence[str], collection: Mapping[str, Any], web: Mapping[str, Any]) -> Dict[str, Any]:
    src = {s["id"]: s for s in sources}
    ev = {e["id"]: e for e in evidence}

    def item(c: Mapping[str, Any]) -> Dict[str, Any]:
        e0 = ev.get(c["evidence"][0]) if c.get("evidence") else None
        s0 = src.get(e0["source"]) if e0 else None
        return {"conclusion": c["id"], "text": c["text"], "evidence": list(c["evidence"]), "sources": list(c.get("sources") or []), "kind": c["kind"], "confidence": c["confidence"], "caveats": list(c.get("caveats") or []),
                "origin": e0["origin"] if e0 else None, "label": _label(src, e0["source"]) if e0 else None, "class": source_class(s0) if s0 else None,
                "location": (e0 or {}).get("location") or {}, "title": (s0 or {}).get("title")}

    sections: List[Dict[str, Any]] = []
    gap_of = {g["subtopic"]: g for g in gaps}
    for st in plan["subtopics"]:
        cs = [c for c in conclusions if c.get("subtopic") == st["id"]]
        sections.append({"subtopic": st["id"], "title": st["title"], "scope": st.get("scope", "general"), "intent": st.get("intent"), "status": "answered" if cs else "not_found",
                         "note": (gap_of.get(st["id"]) or {}).get("note"), "items": [item(c) for c in cs]})
    headline: List[Dict[str, Any]] = []
    for sec in sections:
        if sec["items"] and len(headline) < MAX_ANSWER:
            headline.append({**sec["items"][0], "subtopic": sec["subtopic"], "section": sec["title"]})
    dataset_scope = any(st.get("scope") == "dataset" for st in plan["subtopics"])
    external_ids = [s["id"] for s in sources if s.get("origin") == "external_research"]
    by_class: Dict[str, List[str]] = {}
    for sid in external_ids:
        by_class.setdefault(source_class(src[sid]), []).append(sid)
    unread = [{"source": sid, "title": src[sid].get("title"), "reason": src[sid].get("failure") or src[sid].get("status")} for sid in external_ids if src[sid].get("status") not in ("ok",)]
    datasets: Dict[str, Dict[str, Any]] = {}
    for c in calculations:
        d = datasets.setdefault(c["asset_id"], {"asset_id": c["asset_id"], "name": c["dataset"], "calculations": []})
        d["calculations"].append({k: c.get(k) for k in ("run_id", "kind", "operations", "rows_used", "evidence_status", "result_hash")})
    comparison = build_comparison(evidence, src)

    uncertainty: List[str] = []
    classes = {source_class(s) for s in sources}
    if "wikipedia" in classes:
        uncertainty.append("Wikipedia is an encyclopedia written by volunteers (a tertiary source). It is good for background and for finding primary sources, and it can be wrong or out of date.")
    if "arxiv_preprint" in classes:
        uncertainty.append("arXiv papers are preprints. Unless a paper says otherwise it has not been peer reviewed, and PredictAI has not checked whether any was published later.")
    partial = [s for s in sources if s.get("origin") == "external_research" and s.get("status") == "snippet_only"]
    if partial:
        uncertainty.append(f"{len(partial)} outside source(s) could only be read as the short summary a search service returned, not in full.")
    if unread:
        uncertainty.append(f"{len(unread)} outside source(s) could not be opened, so what they say is missing.")
    if "model" in classes:
        uncertainty.append("Model results are estimates learned from past data. They are not observed facts, and feature importance is not causation.")
    if dataset_scope:
        uncertainty.append("Relationships found in your data are associations. They do not show that one thing causes another.")
    if any("sample" in n.lower() for n in dataset_notes):
        uncertainty.append("A random sample of a large dataset was used for some calculations; each result states how many rows it used.")
    for g in gaps:
        uncertainty.append(f"{g['title']}: {g['note']}")
    for c in conclusions:
        if c["confidence"] in ("single_source", "uncertain_wording", "contested") and len(uncertainty) < 14:
            uncertainty.append(f"{c['id']}: " + ("; ".join(c.get("caveats") or []) or c["confidence"].replace("_", " ")))
    if conflicts:
        uncertainty.append(f"{len(conflicts)} possible disagreement(s) between sources are listed below the conclusions; they are not resolved.")
    if dataset_scope and external_ids and not comparison:
        uncertainty.append("No outside passage mentioned the same variables as the calculated findings, so this report cannot say whether outside sources agree or disagree with them.")
    uncertainty.extend(dataset_notes)
    seen = set()
    uncertainty = [u for u in uncertainty if not (u in seen or seen.add(u))]

    explanation = build_explanation(plan=plan, sources=sources, evidence=evidence, conclusions=conclusions, conflicts=conflicts, gaps=gaps, collection=collection, web=web)
    return {"schema": SCHEMA, "answer": headline, "sections": sections, "explanation": explanation,
            "providers": list(collection.get("providers") or []), "rejected": list(collection.get("dropped") or [])[:40],
            "dataset": {"present": dataset_scope, "datasets": list(datasets.values()), "notes": list(dataset_notes)},
            "external": {"present": bool(external_ids), "by_class": by_class, "requested": bool(web.get("requested")), "used": bool(web.get("used")), "unread": unread,
                         "searches": len(collection.get("searches") or [])},
            "comparison": comparison, "conflicts": [c["id"] for c in conflicts], "uncertainty": uncertainty,
            "separation_note": SEPARATION_NOTE if (dataset_scope and external_ids) else None,
            "reading_guide": "Each line shows what kind of evidence it is. Open its citation to see the exact passage, where it came from and when it was read."}
