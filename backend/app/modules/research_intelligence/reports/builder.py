"""Build report sections from a finished analysis (R5). Pure functions: analysis result in, structured sections out.

Honesty rules, enforced here and tested:
 * Every block has an `origin`: `source` (an exact quote of a stored passage, with its citation), `analysis` (computed by this tool from the quotes, e.g. counts and groupings) or
   `author` (written or edited by the person). Nothing is presented as a source finding unless it is an exact quote.
 * References are built only from the metadata the sources really have. A missing author, year or venue is left out, never invented.
 * The report carries a fixed notice that it is automatically generated from the selected sources and has not been peer reviewed or academically validated.
 * Sections that need interpretation (introduction beyond scope, discussion, conclusion) contain only computed statements plus an empty author block for the person's own text."""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Set

NOTICE = ("This report was generated automatically by PredictAI from the sources the author selected. Quotations are exact text from those sources; everything else was computed by "
          "pattern-based text analysis or written by the author, and is labelled as such. It has not been peer reviewed or academically validated, and groupings, possible contradictions and "
          "evidence grades are reading aids, not conclusions. Check every point against the cited source before relying on it.")

TYPES = {
    "research_summary": ("Research summary", ["abstract", "findings", "contradictions", "gaps", "references"]),
    "literature_review": ("Literature review", ["introduction", "background", "literature_review", "contradictions", "gaps", "conclusion", "references"]),
    "comparative": ("Comparative research report", ["abstract", "methodology", "findings", "contradictions", "discussion", "references"]),
    "gap_analysis": ("Research gap analysis", ["abstract", "gaps", "methodology", "discussion", "references"]),
    "methodology_comparison": ("Methodology comparison", ["abstract", "methodology", "discussion", "references"]),
    "evidence_synthesis": ("Evidence synthesis", ["abstract", "findings", "evidence", "contradictions", "conclusion", "references"]),
    "academic": ("Academic research report", ["abstract", "introduction", "background", "literature_review", "methodology", "findings", "evidence", "contradictions", "gaps", "discussion", "conclusion", "references"]),
}
HEADINGS = {"abstract": "Abstract", "introduction": "Introduction", "background": "Background", "literature_review": "Literature review", "methodology": "Methodology comparison",
            "findings": "Findings", "evidence": "Evidence strength", "contradictions": "Possible contradictions", "gaps": "Research gaps", "discussion": "Discussion",
            "conclusion": "Conclusion", "references": "References"}


class Ctx:
    """Working state while building: which labels are kept, a block-id counter per section."""

    def __init__(self, result: Dict[str, Any], keep: Optional[Set[str]]) -> None:
        self.r = result
        self.cards = {s["label"]: s for s in result["sources"]}
        self.keep = set(self.cards) if keep is None else {l for l in keep if l in self.cards}
        self.n: Dict[str, int] = {}

    def bid(self, section: str) -> str:
        self.n[section] = self.n.get(section, 0) + 1
        return f"{section}-{self.n[section]}"

    def text(self, section: str, text: str, origin: str = "analysis", cite: Iterable[str] = ()) -> Dict[str, Any]:
        return {"id": self.bid(section), "type": "text", "origin": origin, "text": text, "cite": [c for c in cite if c in self.keep]}

    def quote(self, section: str, q: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": self.bid(section), "type": "quote", "origin": "source", "text": q["text"], "cite": [q["label"]], "ref": {"label": q["label"], "source_id": q["source_id"], "chunk_id": q["chunk_id"], "locator": q["locator"]}}

    def bullets(self, section: str, items: List[str], origin: str = "analysis") -> Dict[str, Any]:
        return {"id": self.bid(section), "type": "bullets", "origin": origin, "items": items, "cite": []}

    def table(self, section: str, columns: List[str], rows: List[List[str]], cite: Iterable[str] = ()) -> Dict[str, Any]:
        return {"id": self.bid(section), "type": "table", "origin": "analysis", "columns": columns, "rows": rows, "cite": [c for c in cite if c in self.keep]}

    def author(self, section: str, hint: str) -> Dict[str, Any]:
        return {"id": self.bid(section), "type": "text", "origin": "author", "text": "", "hint": hint, "cite": []}

    def quotes_kept(self, qs: Iterable[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        out, seen = [], set()
        for q in qs:
            if q["label"] in self.keep and q["text"] not in seen:
                seen.add(q["text"])
                out.append(q)
            if len(out) >= limit:
                break
        return out


def live_contradictions(result: Dict[str, Any], keep: Set[str]) -> List[Dict[str, Any]]:
    """Contradictions that still exist among the kept sources: at least two opposing sides must each keep a quotation. Dropping a source can remove one side."""
    out = []
    for e in result["findings"]["contradictions"]:
        sides = {p: [q for q in qs if q["label"] in keep] for p, qs in e["sides"].items()}
        sides = {p: qs for p, qs in sides.items() if qs}
        if len(sides) >= 2:
            out.append({**e, "sides": sides, "sources": sorted({q["label"] for qs in sides.values() for q in qs}, key=lambda x: int(x[1:]))})
    return out


def _terms(entry: Dict[str, Any]) -> str:
    t = entry.get("topic_terms") or []
    return ", ".join(t[:4]) if t else "a shared topic"


def _labels(ls: Iterable[str], keep: Set[str]) -> List[str]:
    return [l for l in ls if l in keep]


def _sec(key: str, blocks: List[Dict[str, Any]], heading: Optional[str] = None) -> Dict[str, Any]:
    return {"id": key, "heading": heading or HEADINGS[key], "kind": "generated", "blocks": blocks}


def _abstract(c: Ctx) -> Dict[str, Any]:
    r, k = c.r, c.keep
    f = r["findings"]
    agree = [e for e in f["common"] if len(_labels(e["sources"], k)) >= 2]
    contra = live_contradictions(r, k)
    s = [f"This report compares {len(k)} source(s)" + (f" on the question: {r['question']}" if r.get("question") else "") + ".",
         f"The automatic analysis found {len(agree)} point(s) that several sources share and {len(contra)} possible contradiction(s)."]
    designs = sorted({row["design"] for row in r["methods"]["rows"] if row["label"] in k and row["design_key"] != "unknown"})
    if designs:
        s.append("Study designs identified: " + "; ".join(d.lower() for d in designs) + ".")
    return _sec("abstract", [c.text("abstract", " ".join(s), "analysis", sorted(k, key=_num))])


def _num(label: str) -> int:
    return int(label[1:])


def _introduction(c: Ctx) -> Dict[str, Any]:
    r, k = c.r, c.keep
    span = r["timeline"]["span"]
    yrs = f" published between {span[0]} and {span[1]}" if span and span[0] != span[1] else (f" published in {span[0]}" if span else "")
    b = [c.text("introduction", f"The author selected {len(k)} source(s){yrs} from a research workspace" + (f" to examine: {r['question']}" if r.get("question") else " for comparison") + ".", "analysis", sorted(k, key=_num)),
         c.author("introduction", "State the research problem and why it matters, in your own words.")]
    return _sec("introduction", b)


def _background(c: Ctx) -> Dict[str, Any]:
    items = []
    for s in sorted((c.cards[l] for l in c.keep), key=lambda s: _num(s["label"])):
        d = next((row for row in c.r["methods"]["rows"] if row["label"] == s["label"]), {})
        bits = [f"{s['citation']}: {s['title']}"]
        if d.get("design_key") not in (None, "unknown"):
            bits.append(f"design: {d['design'].lower()}")
        if s["access"] != "full_text":
            bits.append("abstract or metadata only")
        items.append("[" + s["label"] + "] " + "; ".join(bits))
    return _sec("background", [c.bullets("background", items)])


def _themes(c: Ctx, section: str) -> List[Dict[str, Any]]:
    conf = {t["theme_index"]: t["confidence"] for t in c.r["evidence"]["themes"]}
    blocks: List[Dict[str, Any]] = []
    for i, e in enumerate(c.r["findings"]["common"]):
        ls = _labels(e["sources"], c.keep)
        if len(ls) < 1:
            continue
        qs = c.quotes_kept(e["quotes"], 4)
        if not qs:
            continue
        head = (f"{len(ls)} sources ({', '.join(ls)}) report related findings about {_terms(e)}" if len(ls) > 1 else f"{ls[0]} reports a finding about {_terms(e)}") + (f" (confidence reading aid: {conf[i]})." if i in conf and len(ls) > 1 else ".")
        blocks.append(c.text(section, head, "analysis", ls))
        blocks += [c.quote(section, q) for q in qs]
    for v in c.r["findings"]["variations"]:
        ls = _labels(v["sources"], c.keep)
        if len(ls) >= 2:
            blocks.append(c.text(section, f"Same direction, different size on {_terms(v)}: {v['low']['label']} reports {v['low']['value']:g}% and {v['high']['label']} reports {v['high']['value']:g}%. {v['note']}", "analysis", ls))
    if not blocks:
        blocks.append(c.text(section, "No findings shared by two or more of the selected sources could be extracted automatically.", "analysis"))
    return blocks


def _per_source(c: Ctx, section: str) -> List[Dict[str, Any]]:
    """What each source says on its own: its first extracted findings, as exact quotes."""
    b: List[Dict[str, Any]] = []
    for p in sorted((p for p in c.r["synthesis"]["per_source"] if p["label"] in c.keep), key=lambda p: _num(p["label"])):
        if p["findings"]:
            b.append(c.text(section, f"{p['citation']} reports:", "analysis", [p["label"]]))
            b += [c.quote(section, q) for q in p["findings"]]
    return b


def _findings(c: Ctx) -> Dict[str, Any]:
    themes = _themes(c, "findings")
    head = [c.text("findings", "Findings by source (exact quotations):", "analysis")] if themes else []
    return _sec("findings", themes + head + _per_source(c, "findings") + [c.author("findings", "Add your own reading of these findings.")])


def _literature_review(c: Ctx) -> Dict[str, Any]:
    b: List[Dict[str, Any]] = []
    for y in c.r["timeline"]["years"]:
        for s in y["sources"]:
            if s["label"] not in c.keep:
                continue
            b.append(c.text("literature_review", f"{c.cards[s['label']]['citation']}: {s['title']}", "analysis", [s["label"]]))
            if s.get("highlight") and s["highlight"]["label"] in c.keep:
                b.append(c.quote("literature_review", s["highlight"]))
    for s in c.r["timeline"]["undated"]:
        if s["label"] in c.keep:
            b.append(c.text("literature_review", f"{c.cards[s['label']]['citation']}: {s['title']} (no publication date recorded)", "analysis", [s["label"]]))
            if s.get("highlight") and s["highlight"]["label"] in c.keep:
                b.append(c.quote("literature_review", s["highlight"]))
    b += _themes(c, "literature_review")
    b.append(c.author("literature_review", "Synthesise the literature in your own words."))
    return _sec("literature_review", b)


def _methodology(c: Ctx) -> Dict[str, Any]:
    rows = [row for row in c.r["methods"]["rows"] if row["label"] in c.keep]
    table = c.table("methodology", ["Source", "Design", "Sample size", "Access", "Year"],
                    [[f"{r['label']} {c.cards[r['label']]['citation']}", r["design"], f"{r['sample_size']:,}" if r.get("sample_size") else "not stated", r["access"].replace("_", " "), str(r["year"] or "n/d")] for r in rows],
                    [r["label"] for r in rows])
    b: List[Dict[str, Any]] = [table]
    notes = [n for n in c.r["methods"]["notes"]]
    if notes:
        b.append(c.bullets("methodology", notes))
    for r in rows:
        for m in r["methods"][:1]:
            if m["label"] in c.keep:
                b.append(c.quote("methodology", m))
    return _sec("methodology", b)


def _evidence(c: Ctx) -> Dict[str, Any]:
    rows = [[f"{g['label']} {c.cards[g['label']]['citation']}", g["grade"], "; ".join(g["reasons"])] for g in c.r["evidence"]["sources"] if g["label"] in c.keep]
    return _sec("evidence", [c.table("evidence", ["Source", "Grade", "Why"], rows, [r[0].split()[0] for r in rows]), c.text("evidence", c.r["evidence"]["note"], "analysis")])


def _contradictions(c: Ctx) -> Dict[str, Any]:
    b: List[Dict[str, Any]] = []
    for e in live_contradictions(c.r, c.keep):
        ls = e["sources"]
        qs = c.quotes_kept([q for side in e["sides"].values() for q in side], 6)
        b.append(c.text("contradictions", f"Possible contradiction about {_terms(e)} between {', '.join(ls)}. {e['note']}", "analysis", ls))
        b += [c.quote("contradictions", q) for q in qs]
    if not b:
        b.append(c.text("contradictions", "No possible contradictions were detected among the selected sources. This does not show that the sources agree.", "analysis"))
    return _sec("contradictions", b)


def _gaps(c: Ctx) -> Dict[str, Any]:
    g = c.r["gaps"]
    b: List[Dict[str, Any]] = []
    for r in g["recurring"]:
        ls = _labels(r["sources"], c.keep)
        if len(ls) >= 2:
            b.append(c.text("gaps", f"A limitation or need for further work is stated by {', '.join(ls)}:", "analysis", ls))
            b += [c.quote("gaps", q) for q in c.quotes_kept(r["quotes"], 3)]
    singles = [q for q in g["stated_by_authors"] if q["label"] in c.keep][:6]
    if singles:
        b.append(c.text("gaps", "Limitations and future work stated by the authors of individual sources:", "analysis"))
        b += [c.quote("gaps", q) for q in singles]
    cov = [x["text"] for x in g["coverage"]]
    if cov:
        b.append(c.text("gaps", "Coverage observations about this set of sources (computed):", "analysis"))
        b.append(c.bullets("gaps", cov))
    if not b:
        b.append(c.text("gaps", "No limitations or gaps could be extracted automatically.", "analysis"))
    return _sec("gaps", b)


def _discussion(c: Ctx) -> Dict[str, Any]:
    o = c.r["synthesis"]["overview"]
    return _sec("discussion", [c.text("discussion", o["statement"] + " Interpretation of these results is left to the author.", "analysis"), c.author("discussion", "Discuss what the findings mean, where the sources agree or differ, and why.")])


def _conclusion(c: Ctx) -> Dict[str, Any]:
    f = c.r["findings"]
    agree = sum(1 for e in f["common"] if len(_labels(e["sources"], c.keep)) >= 2)
    contra = len(live_contradictions(c.r, c.keep))
    return _sec("conclusion", [c.text("conclusion", f"Across {len(c.keep)} source(s), {agree} shared point(s) and {contra} possible contradiction(s) were found by automatic analysis. These are starting points for reading, not conclusions about the topic.", "analysis"),
                               c.author("conclusion", "State your conclusions and next steps.")])


def reference_text(card: Dict[str, Any]) -> str:
    """APA-like reference from the fields that exist. Nothing is invented: a missing author, year or venue is simply absent."""
    authors = card.get("authors") or []
    def fmt(a: str) -> str:
        parts = a.replace(",", " ").split()
        return f"{parts[-1]}, {' '.join(p[0] + '.' for p in parts[:-1])}".strip() if len(parts) > 1 else a
    names = [fmt(a) for a in authors[:6]]
    who = (", ".join(names[:-1]) + ", & " + names[-1] if len(names) > 1 else names[0]) if names else ""
    s = (who + " " if who else "") + (f"({card['year']}). " if card.get("year") else ("(n.d.). " if who else "")) + card["title"].rstrip(".") + "."
    if card.get("venue"):
        s += f" {card['venue']}."
    if card.get("doi"):
        s += f" https://doi.org/{card['doi']}"
    return re.sub(r"\s+", " ", s).strip()


def references(c: Ctx) -> List[Dict[str, Any]]:
    return [{"label": l, "source_id": c.cards[l]["source_id"], "text": reference_text(c.cards[l]), "doi": c.cards[l].get("doi"), "access": c.cards[l]["access"]} for l in sorted(c.keep, key=_num)]


def _references(c: Ctx) -> Dict[str, Any]:
    return _sec("references", [c.bullets("references", [f"[{r['label']}] {r['text']}" for r in references(c)])])


BUILD = {"abstract": _abstract, "introduction": _introduction, "background": _background, "literature_review": _literature_review, "methodology": _methodology, "findings": _findings,
         "evidence": _evidence, "contradictions": _contradictions, "gaps": _gaps, "discussion": _discussion, "conclusion": _conclusion, "references": _references}


def build(report_type: str, result: Dict[str, Any], keep_labels: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """-> {sections, sources, notice}. `keep_labels` restricts the report to some of the analysis's sources; themes and contradictions that no longer involve enough of them are dropped."""
    if report_type not in TYPES:
        raise ValueError("unknown report type")
    c = Ctx(result, set(keep_labels) if keep_labels is not None else None)
    if len(c.keep) < 1:
        raise ValueError("no sources selected")
    sections = [BUILD[k](c) for k in TYPES[report_type][1]]
    return {"sections": sections, "sources": references(c), "notice": NOTICE}


def regenerate_merge(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep what the person wrote: author sections, author text in matching blocks, and blocks they added. Everything else comes from the fresh build."""
    old_by = {s["id"]: s for s in old}
    out = []
    for s in new:
        o = old_by.get(s["id"])
        if o is None:
            out.append(s)
            continue
        ob = {b["id"]: b for b in o["blocks"]}
        merged = []
        for b in s["blocks"]:
            prev = ob.get(b["id"])
            merged.append(copy.deepcopy(prev) if prev is not None and prev.get("origin") == "author" and prev.get("type") in ("text", "bullets") else b)
        known = {b["id"] for b in s["blocks"]}
        merged += [copy.deepcopy(b) for b in o["blocks"] if b["id"] not in known and b.get("origin") == "author"]
        out.append({**s, "heading": o["heading"] if o.get("heading") != HEADINGS.get(s["id"]) else s["heading"], "blocks": merged})
    present = {s["id"] for s in out}
    for o in old:
        if o.get("kind") == "author" and o["id"] not in present:
            out.append(copy.deepcopy(o))
    return out
