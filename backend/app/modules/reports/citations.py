"""Citation formatting. Source metadata is stored once (in `rep_evidence.citation`); every style is produced from it on demand. A field that is missing is reported as missing; nothing is
guessed, so an unknown author is never replaced by the website's name and an unknown date is written "n.d." (the style's own convention) and listed in `missing`."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Tuple

STYLES = ("apa", "mla", "chicago", "ieee", "harvard")
LABELS = {"apa": "APA 7", "mla": "MLA 9", "chicago": "Chicago (author-date)", "ieee": "IEEE", "harvard": "Harvard"}


def _name(a: str) -> Tuple[str, str]:
    a = a.strip()
    if "," in a:
        last, first = [p.strip() for p in a.split(",", 1)]
    else:
        parts = a.split()
        last, first = (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (a, "")
    return last, first


def _initials(first: str) -> str:
    return " ".join(p[0] + "." for p in re.split(r"[\s\-]+", first) if p)


def _year(meta: Mapping[str, Any]) -> str:
    m = re.match(r"(\d{4})", str(meta.get("date") or ""))
    return m.group(1) if m else ""


def missing_fields(meta: Mapping[str, Any]) -> List[str]:
    out = []
    if not meta.get("authors"):
        out.append("author")
    if not _year(meta):
        out.append("date")
    if not meta.get("title"):
        out.append("title")
    if meta.get("kind") in ("web", "wikipedia", "arxiv", "paper") and not (meta.get("url") or meta.get("doi")):
        out.append("url")
    return out


def format_citation(meta: Mapping[str, Any], style: str) -> Dict[str, Any]:
    """Returns {"text": formatted reference, "missing": [fields the source did not supply]}."""
    style = style if style in STYLES else "apa"
    authors = [a for a in (meta.get("authors") or []) if a]
    title = meta.get("title") or "Untitled source"
    year = _year(meta)
    pub = meta.get("publisher") or meta.get("site") or ""
    link = meta.get("doi") and f"https://doi.org/{meta['doi']}" or meta.get("url") or ""
    accessed = (meta.get("retrieved") or "")[:10]
    page = meta.get("page")
    kind = meta.get("kind") or "web"
    names = [_name(a) for a in authors]
    if style == "apa":
        each = [f"{l}, {_initials(f)}".strip().rstrip(",") for l, f in names[:6]]
        who = "" if not each else (each[0] if len(each) == 1 else ", ".join(each[:-1]) + ", & " + each[-1]) + (", et al." if len(names) > 6 else "")
        text = f"{who + ' ' if who else ''}({year or 'n.d.'}). {title}." + (f" {pub}." if pub else "") + (f" {link}" if link else "") + (f" (p. {page})" if page else "")
    elif style == "mla":
        who = ""
        if names:
            who = f"{names[0][0]}, {names[0][1]}".strip().rstrip(",") + (", et al" if len(names) > 2 else (f", and {' '.join(reversed(names[1]))}" if len(names) == 2 and names[1][1] else ""))
        text = f"{who + '. ' if who else ''}“{title}.”" + (f" {pub}," if pub else "") + (f" {year}," if year else "") + (f" {link}." if link else "") + (f" Accessed {accessed}." if accessed else "")
    elif style == "chicago":
        who = ", ".join(f"{f} {l}".strip() for l, f in names) if names else ""
        text = f"{who + '. ' if who else ''}{year or 'n.d.'}. “{title}.”" + (f" {pub}." if pub else "") + (f" {link}." if link else "")
    elif style == "ieee":
        who = ", ".join(f"{_initials(f)} {l}".strip() for l, f in names[:3]) + (" et al." if len(names) > 3 else "")
        text = f"{who + ', ' if who else ''}“{title},”" + (f" {pub}," if pub else "") + (f" {year}." if year else " n.d.") + (f" [Online]. Available: {link}" if link else "")
    else:  # harvard
        who = " and ".join(f"{l}, {_initials(f)}".strip().rstrip(",") for l, f in names[:3]) + (" et al." if len(names) > 3 else "")
        text = f"{who + ' ' if who else ''}({year or 'n.d.'}) {title}." + (f" {pub}." if pub else "") + (f" Available at: {link}" if link else "") + (f" (Accessed: {accessed})." if accessed else "")
    return {"text": re.sub(r"\s+", " ", text).strip(), "missing": missing_fields(meta), "style": style, "kind": kind}


def in_text(style: str, number: int, meta: Mapping[str, Any]) -> str:
    """The short marker placed next to a claim."""
    if style == "ieee":
        return f"[{number}]"
    names = [_name(a)[0] for a in (meta.get("authors") or []) if a]
    who = names[0] + (" et al." if len(names) > 2 else (f" & {names[1]}" if len(names) == 2 else "")) if names else (meta.get("site") or meta.get("title") or "source")
    year = _year(meta) or "n.d."
    if style == "mla":
        return f"({who}{', p. ' + str(meta['page']) if meta.get('page') else ''})"
    return f"({who}, {year})" if style != "harvard" else f"({who} {year})"
