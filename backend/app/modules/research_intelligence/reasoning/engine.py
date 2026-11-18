"""Cross-source reasoning over extracted facts. Pure functions: facts in, structured findings out, no database and no model.

Every item carries the exact quotes it rests on (source label, locator), so the reader can check it. The grouping is LEXICAL: two findings are "about the same thing" when they share enough
content words, not because a model understood them. That is stated in every result (`method`), contradictions are labelled "possible", and a difference in wording, scope or number is
reported as a variation, never as a contradiction. A person should read the quotes before relying on any of it."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence

from .facts import topic_terms

SAME_TOPIC = 0.40           # Jaccard of content-word stems for "about the same thing"
MIN_SHARED = 3              # ... and at least this many shared stems
MAGNITUDE_GAP = 0.5         # same direction, a shared percentage differing by more than this fraction -> "variation"
METHOD = "lexical grouping of exact sentences; no model understanding"


class Src:
    """One source as the engine sees it."""
    def __init__(self, label: str, row: Dict[str, Any], facts: Dict[str, Any]) -> None:
        self.label, self.row, self.facts = label, row, facts
        self.year = _year(row.get("published"))

    @property
    def short(self) -> str:
        a = row_authors(self.row)
        return f"{a[0].split()[-1] if a else 'Unknown'}{' et al.' if len(a) > 2 else (' & ' + a[1].split()[-1] if len(a) == 2 else '')}{f' ({self.year})' if self.year else ''}"


def row_authors(row: Dict[str, Any]) -> List[str]:
    import json
    try:
        v = json.loads(row.get("authors") or "[]")
        return [a for a in v if isinstance(a, str)]
    except (TypeError, ValueError):
        return []


def _year(published: Optional[str]) -> Optional[int]:
    m = re.match(r"(\d{4})", published or "")
    return int(m.group(1)) if m else None


def quote(src: Src, f: Dict[str, Any]) -> Dict[str, Any]:
    return {"label": src.label, "source_id": src.row["id"], "text": f["text"], "locator": f["locator"], "chunk_id": f["chunk_id"]}


def _sim(a: frozenset, b: frozenset) -> float:
    shared = len(a & b)
    if shared < MIN_SHARED:
        return 0.0
    return shared / len(a | b)


def _focus_ok(text_terms: frozenset, focus: Optional[frozenset]) -> bool:
    return focus is None or bool(text_terms & focus)


# --------------------------------------------------------------------------- grouping
def cluster_findings(sources: Sequence[Src], focus: Optional[str] = None) -> List[Dict[str, Any]]:
    """Group findings that are about the same thing, across sources. Single-link greedy grouping, deterministic order."""
    ft = topic_terms(focus) if focus else None
    items = []
    for s in sources:
        for f in s.facts["findings"]:
            t = topic_terms(f["text"])
            if len(t) >= MIN_SHARED and _focus_ok(t, ft):
                items.append((s, f, t))
    clusters: List[List[tuple]] = []
    for it in items:
        home = None
        for c in clusters:
            if any(_sim(it[2], m[2]) >= SAME_TOPIC for m in c):
                home = c
                break
        if home is not None:
            home.append(it)
        else:
            clusters.append([it])
    return [[{"src": s, "f": f, "terms": t} for s, f, t in c] for c in clusters]


def _mag_variation(members: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    nums = [(m["src"], m["f"], n) for m in members for n in m["f"].get("numbers", [])[:1]]
    if len({s.label for s, _, _ in nums}) < 2:
        return None
    lo, hi = min(nums, key=lambda t: t[2]), max(nums, key=lambda t: t[2])
    if hi[2] > 0 and (hi[2] - lo[2]) / hi[2] > MAGNITUDE_GAP and lo[0].label != hi[0].label:
        return {"low": {"label": lo[0].label, "value": lo[2]}, "high": {"label": hi[0].label, "value": hi[2]}}
    return None


def analyse_findings(sources: Sequence[Src], focus: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    common: List[Dict[str, Any]] = []
    contradictions: List[Dict[str, Any]] = []
    variations: List[Dict[str, Any]] = []
    for c in cluster_findings(sources, focus):
        labels = sorted({m["src"].label for m in c}, key=lambda x: int(x[1:]))
        if len(labels) < 2:
            continue
        by_pol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for m in c:
            if m["f"].get("polarity"):
                by_pol[m["f"]["polarity"]].append(m)
        pols = [p for p, ms in by_pol.items() if len({m["src"].label for m in ms}) >= 1]
        opposed = ("up" in by_pol and "down" in by_pol) or ("null" in by_pol and ("up" in by_pol or "down" in by_pol))
        topic = sorted(set.intersection(*[set(m["terms"]) for m in c]) or set.union(*[set(m["terms"]) for m in c]), key=lambda w: (-len(w), w))[:6]
        if opposed:
            sides = {p: [quote(m["src"], m["f"]) for m in ms][:3] for p, ms in by_pol.items()}
            if len({q["label"] for qs in sides.values() for q in qs}) >= 2:
                contradictions.append({"kind": "possible_contradiction", "topic_terms": topic, "sources": labels, "sides": sides,
                                       "note": "These sentences are about similar things but point in opposite directions. Check whether they concern the same population, setting and outcome before treating them as conflicting."})
            continue
        entry = {"topic_terms": topic, "sources": labels, "direction": pols[0] if len(pols) == 1 else None, "quotes": [quote(m["src"], m["f"]) for m in c][:6]}
        common.append(entry)
        var = _mag_variation(c)
        if var:
            variations.append({"kind": "magnitude_variation", "topic_terms": topic, "sources": labels, **var, "quotes": entry["quotes"][:4],
                               "note": "Same direction, different size. Often a difference in sample, measure or setting rather than a conflict."})
    common.sort(key=lambda e: (-len(e["sources"]), e["topic_terms"]))
    return {"common": common, "contradictions": contradictions, "variations": variations}


# --------------------------------------------------------------------------- methods
def compare_methods(sources: Sequence[Src]) -> Dict[str, Any]:
    rows = []
    for s in sources:
        d = s.facts["design"]
        rows.append({"label": s.label, "source_id": s.row["id"], "title": s.row["title"], "year": s.year, "design": d["label"], "design_key": d["key"], "design_evidence": d["evidence"],
                     "sample_size": s.facts.get("sample_size"), "access": s.row["access"], "methods": [quote(s, m) for m in s.facts["methods"][:2]]})
    notes: List[str] = []
    designs = Counter(r["design"] for r in rows)
    if len(designs) > 1:
        notes.append("The sources use different designs (" + ", ".join(f"{n}× {d.lower()}" for d, n in designs.most_common()) + "), so their results are not equally comparable.")
    sizes = [r["sample_size"] for r in rows if r["sample_size"]]
    if len(sizes) >= 2 and max(sizes) / max(1, min(sizes)) >= 10:
        notes.append(f"Sample sizes differ by about {round(max(sizes) / min(sizes))}× ({min(sizes):,} to {max(sizes):,}).")
    unknown = [r["label"] for r in rows if r["design_key"] == "unknown"]
    if unknown:
        notes.append(f"No study design could be identified for {', '.join(unknown)}.")
    return {"rows": rows, "notes": notes, "method": METHOD}


# --------------------------------------------------------------------------- gaps
def find_gaps(sources: Sequence[Src], focus: Optional[str] = None) -> Dict[str, Any]:
    stated: List[Dict[str, Any]] = []
    for s in sources:
        for lim in s.facts["limitations"][:3]:
            stated.append({**quote(s, lim), "kind": "future_work" if re.search(r"future|further|warrants?", lim["text"], re.I) else "limitation"})
    recurring: List[Dict[str, Any]] = []
    groups: List[List[Dict[str, Any]]] = []
    for q in stated:
        t = topic_terms(q["text"])
        for g in groups:
            if _sim(t, topic_terms(g[0]["text"])) >= 0.3:
                g.append(q)
                break
        else:
            groups.append([q])
    for g in groups:
        if len({q["label"] for q in g}) >= 2:
            recurring.append({"sources": sorted({q["label"] for q in g}, key=lambda x: int(x[1:])), "quotes": g[:4]})
    coverage: List[Dict[str, Any]] = []
    present = {s.facts["design"]["key"] for s in sources}
    if not present & {"rct", "meta_analysis"}:
        coverage.append({"kind": "no_strong_design", "text": "None of these sources reports a randomised trial or a systematic review, so stronger evidence of effect may exist elsewhere."})
    abstract_only = [s.label for s in sources if s.row["access"] != "full_text"]
    if abstract_only:
        coverage.append({"kind": "limited_access", "text": f"{len(abstract_only)} source(s) ({', '.join(abstract_only)}) are available as abstract or metadata only, so their methods and limitations could not be read."})
    kw = Counter(k.lower() for s in sources for k in set(x.lower() for x in s.facts.get("keywords", [])))
    thin = sorted(k for k, n in kw.items() if n == 1 and len(k) > 3)[:8]
    if thin and len(sources) >= 3:
        coverage.append({"kind": "thin_topics", "text": "Topics that only one source covers: " + ", ".join(thin) + ". They may deserve more sources.", "topics": thin})
    years = [s.year for s in sources if s.year]
    if years and len(sources) >= 3 and max(years) < _this_year() - 5:
        coverage.append({"kind": "dated", "text": f"The newest source is from {max(years)}; more recent work is not represented."})
    return {"stated_by_authors": stated, "recurring": recurring, "coverage": coverage, "method": METHOD + "; coverage notes are counted from the selected sources only"}


def _this_year() -> int:
    import datetime
    return datetime.date.today().year


# --------------------------------------------------------------------------- evidence
def grade_sources(sources: Sequence[Src]) -> List[Dict[str, Any]]:
    out = []
    for s in sources:
        d = s.facts["design"]
        reasons = [f"Design: {d['label'].lower()}" + (f" (found “{d['evidence']}”)" if d["evidence"] else "")]
        grade = d["grade"]
        n = s.facts.get("sample_size")
        if n:
            reasons.append(f"Sample size mentioned: {n:,}")
            if n < 30 and grade in ("strong", "moderate"):
                grade, reasons = "limited", reasons + ["Downgraded: very small sample"]
        if s.row["access"] != "full_text":
            reasons.append("Only the abstract or metadata was available, so confidence is lower")
            if grade == "strong":
                grade = "moderate"
        out.append({"label": s.label, "source_id": s.row["id"], "grade": grade, "rank": d["rank"], "reasons": reasons})
    return out


def grade_themes(common: Sequence[Dict[str, Any]], grades: Sequence[Dict[str, Any]], contradictions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    g = {x["label"]: x for x in grades}
    disputed = {l for c in contradictions for l in c["sources"]}
    out = []
    for i, e in enumerate(common):
        strong = [l for l in e["sources"] if g[l]["grade"] in ("strong", "moderate")]
        if len(e["sources"]) >= 3 and len(strong) >= 2 and not (set(e["sources"]) & disputed):
            level = "higher"
        elif len(e["sources"]) >= 2 and strong:
            level = "moderate"
        else:
            level = "lower"
        out.append({"theme_index": i, "sources": e["sources"], "confidence": level,
                    "reason": f"{len(e['sources'])} sources agree; {len(strong)} of them use a strong or moderate design" + ("; some of these sources also appear in a possible contradiction" if set(e["sources"]) & disputed else "")})
    return out


# --------------------------------------------------------------------------- timeline
def timeline(sources: Sequence[Src]) -> Dict[str, Any]:
    dated, undated = [], []
    for s in sources:
        top = s.facts["findings"][0] if s.facts["findings"] else None
        e = {"label": s.label, "source_id": s.row["id"], "title": s.row["title"], "year": s.year, "published": s.row.get("published"), "design": s.facts["design"]["label"],
             "highlight": quote(s, top) if top else None}
        (dated if s.year else undated).append(e)
    dated.sort(key=lambda e: (e["year"], e["label"]))
    years: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for e in dated:
        years[e["year"]].append(e)
    return {"years": [{"year": y, "sources": v} for y, v in sorted(years.items())], "undated": undated,
            "span": [dated[0]["year"], dated[-1]["year"]] if dated else None}


# --------------------------------------------------------------------------- synthesis
def synthesise(sources: Sequence[Src], findings: Dict[str, Any], themes_conf: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Three levels. 1: what each source found (its own sentences). 2: what several sources share or dispute (quotes from each). 3: counts about the whole set (computed, not claimed)."""
    per_source = []
    for s in sources:
        top = s.facts["findings"][:2]
        per_source.append({"label": s.label, "source_id": s.row["id"], "citation": s.short, "title": s.row["title"], "findings": [quote(s, f) for f in top]})
    themes = []
    for i, e in enumerate(findings["common"][:10]):
        conf = next((t for t in themes_conf if t["theme_index"] == i), None)
        themes.append({"kind": "agreement", "sources": e["sources"], "quotes": e["quotes"][:4], "confidence": conf["confidence"] if conf else "lower"})
    for c in findings["contradictions"][:5]:
        themes.append({"kind": "possible_contradiction", "sources": c["sources"], "quotes": [q for qs in c["sides"].values() for q in qs][:4], "confidence": None})
    with_findings = sum(1 for s in sources if s.facts["findings"])
    overview = {"sources": len(sources), "with_findings": with_findings, "agreements": len(findings["common"]), "possible_contradictions": len(findings["contradictions"]),
                "magnitude_variations": len(findings["variations"]),
                "statement": (f"Computed from {len(sources)} sources: {len(findings['common'])} point(s) several sources share, {len(findings['contradictions'])} possible contradiction(s), "
                              f"{len(findings['variations'])} difference(s) in size. {len(sources) - with_findings} source(s) had no extractable findings.")}
    return {"overview": overview, "themes": themes, "per_source": per_source, "narrative": None}
