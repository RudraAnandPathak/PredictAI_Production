"""Structured Studio outputs: flashcards, quiz, mind map, data table, slide deck, infographic and audio-overview script.

The model is asked for JSON in a fixed shape. Everything it returns is checked here: wrong types are dropped, text is trimmed to limits, source marks that do not exist are removed,
and an item with no valid source mark is kept only if the kind allows it (it is then counted as unchecked). No database, no network."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

TEXT_MAX = 500
LABEL_RE = re.compile(r"^S\d{1,3}$")

JSON_RULE = ("Reply with one JSON object and nothing else (no Markdown fence). Every item has a \"sources\" list naming the source ids it comes from, like [\"S2\"]. Use only facts the sources state.")

SPECS: Dict[str, Dict[str, Any]] = {
    "flashcards": {"title": "Flashcards", "blurb": "Question-and-answer cards to study from.",
                   "ask": "Make 10 to 25 study flashcards. Shape: {\"cards\": [{\"front\": \"question or term\", \"back\": \"answer\", \"sources\": [\"S1\"]}]}"},
    "quiz": {"title": "Quiz", "blurb": "Multiple-choice questions with the answer explained.",
             "ask": "Make 5 to 15 multiple-choice questions, each with exactly 4 options and one correct answer. Shape: {\"questions\": [{\"question\": \"...\", \"options\": [\"a\", \"b\", \"c\", \"d\"], "
                    "\"answer\": 0, \"explanation\": \"why the answer is right\", \"sources\": [\"S1\"]}]} where answer is the 0-based index of the correct option."},
    "mind_map": {"title": "Mind map", "blurb": "The main themes and how they branch.",
                 "ask": "Make a mind map of the main themes, at most 3 levels below the root and at most 60 nodes. Shape: {\"root\": \"central topic\", \"children\": [{\"label\": \"theme\", "
                        "\"sources\": [\"S1\"], \"children\": [{\"label\": \"sub-theme\", \"sources\": [\"S2\"], \"children\": []}]}]}"},
    "data_table": {"title": "Data table", "blurb": "Facts from the sources laid out in rows and columns.",
                   "ask": "Make a table of comparable facts from the sources (2 to 8 columns, up to 40 rows). Shape: {\"title\": \"...\", \"columns\": [\"col 1\", \"col 2\"], "
                          "\"rows\": [{\"cells\": [\"value 1\", \"value 2\"], \"sources\": [\"S1\"]}]}. Leave a cell as \"\" when the sources do not give it."},
    "slides": {"title": "Slide deck", "blurb": "A short presentation you can download.",
               "ask": "Make a slide deck of 5 to 12 slides. Shape: {\"title\": \"deck title\", \"slides\": [{\"title\": \"slide title\", \"bullets\": [\"point\", \"point\"], \"notes\": \"speaker notes\", "
                      "\"sources\": [\"S1\"]}]}. At most 6 short bullets per slide."},
    "infographic": {"title": "Infographic", "blurb": "Headline numbers and key points on one page.",
                    "ask": "Make an infographic. Shape: {\"title\": \"...\", \"subtitle\": \"...\", \"stats\": [{\"value\": \"a number or short figure\", \"label\": \"what it measures\", \"sources\": [\"S1\"]}], "
                           "\"points\": [{\"text\": \"key point\", \"sources\": [\"S2\"]}]}. 0 to 6 stats (only figures the sources state), 3 to 8 points."},
    "audio_script": {"title": "Audio overview", "blurb": "A two-host conversation about the sources, read aloud by your browser.",
                     "ask": "Write a lively but accurate two-host podcast conversation about the sources, 12 to 40 turns. Host A introduces and asks; host B explains. Shape: {\"title\": \"...\", "
                            "\"lines\": [{\"speaker\": \"A\", \"text\": \"...\", \"sources\": [\"S1\"]}]}. Greetings and questions may have an empty sources list; every factual line needs one."},
}


def _s(v: Any, n: int = TEXT_MAX) -> str:
    return " ".join(v.split())[:n] if isinstance(v, str) else ""


class Checker:
    def __init__(self, labels: Sequence[str]) -> None:
        self.valid, self.used, self.dropped_marks, self.uncited, self.dropped_items = set(labels), [], 0, 0, 0

    def marks(self, v: Any, *, need: bool = False) -> Optional[List[str]]:
        raw = v if isinstance(v, list) else []
        out: List[str] = []
        for x in raw:
            if isinstance(x, str) and LABEL_RE.match(x.strip()) and x.strip() in self.valid:
                lab = x.strip()
                if lab not in out:
                    out.append(lab)
                if lab not in self.used:
                    self.used.append(lab)
            else:
                self.dropped_marks += 1
        if not out:
            if need:
                self.dropped_items += 1
                return None
            self.uncited += 1
        return out


def parse_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    if not t.startswith("{"):
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            return None
        t = t[a:b + 1]
    try:
        out = json.loads(t)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _flashcards(d: Dict[str, Any], c: Checker) -> Tuple[Dict[str, Any], int]:
    cards = []
    for x in (d.get("cards") or [])[:40]:
        if not isinstance(x, dict) or not _s(x.get("front")) or not _s(x.get("back")):
            c.dropped_items += 1
            continue
        src = c.marks(x.get("sources"), need=True)
        if src is not None:
            cards.append({"front": _s(x["front"], 300), "back": _s(x["back"]), "sources": src})
    return {"cards": cards}, len(cards)


def _quiz(d: Dict[str, Any], c: Checker) -> Tuple[Dict[str, Any], int]:
    qs = []
    for x in (d.get("questions") or [])[:25]:
        opts = x.get("options") if isinstance(x, dict) else None
        ans = x.get("answer") if isinstance(x, dict) else None
        if (not isinstance(x, dict) or not _s(x.get("question")) or not isinstance(opts, list) or len(opts) != 4 or not all(_s(o, 200) for o in opts)
                or not isinstance(ans, int) or isinstance(ans, bool) or not 0 <= ans < 4 or len({_s(o, 200).lower() for o in opts}) != 4):
            c.dropped_items += 1
            continue
        src = c.marks(x.get("sources"), need=True)
        if src is not None:
            qs.append({"question": _s(x["question"]), "options": [_s(o, 200) for o in opts], "answer": ans, "explanation": _s(x.get("explanation")), "sources": src})
    return {"questions": qs}, len(qs)


def _mind_map(d: Dict[str, Any], c: Checker) -> Tuple[Dict[str, Any], int]:
    count = [0]

    def node(x: Any, depth: int) -> Optional[Dict[str, Any]]:
        if not isinstance(x, dict) or not _s(x.get("label"), 120) or count[0] >= 80:
            return None
        count[0] += 1
        kids = [n for n in (node(k, depth + 1) for k in (x.get("children") or [])[:12]) if n] if depth < 3 else []
        return {"label": _s(x["label"], 120), "sources": c.marks(x.get("sources")) or [], "children": kids}
    root = _s(d.get("root"), 120)
    kids = [n for n in (node(k, 1) for k in (d.get("children") or [])[:12]) if n]
    return {"root": root or "Sources", "children": kids}, count[0]


def _data_table(d: Dict[str, Any], c: Checker) -> Tuple[Dict[str, Any], int]:
    cols = [_s(x, 80) for x in (d.get("columns") or [])[:8] if _s(x, 80)]
    rows = []
    if 2 <= len(cols):
        for r in (d.get("rows") or [])[:50]:
            cells = r.get("cells") if isinstance(r, dict) else None
            if not isinstance(cells, list) or len(cells) != len(cols):
                c.dropped_items += 1
                continue
            src = c.marks(r.get("sources"), need=True)
            if src is not None:
                rows.append({"cells": [_s(x, 200) if isinstance(x, str) else (str(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else "") for x in cells], "sources": src})
    return {"title": _s(d.get("title"), 160), "columns": cols if len(cols) >= 2 else [], "rows": rows}, len(rows)


def _slides(d: Dict[str, Any], c: Checker) -> Tuple[Dict[str, Any], int]:
    out = []
    for x in (d.get("slides") or [])[:15]:
        if not isinstance(x, dict) or not _s(x.get("title"), 120):
            c.dropped_items += 1
            continue
        bullets = [_s(b, 200) for b in (x.get("bullets") or [])[:6] if _s(b, 200)]
        out.append({"title": _s(x["title"], 120), "bullets": bullets, "notes": _s(x.get("notes"), 800), "sources": c.marks(x.get("sources")) or []})
    return {"title": _s(d.get("title"), 160), "slides": out}, len(out)


def _infographic(d: Dict[str, Any], c: Checker) -> Tuple[Dict[str, Any], int]:
    stats = []
    for x in (d.get("stats") or [])[:6]:
        if isinstance(x, dict) and _s(x.get("value"), 40) and _s(x.get("label"), 120):
            src = c.marks(x.get("sources"), need=True)                        # a number with no source is not shown
            if src is not None:
                stats.append({"value": _s(x["value"], 40), "label": _s(x["label"], 120), "sources": src})
        else:
            c.dropped_items += 1
    points = []
    for x in (d.get("points") or [])[:8]:
        if isinstance(x, dict) and _s(x.get("text"), 300):
            points.append({"text": _s(x["text"], 300), "sources": c.marks(x.get("sources")) or []})
    return {"title": _s(d.get("title"), 160), "subtitle": _s(d.get("subtitle"), 200), "stats": stats, "points": points}, len(stats) + len(points)


def _audio(d: Dict[str, Any], c: Checker) -> Tuple[Dict[str, Any], int]:
    lines = []
    for x in (d.get("lines") or [])[:60]:
        if not isinstance(x, dict) or x.get("speaker") not in ("A", "B") or not _s(x.get("text"), 600):
            c.dropped_items += 1
            continue
        lines.append({"speaker": x["speaker"], "text": _s(x["text"], 600), "sources": c.marks(x.get("sources")) or []})
    return {"title": _s(d.get("title"), 160), "lines": lines}, len(lines)


VALIDATORS: Dict[str, Callable[[Dict[str, Any], Checker], Tuple[Dict[str, Any], int]]] = {
    "flashcards": _flashcards, "quiz": _quiz, "mind_map": _mind_map, "data_table": _data_table, "slides": _slides, "infographic": _infographic, "audio_script": _audio}
MINIMUM = {"flashcards": 3, "quiz": 2, "mind_map": 2, "data_table": 1, "slides": 2, "infographic": 2, "audio_script": 4}


def validate(kind: str, text: str, labels: Sequence[str]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """(data or None, report). None means nothing usable came back; the report says why."""
    d = parse_json(text)
    if d is None:
        return None, {"problem": "The AI's reply was not the JSON that was asked for."}
    c = Checker(labels)
    data, n = VALIDATORS[kind](d, c)
    report = {"items": n, "dropped_items": c.dropped_items, "dropped_marks": c.dropped_marks, "uncited_items": c.uncited, "used": c.used}
    if n < MINIMUM[kind]:
        return None, {**report, "problem": f"Only {n} usable item(s) came back (at least {MINIMUM[kind]} needed); items without a valid source mark are not kept."}
    return data, report


# ------------------------------------------------------------------ Markdown copies (shown in lists, exported, searchable)
def _m(src: Sequence[str]) -> str:
    return (" " + " ".join(f"[{s}]" for s in src)) if src else ""


def to_markdown(kind: str, d: Dict[str, Any]) -> str:
    L: List[str] = []
    if kind == "flashcards":
        for i, x in enumerate(d["cards"], 1):
            L += [f"**{i}. {x['front']}**", f"- {x['back']}{_m(x['sources'])}"]
    elif kind == "quiz":
        for i, x in enumerate(d["questions"], 1):
            L.append(f"**{i}. {x['question']}**")
            L += [f"- {'ABCD'[j]}. {o}" for j, o in enumerate(x["options"])]
            L.append(f"- Answer: {'ABCD'[x['answer']]}. {x['explanation']}{_m(x['sources'])}")
    elif kind == "mind_map":
        L.append(f"## {d['root']}")

        def walk(nodes: Sequence[Dict[str, Any]], depth: int) -> None:
            for n in nodes:
                L.append("  " * depth + f"- {n['label']}{_m(n['sources'])}")
                walk(n["children"], depth + 1)
        walk(d["children"], 0)
    elif kind == "data_table":
        if d["title"]:
            L.append(f"## {d['title']}")
        L += ["| " + " | ".join(d["columns"]) + " | Sources |", "|" + " --- |" * (len(d["columns"]) + 1)]
        L += ["| " + " | ".join(c.replace("|", "/") for c in r["cells"]) + f" |{_m(r['sources'])} |" for r in d["rows"]]
    elif kind == "slides":
        L.append(f"# {d['title'] or 'Slides'}")
        for i, s in enumerate(d["slides"], 1):
            L.append(f"## {i}. {s['title']}{_m(s['sources'])}")
            L += [f"- {b}" for b in s["bullets"]]
    elif kind == "infographic":
        L.append(f"# {d['title'] or 'Infographic'}")
        if d["subtitle"]:
            L.append(d["subtitle"])
        L += [f"- **{s['value']}** {s['label']}{_m(s['sources'])}" for s in d["stats"]]
        L += [f"- {p['text']}{_m(p['sources'])}" for p in d["points"]]
    elif kind == "audio_script":
        L.append(f"# {d['title'] or 'Audio overview'}")
        L += [f"**Host {x['speaker']}:** {x['text']}{_m(x['sources'])}" for x in d["lines"]]
    return "\n".join(L)


# ------------------------------------------------------------------ files
def to_csv(kind: str, d: Dict[str, Any]) -> Optional[str]:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    if kind == "data_table":
        w.writerow(d["columns"] + ["sources"])
        for r in d["rows"]:
            w.writerow([_safe_cell(c) for c in r["cells"]] + [" ".join(r["sources"])])
    elif kind == "flashcards":                                                   # front,back: imports into Anki and most flashcard apps
        for x in d["cards"]:
            w.writerow([_safe_cell(x["front"]), _safe_cell(x["back"])])
    elif kind == "quiz":
        w.writerow(["question", "A", "B", "C", "D", "answer", "explanation"])
        for x in d["questions"]:
            w.writerow([_safe_cell(x["question"]), *[_safe_cell(o) for o in x["options"]], "ABCD"[x["answer"]], _safe_cell(x["explanation"])])
    else:
        return None
    return buf.getvalue()


def _safe_cell(v: str) -> str:
    """A cell that a spreadsheet would run as a formula is prefixed with a quote (CSV formula injection)."""
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def to_pptx(d: Dict[str, Any], citations: Dict[str, Any]) -> bytes:
    from pptx import Presentation                                                  # optional dependency (requirements-reports.txt)
    from pptx.util import Pt
    prs = Presentation()
    title = prs.slides.add_slide(prs.slide_layouts[0])
    title.shapes.title.text = d.get("title") or "Slides"
    title.placeholders[1].text = "Made by PredictAI from your notebook sources"
    for s in d["slides"]:
        sl = prs.slides.add_slide(prs.slide_layouts[1])
        sl.shapes.title.text = s["title"]
        tf = sl.placeholders[1].text_frame
        tf.clear()
        for i, b in enumerate(s["bullets"] or [""]):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = b
            p.font.size = Pt(20)
        refs = "; ".join(f"{lab}: {citations.get(lab, {}).get('source_title') or '?'}" for lab in s["sources"])
        sl.notes_slide.notes_text_frame.text = (s["notes"] + ("\n\nSources: " + refs if refs else "")).strip()
    import io
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
