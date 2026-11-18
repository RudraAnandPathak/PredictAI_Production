"""Studio generators: research questions and hypotheses from a finished analysis.

These are rule-based and EXTRACTIVE in their evidence: every suggestion states the pattern that triggered it and carries the exact quotes it rests on. The wording is a fixed template
filled with terms and labels taken from the analysis; no model writes them and no fact is added. They are prompts for the researcher, not results: each item is labelled `suggestion` and
says what it has NOT been checked against (the wider literature)."""
from __future__ import annotations

import hashlib

from ..reports.builder import live_contradictions
from typing import Any, Dict, Iterable, List, Optional, Set

NOTE = "Suggested from patterns in the selected sources only. It has not been checked against the wider literature and is not a validated research question or hypothesis."
DIRECTION = {"down": "a decrease", "up": "an increase", "null": "no clear effect"}


def _id(kind: str, text: str) -> str:
    return kind[:2] + hashlib.sha1(text.encode()).hexdigest()[:10]


def _terms(entry: Dict[str, Any]) -> str:
    t = entry.get("topic_terms") or []
    return ", ".join(t[:4]) if t else "the shared topic"


def _keep(result: Dict[str, Any], keep: Optional[Iterable[str]]) -> Set[str]:
    all_l = {s["label"] for s in result["sources"]}
    return all_l if keep is None else {l for l in keep if l in all_l}


def _qs(quotes: Iterable[Dict[str, Any]], keep: Set[str], n: int = 4) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for q in quotes:
        if q["label"] in keep and q["text"] not in seen:
            seen.add(q["text"])
            out.append(q)
        if len(out) >= n:
            break
    return out


def research_questions(result: Dict[str, Any], keep: Optional[Iterable[str]] = None, limit: int = 12) -> List[Dict[str, Any]]:
    k = _keep(result, keep)
    f, g = result["findings"], result["gaps"]
    items: List[Dict[str, Any]] = []

    def add(kind: str, trigger: str, text: str, basis: List[Dict[str, Any]]) -> None:
        if basis:
            items.append({"id": _id(kind, text), "kind": kind, "trigger": trigger, "text": text, "basis": basis, "note": NOTE})

    for e in live_contradictions(result, k):
        ls = e["sources"]
        if len(ls) >= 2:
            add("contradiction", "Sources point in opposite directions on the same topic.", f"What explains the different results on {_terms(e)} reported by {', '.join(ls)}? Do they differ in population, setting, design or measurement?",
                _qs((q for side in e["sides"].values() for q in side), k, 6))
    for v in f["variations"]:
        ls = [l for l in v["sources"] if l in k]
        if len(ls) >= 2:
            add("variation", "Sources agree on direction but report different sizes.", f"Why does the size of the effect on {_terms(v)} differ between {v['low']['label']} ({v['low']['value']:g}%) and {v['high']['label']} ({v['high']['value']:g}%)?", _qs(v["quotes"], k))
    for q in g["stated_by_authors"]:
        if q["label"] in k and q["kind"] == "future_work":
            add("author_need", "An author states that further work is needed.", f"Which study would address the need stated in {q['label']}: “{q['text'][:160]}{'…' if len(q['text']) > 160 else ''}”?", [q])
    strong = {r["design_key"] for r in result["methods"]["rows"] if r["label"] in k} & {"rct", "meta_analysis"}
    for e in f["common"][:3]:
        ls = [l for l in e["sources"] if l in k]
        if len(ls) >= 2 and not strong:
            add("design", "Several sources agree, but none uses a randomised or review design.", f"Does the shared finding about {_terms(e)} ({', '.join(ls)}) still hold in a randomised or systematic-review design?", _qs(e["quotes"], k))
    for c in g["coverage"]:
        if c["kind"] == "thin_topics":
            for t in c.get("topics", [])[:2]:
                basis = [q for s in result["synthesis"]["per_source"] if s["label"] in k for q in s["findings"][:1]][:1]
                add("coverage", "A topic appears in only one source.", f"How does “{t}” relate to the main themes of these sources? Only one source covers it.", basis)
    seen, out = set(), []
    for i in items:
        if i["id"] not in seen:
            seen.add(i["id"])
            out.append(i)
    return out[:limit]


def hypotheses(result: Dict[str, Any], keep: Optional[Iterable[str]] = None, limit: int = 10) -> List[Dict[str, Any]]:
    k = _keep(result, keep)
    f = result["findings"]
    rows = {r["label"]: r for r in result["methods"]["rows"]}
    conf = {t["theme_index"]: t["confidence"] for t in result["evidence"]["themes"]}
    items: List[Dict[str, Any]] = []

    def add(kind: str, statement: str, test: str, basis: List[Dict[str, Any]], strength: Optional[str]) -> None:
        if basis:
            items.append({"id": _id(kind, statement), "kind": kind, "statement": statement, "how_to_test": test, "basis": basis, "evidence_reading": strength, "status": "untested suggestion", "note": NOTE})

    for i, e in enumerate(f["common"]):
        ls = [l for l in e["sources"] if l in k]
        if len(ls) >= 2 and e.get("direction") in DIRECTION:
            add("generalisation", f"The pattern on {_terms(e)} ({DIRECTION[e['direction']]}) reported by {', '.join(ls)} also appears in settings that none of these sources studied.",
                "Replicate in a new sample or setting. The hypothesis is weakened if the same direction is not found.", _qs(e["quotes"], k), conf.get(i))
    for e in live_contradictions(result, k):
        ls = e["sources"]
        designs = {rows[l]["design"] for l in ls if l in rows and rows[l]["design_key"] != "unknown"}
        how = ("Compare the studies under one design: they currently use " + " and ".join(sorted(d.lower() for d in designs)) + ".") if len(designs) > 1 else "Compare population, setting and outcome measure between the sources, then test the one that differs."
        add("moderator", f"The difference between {', '.join(ls)} on {_terms(e)} is explained by a difference in design, population or setting rather than by a real conflict.", how,
            _qs((q for side in e["sides"].values() for q in side), k, 6), None)
    for v in f["variations"]:
        ls = [l for l in v["sources"] if l in k]
        if len(ls) >= 2:
            add("magnitude", f"The size of the effect on {_terms(v)} depends on context: it is larger in {v['high']['label']} than in {v['low']['label']} for a reason that can be identified.",
                "Record the context variables of both studies (sample, measure, setting) and test whether they predict the size.", _qs(v["quotes"], k), None)
    seen, out = set(), []
    for i in items:
        if i["id"] not in seen:
            seen.add(i["id"])
            out.append(i)
    return out[:limit]
