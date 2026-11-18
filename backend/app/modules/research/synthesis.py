"""A reading order for the evidence: the part of the report a person reads as an explanation.

Nothing here is written by a language model, and nothing here is invented. Every statement is a quotation that already passed the evidence checks, kept next to the citation that proves
it. What this module adds is ORDER and HONEST LABELS:

  direct_answer   the strongest statement for each part of the question, in the order the plan asked it
  sections        per part: what the person's own document says, what outside sources say (kept apart), in time order when the statements carry years, with a label for how well each is
                  supported (several sources / one source / hedged) and a citation that names the page or the article section
  connections     how statements relate, but only relations that can be checked from the text itself: two sources saying the same thing, the same named event or person appearing in two parts,
                  and causes that a source itself states with a causal word. Each connection is labelled as PredictAI's reading, not as a source's claim
  unknown         what the research did not find, which providers failed or were left out, and how many results were rejected as not about the question

For a question about causes, statements are also split into long-term conditions and immediate triggers using only the words of the statement itself."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..providers.verify import CAUSAL
from . import relevance as rel
from .sources import source_class, source_label

SCHEMA = "predictai.explanation/1"
IMMEDIATE = re.compile(r"\b(immediate(?:ly)?|triggered?|precipitated|sparked|set off|final straw|proximate|directly led)\b", re.I)
YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
NAMED = re.compile(r"\b(?:[A-Z][a-zA-Z]+(?:\s+(?:of|the|de|du)\s+)?(?:\s+[A-Z][a-zA-Z]+)+|[A-Z][a-z]{3,})\b")
SKIP_NAMES = frozenset("The This That These Those When While After Before During Although However Louis France French Revolution King Third First Second".split())


def _where(src: Mapping[str, Any], ev: Mapping[str, Any]) -> str:
    loc = ev.get("location") or {}
    title = src.get("title") or "source"
    if src.get("origin") == "user_provided" or src.get("kind") == "document":
        a, b = loc.get("page_start"), loc.get("page_end")
        page = f", p. {a}" if a and (not b or a == b) else f", pp. {a}–{b}" if a and b else ""
        sec = f" › {loc['section']}" if loc.get("section") else ""
        return f"{title}{page}{sec}"
    sec = f" › {loc['section']}" if loc.get("section") and loc["section"] != "Introduction (lead)" else " (introduction)" if loc.get("section") else ""
    return f"{title}{sec}"


def _year(text: str) -> Optional[int]:
    m = YEAR.search(text)
    return int(m.group(1)) if m else None


def _names(text: str, skip: Sequence[str] = ()) -> set:
    out = set()
    skip_l = [x.lower() for x in skip if x]
    for m in NAMED.finditer(text):
        w = m.group(0).strip()
        first = w.split()[0]
        if first in SKIP_NAMES and len(w.split()) == 1:
            continue
        if w in SKIP_NAMES or any(k in w.lower() or w.lower() in k for k in skip_l):
            continue
        out.add(w)
    return out


def build_explanation(*, plan: Mapping[str, Any], sources: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]], conclusions: Sequence[Mapping[str, Any]], conflicts: Sequence[Mapping[str, Any]],
                      gaps: Sequence[Mapping[str, Any]], collection: Mapping[str, Any], web: Mapping[str, Any]) -> Dict[str, Any]:
    src = {s["id"]: s for s in sources}
    ev = {e["id"]: e for e in evidence}
    u = plan.get("understanding") or {}
    task = u.get("task") or plan.get("task") or "overview"
    contested = {i for c in conflicts for s in c["statements"] for i in [s["evidence"]]}
    sections: List[Dict[str, Any]] = []
    placed: Dict[str, List[Dict[str, Any]]] = defaultdict(list)                       # statements by part, for the connections

    for st in plan["subtopics"]:
        cs = [c for c in conclusions if c.get("subtopic") == st["id"]]
        statements: List[Dict[str, Any]] = []
        for c in cs:
            e0 = ev[c["evidence"][0]]
            s0 = src[e0["source"]]
            origin = "your_document" if e0["origin"] == "user_provided" else "calculated" if e0["origin"] in ("calculated", "model_prediction") else "outside"
            n_src = len(c.get("sources") or [])
            support = "hedged" if c["kind"] in ("uncertain", "opinion", "prediction") else "several_sources" if n_src >= 2 else "one_source"
            cites = [{"evidence": i, "source": ev[i]["source"], "where": _where(src[ev[i]["source"]], ev[i])} for i in c["evidence"][:3] if i in ev]
            statements.append({"text": c["text"], "conclusion": c["id"], "evidence": list(c["evidence"]), "sources": list(c.get("sources") or []), "origin": origin, "support": support,
                               "kind": c["kind"], "cites": cites, "contested": any(i in contested for i in c["evidence"]), "context": (e0.get("context") or None),
                               "timing": ("immediate" if IMMEDIATE.search(c["text"]) else "long_term") if task in ("causes",) else None, "year": _year(c["text"]),
                               "source_class": source_class(s0)})
        if sum(1 for s in statements if s["year"]) * 2 >= len(statements) > 1:
            statements.sort(key=lambda s: (s["year"] is None, s["year"] or 0))
        by_origin = {k: [s for s in statements if s["origin"] == k] for k in ("your_document", "outside", "calculated")}
        sections.append({"subtopic": st["id"], "title": st["title"], "status": "answered" if statements else "not_found", "note": next((g["note"] for g in gaps if g["subtopic"] == st["id"]), None),
                         "statements": statements, "from_your_document": by_origin["your_document"], "from_outside_sources": by_origin["outside"], "calculated": by_origin["calculated"],
                         "agreement": ("Your document and outside sources both address this part." if by_origin["your_document"] and by_origin["outside"] else
                                       "Only your document addresses this part." if by_origin["your_document"] else "Only outside sources address this part." if by_origin["outside"] else None)})
        placed[st["id"]] = statements

    # ---- the direct answer: the best statement of every answered part, in plan order, without repeating one sentence
    direct: List[Dict[str, Any]] = []
    used_text = set()
    for sec in sections:
        vocab = rel.subtopic_vocab(next(st for st in plan["subtopics"] if st["id"] == sec["subtopic"]))
        pool = sorted(sec["statements"], key=lambda s: (-len(rel._stems(s["text"]) & vocab), s["support"] != "several_sources", s["support"] == "hedged", s["contested"]))
        for s in pool:
            if s["text"] not in used_text:
                used_text.add(s["text"])
                direct.append({"part": sec["title"], "text": s["text"], "evidence": s["evidence"], "support": s["support"], "cites": s["cites"][:2]})
                break

    # ---- connections that can be checked from the text
    connections: List[Dict[str, Any]] = []
    for sec in sections:
        for s in sec["statements"]:
            if s["support"] == "several_sources" and len(s["sources"]) >= 2 and not s["contested"]:
                connections.append({"kind": "agreement", "text": f"{len(s['sources'])} sources state this in nearly the same words: “{_short(s['text'])}”", "evidence": s["evidence"], "part": sec["title"], "label": "predictai_reading"})
                break
    skip_names = [e.get("name", "") for e in (u.get("entities") or [])] + [a for e in (u.get("entities") or []) for a in e.get("aliases") or []]
    names_by_part: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for pid_, stmts in placed.items():
        d: Dict[str, Dict[str, Any]] = {}
        for s in stmts:
            for n in _names(s["text"], skip_names):
                d.setdefault(n, s)
        names_by_part[pid_] = d
    titles = {sec["subtopic"]: sec["title"] for sec in sections}
    seen_pairs = set()
    ids = [i for i in names_by_part if names_by_part[i]]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            for name in sorted(set(names_by_part[a]) & set(names_by_part[b])):
                if (name in seen_pairs) or len(connections) >= 10:
                    continue
                seen_pairs.add(name)
                sa, sb = names_by_part[a][name], names_by_part[b][name]
                connections.append({"kind": "shared_subject", "text": f"“{name}” appears under both “{titles[a]}” and “{titles[b]}”, so these two parts of the answer touch the same subject: “{_short(sa['text'])}” and “{_short(sb['text'])}”.",
                                    "evidence": sa["evidence"][:1] + sb["evidence"][:1], "label": "predictai_reading"})
    for sec in sections:
        for s in sec["statements"]:
            if CAUSAL.search(s["text"]) and len(connections) < 14:
                connections.append({"kind": "stated_cause", "text": f"A source states this link itself: “{_short(s['text'])}”", "evidence": s["evidence"], "part": sec["title"], "label": "source_statement"})

    # ---- causes: long-term versus immediate, only from each statement's own words
    timing = None
    if task == "causes":
        timing = {"long_term": [s["conclusion"] for sec in sections for s in sec["statements"] if s["timing"] == "long_term"],
                  "immediate": [s["conclusion"] for sec in sections for s in sec["statements"] if s["timing"] == "immediate"],
                  "note": "A statement is listed as an immediate trigger only if its own words say so (for example “immediate cause”, “triggered”, “sparked”). Everything else is listed as a long-term condition; "
                          "the sources do not always make this distinction."}

    # ---- a comparison keeps the compared things apart and puts them side by side
    side_by_side = None
    if task == "comparison":
        side_by_side = []
        for st in plan["subtopics"]:
            if st.get("entity") in (None, "*"):
                continue
            e_ = next((e for e in (u.get("entities") or []) if e.get("key") == st["entity"]), {})
            stm = placed.get(st["id"], [])
            years = sorted({int(y) for s_ in stm for y in YEAR.findall(s_["text"]) if int(y) >= 1000})
            side_by_side.append({"entity": e_.get("name") or st["title"], "key": st["entity"], "years_mentioned": years[:12], "period": (f"{years[0]}–{years[-1]}" if len(years) >= 2 else str(years[0]) if years else None),
                                 "statements": [{"text": s_["text"], "evidence": s_["evidence"], "cites": s_["cites"][:2]} for s_ in stm[:6]]})
        shared = next((sec for sec in sections if sec["title"] == "Differences and similarities"), None)
        if shared is not None and not shared["statements"]:
            shared["note"] = ("No source in this research states a comparison of them directly. Each is described separately above; the years are the ones the sources themselves give. "
                              "Any difference you draw between them is your own reading.")

    # ---- what is not known
    unknown: List[str] = []
    for g in gaps:
        unknown.append(f"{g['title']}: {g['note']}")
    providers = collection.get("providers") or []
    for p in providers:
        if p["status"] == "failed":
            unknown.append(f"{p['provider']} could not be used ({p['note']}). Nothing from it is included, and this research does not claim it was searched.")
        elif p["status"] == "partial":
            unknown.append(f"{p['provider']} failed for some searches ({p['note']}).")
        elif p["status"] in ("no_results", "no_relevant_results"):
            unknown.append(f"{p['provider']}: {p['note']}")
        elif p["status"] == "skipped":
            unknown.append(f"{p['provider']} was not searched: {p['note']}")
    rejected = collection.get("dropped") or []
    if rejected:
        unknown.append(f"{len(rejected)} search result(s) were set aside because they were not about the question (listed with reasons under “Rejected results”).")
    answered = [sec for sec in sections if sec["statements"]]
    if answered and not any(s["support"] == "several_sources" for sec in answered for s in sec["statements"]):
        unknown.append("No statement is counted as confirmed by several sources: that needs two sources saying the same thing in nearly the same words. Related statements from different sources "
                       "(for example your document and an encyclopedia) are shown side by side instead, and each is labelled as resting on one source.")
    if web.get("requested") and not web.get("used"):
        unknown.append("Web research was requested but was not run, so only your own material is used.")
    if not any(sec["statements"] for sec in sections):
        unknown.insert(0, "No appropriate source was found for this question, so no answer is given. Nothing irrelevant was substituted.")

    return {"schema": SCHEMA, "task": task, "subject": [e["name"] for e in (u.get("entities") or []) if e.get("role") == "subject"],
            "how_to_read": ("Each statement is a quotation from a source, shown with where it is. “PredictAI's reading” marks the only lines PredictAI added: how statements line up. "
                            "Your document and outside sources are kept apart, and a statement that only one source makes is labelled as such."),
            "direct_answer": direct, "side_by_side": side_by_side, "sections": sections, "connections": connections, "timing": timing, "unknown": unknown}


def _short(text: str, n: int = 160) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"
