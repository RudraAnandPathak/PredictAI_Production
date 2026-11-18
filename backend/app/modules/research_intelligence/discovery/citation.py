"""Citation text built only from fields the providers reported. Missing parts are left out, never guessed."""
from __future__ import annotations

import re
from typing import Any, List

from .records import fold


def _apa_author(name: str) -> str:
    n = name.strip()
    if "," in n:
        last, _, first = n.partition(",")
    else:
        parts = n.split()
        if len(parts) < 2:
            return n
        last, first = parts[-1], " ".join(parts[:-1])
    initials = " ".join(f"{p[0].upper()}." for p in re.split(r"[\s\-]+", first.strip()) if p)
    return f"{last.strip()}, {initials}".strip().rstrip(",")


def apa(r: Any) -> str:
    authors: List[str] = [_apa_author(a) for a in (r.authors or [])[:20]]
    if not authors:
        who = ""
    elif len(authors) == 1:
        who = authors[0]
    elif len(authors) <= 20:
        who = ", ".join(authors[:-1]) + ", & " + authors[-1]
    else:
        who = ", ".join(authors[:19]) + ", ... " + authors[-1]
    year = f"({r.year})" if r.year else "(n.d.)"
    parts = [p for p in (who, year + ".", (r.title.rstrip(".") + "."), (f"{r.venue}." if r.venue else "")) if p]
    link = f"https://doi.org/{r.doi}" if r.doi else (f"https://arxiv.org/abs/{r.arxiv_id}" if r.arxiv_id else (r.url or ""))
    text = " ".join(parts)
    return (text + (" " + link if link else "")).strip()


def bibtex(r: Any) -> str:
    first = re.sub(r"[^a-z]", "", fold((r.authors[0] if r.authors else "anon").split(",")[0].split()[-1]).lower()) or "anon"
    word = next((w for w in re.findall(r"[a-z]{4,}", fold(r.title).lower())), "work")
    key = f"{first}{r.year or 'nd'}{word}"
    kind = {"journal-article": "article", "preprint": "misc", "conference-paper": "inproceedings", "book": "book", "book-chapter": "incollection", "thesis": "phdthesis", "report": "techreport"}.get(r.pub_type, "misc")
    esc = lambda s: str(s).replace("\\", " ").replace("{", "(").replace("}", ")").replace("%", "\\%").replace("&", "\\&")        # noqa: E731
    fields = [("title", "{" + esc(r.title) + "}"), ("author", " and ".join(esc(a) for a in r.authors[:30]) if r.authors else None), ("year", str(r.year) if r.year else None),
              ("journal" if kind == "article" else ("booktitle" if kind in ("inproceedings", "incollection") else "howpublished"), esc(r.venue) if r.venue else None),
              ("doi", r.doi), ("eprint", r.arxiv_id), ("url", r.url if not r.doi else None)]
    body = ",\n".join(f"  {k} = {{{v}}}" if k != "title" else f"  title = {v}" for k, v in fields if v)
    return f"@{kind}{{{key},\n{body}\n}}"
