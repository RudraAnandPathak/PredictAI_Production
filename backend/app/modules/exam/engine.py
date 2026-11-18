"""Exam Intelligence engine: pure functions, no database. Question extraction, curriculum reading, mapping, and the historical-frequency / relative-emphasis / heuristic-score calculation.

Wording is deliberate. A topic that appeared often in past papers has a high *historical frequency*. That is a count, not a probability that it will appear again, and nothing here is
presented as a prediction. The score below is a *heuristic*: a transparent weighted blend, shown with its parts."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

HEURISTIC_VERSION = "emphasis_heuristic_v1"
WEIGHTS = {"marks_share": 0.4, "recurrence": 0.3, "count_share": 0.2, "recency": 0.1}
NOTICE = ("Historical frequency counts how often a topic appeared in the papers you supplied. It is not the probability that the topic will appear in a future paper, and the heuristic score "
          "is a relative ranking of those past papers, not a forecast.")
STOP = set("the a an and or of to in on for with by from is are was were be been this that these those which what when where why how who whom whose it its as at into than then there their "
           "about above below between after before during each other such any all both can could may might must shall should will would explain describe discuss define state write give "
           "following question answer marks mark short long notes chapter unit topic section part".split())

_QSTART = re.compile(r"^\s*(?:Q(?:uestion)?\.?\s*)?(\d{1,3})\s*[.):]\s+(?=\S)", re.I)
_OPTION = re.compile(r"^\s*\(?([A-Da-d])[.)]\s+(.+)$")
_MARKS = [re.compile(r"[\[(]\s*(\d{1,3}(?:\.\d)?)\s*(?:marks?|mks?|m)\s*[\])]", re.I), re.compile(r"\b(\d{1,3}(?:\.\d)?)\s*marks?\b", re.I), re.compile(r"[\[(]\s*(\d{1,3})\s*[\])]\s*$")]
_ROMAN_SUB = re.compile(r"^\s*\(?(?:[ivx]+|[a-z])\)\s+", re.I)


def tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z\-]{3,}", text.lower()) if w not in STOP]


def _marks(block: str) -> Optional[float]:
    for pat in _MARKS:
        m = pat.search(block)
        if m:
            v = float(m.group(1))
            if 0 < v <= 100:
                return v
    return None


def parse_questions(pages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split paper text into questions. Never edits wording: `text` is the extracted text as read. Each question gets a confidence and the warnings that justify it."""
    lines: List[Tuple[int, str, Optional[float]]] = []
    for pg in pages:
        for ln in str(pg.get("text") or "").splitlines():
            if ln.strip():
                lines.append((pg["number"], ln.rstrip(), pg.get("ocr_confidence")))
    blocks: List[Dict[str, Any]] = []
    for page, ln, conf in lines:
        m = _QSTART.match(ln)
        if m:
            blocks.append({"number": m.group(1), "page": page, "lines": [ln[m.end():].strip()], "ocr": [conf] if conf is not None else []})
        elif blocks:
            blocks[-1]["lines"].append(ln.strip())
            if conf is not None:
                blocks[-1]["ocr"].append(conf)
    out = []
    for b in blocks:
        body = [x for x in b["lines"]]
        opts = []
        stem = []
        for x in body:
            m = _OPTION.match(x)
            if m and (stem or opts):
                opts.append({"label": m.group(1).upper(), "text": m.group(2).strip()})
            else:
                stem.append(x)
        text = " ".join(stem).strip()
        full = " ".join(body)
        marks = _marks(full)
        text = re.sub(r"\s+", " ", text)
        warnings: List[str] = []
        low = text.lower()
        if len(opts) >= 2:
            qtype = "mcq"
        elif "true or false" in low or "true/false" in low or re.search(r"\bstate whether\b.*\b(true|false)\b", low):
            qtype = "true_false"
        elif "____" in text or "……" in text or "..........." in text:
            qtype = "fill_blank"
        elif marks is not None:
            qtype = "short" if marks <= 4 else "long"
        else:
            qtype = "unknown"
            warnings.append("Question type could not be decided: no options, blank or marks were found.")
        conf = 0.92
        if marks is None:
            conf -= 0.12
            warnings.append("No marks were found for this question.")
        if len(text) < 15:
            conf -= 0.4
            warnings.append("The question text is very short; the extraction may be incomplete.")
        if qtype == "mcq" and len(opts) < 3:
            conf -= 0.1
            warnings.append("Fewer than three options were found.")
        if b["ocr"]:
            ocr = sum(b["ocr"]) / len(b["ocr"])
            if ocr < 85:
                conf -= min(0.4, (85 - ocr) / 100)
                warnings.append(f"Read by OCR with mean confidence {ocr:.0f}%; check the wording against the original.")
        out.append({"number": b["number"], "page": b["page"], "text": text, "qtype": qtype, "marks": marks, "options": opts or None, "confidence": round(max(0.05, conf), 2), "warnings": warnings})
    seen: Dict[str, str] = {}
    for q in out:
        key = re.sub(r"\W+", " ", q["text"].lower()).strip()
        if key in seen:
            q["warnings"].append(f"Same wording as question {seen[key]} in this paper.")
        seen.setdefault(key, q["number"])
    return out


_CHAP = re.compile(r"^\s*(?:chapter|unit|module|section)\s*(\d+)\s*[:.\-–—]?\s*(.+)$", re.I)
_SUB = re.compile(r"^\s*(?:[-•*·]|\d+\.\d+[.)]?|\([a-z0-9]\)|[a-z][.)])\s*(.+)$", re.I)


def parse_curriculum(pages: Sequence[Dict[str, Any]], headings: Sequence[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    chapter: Optional[str] = None
    for pg in pages:
        for ln in str(pg.get("text") or "").splitlines():
            if not ln.strip():
                continue
            m = _CHAP.match(ln)
            if m:
                chapter = f"{m.group(1)}. {m.group(2).strip()}"[:120]
                rows.append({"chapter": chapter, "topic": None, "subtopic": None, "page": pg["number"]})
                continue
            s = _SUB.match(ln)
            if s and chapter and len(s.group(1).strip()) > 2:
                for part in re.split(r"\s*[;,]\s*", s.group(1).strip()) if len(s.group(1)) < 160 and ";" in s.group(1) else [s.group(1).strip()]:
                    if part:
                        rows.append({"chapter": chapter, "topic": part[:140], "subtopic": None, "page": pg["number"]})
    if not rows:                                                           # no "Chapter n" lines: fall back to the document's own headings
        cur = None
        for h in headings:
            lvl = h.get("level", 1)
            if lvl <= 1 or cur is None:
                cur = str(h.get("text") or "")[:120]
                if cur:
                    rows.append({"chapter": cur, "topic": None, "subtopic": None, "page": h.get("page")})
            else:
                rows.append({"chapter": cur, "topic": str(h.get("text") or "")[:140], "subtopic": None, "page": h.get("page")})
    for r in rows:
        r["keywords"] = sorted(set(tokens(" ".join(x for x in (r["chapter"], r["topic"]) if x))))
    return rows


def map_question(text: str, curriculum: Sequence[Dict[str, Any]], threshold: float = 0.2) -> Dict[str, Any]:
    """Keyword overlap against the curriculum. Returns the best chapter/topic with a confidence, or an unmapped result: a weak match is reported as weak, never forced."""
    q = set(tokens(text))
    if not q or not curriculum:
        return {"curriculum_id": None, "chapter": None, "topic": None, "confidence": 0.0, "reason": "No usable words or no curriculum to match against."}
    best, best_score = None, 0.0
    for row in curriculum:
        kws = set(row["keywords"])
        if not kws:
            continue
        hit = q & kws
        if not hit:
            continue
        score = len(hit) / min(len(kws), 6) * 0.7 + len(hit) / max(len(q), 1) * 0.3
        if row["topic"]:
            score += 0.05
        if score > best_score:
            best, best_score = row, score
    if best is None or best_score < threshold:
        return {"curriculum_id": None, "chapter": None, "topic": None, "confidence": round(best_score, 2), "reason": "No chapter shares enough words with the question."}
    return {"curriculum_id": best["id"], "chapter": best["chapter"], "topic": best["topic"], "confidence": round(min(0.95, best_score), 2), "reason": "Shared words: " + ", ".join(sorted(q & set(best["keywords"]))[:6])}


def analyse(questions: Sequence[Dict[str, Any]], curriculum_chapters: Sequence[str], papers: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """`questions` carry the *current* mapping (chapter, topic) and their paper's year. Returns per-chapter figures and the heuristic emphasis score with its parts."""
    n_papers = max(1, len(papers))
    years = sorted({p["year"] for p in papers if p.get("year")})
    latest = max(years) if years else None
    by: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"questions": 0, "marks": 0.0, "papers": set(), "years": set(), "topics": defaultdict(int), "unmarked": 0})
    total_q, total_marks = 0, 0.0
    for q in questions:
        ch = q.get("chapter") or "(unmapped)"
        b = by[ch]
        b["questions"] += 1
        total_q += 1
        if q.get("marks"):
            b["marks"] += q["marks"]
            total_marks += q["marks"]
        else:
            b["unmarked"] += 1
        b["papers"].add(q["document_id"])
        if q.get("year"):
            b["years"].add(q["year"])
        if q.get("topic"):
            b["topics"][q["topic"]] += 1
    rows = []
    for ch in sorted(set(by) | set(curriculum_chapters)):
        b = by.get(ch)
        if b is None:
            rows.append({"chapter": ch, "questions": 0, "marks": 0.0, "marks_share": 0.0, "papers_with_topic": 0, "recurrence": 0.0, "years": [], "last_year": None, "topics": {}, "components": {}, "score": 0.0, "unmarked_questions": 0})
            continue
        marks_share = b["marks"] / total_marks if total_marks else 0.0
        count_share = b["questions"] / total_q if total_q else 0.0
        recurrence = len(b["papers"]) / n_papers
        recency = 0.0
        if latest and b["years"]:
            recency = max(0.0, 1 - (latest - max(b["years"])) / max(1, (latest - min(years)) or 1)) if len(years) > 1 else 1.0
        comp = {"marks_share": round(marks_share, 4), "recurrence": round(recurrence, 4), "count_share": round(count_share, 4), "recency": round(recency, 4)}
        rows.append({"chapter": ch, "questions": b["questions"], "marks": round(b["marks"], 2), "marks_share": round(marks_share, 4), "papers_with_topic": len(b["papers"]), "recurrence": round(recurrence, 4),
                     "years": sorted(b["years"]), "last_year": max(b["years"]) if b["years"] else None, "topics": dict(b["topics"]), "components": comp, "unmarked_questions": b["unmarked"], "score": 0.0})
    peak = {k: max([r["components"].get(k, 0) for r in rows] or [0]) or 1 for k in WEIGHTS}
    for r in rows:
        if r["components"]:
            r["score"] = round(100 * sum(WEIGHTS[k] * (r["components"][k] / peak[k]) for k in WEIGHTS), 1)
    ranked = sorted([r for r in rows if r["questions"]], key=lambda r: (-r["score"], r["chapter"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return {"heuristic": HEURISTIC_VERSION, "weights": WEIGHTS, "notice": NOTICE, "papers": len(papers), "years": years, "questions": total_q, "marks_total": round(total_marks, 2),
            "chapters": rows, "never_asked": [r["chapter"] for r in rows if not r["questions"] and r["chapter"] != "(unmapped)"],
            "unmapped_questions": by["(unmapped)"]["questions"] if "(unmapped)" in by else 0, "labels": {"questions": "historical frequency", "score": "heuristic score", "marks_share": "relative emphasis"}}
