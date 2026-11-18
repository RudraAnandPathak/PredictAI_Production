"""Which providers suit a question, and what each is asked.

Providers differ in what they are good at, so one keyword bag sent everywhere (the earlier design) finds whatever happens to contain the words:
  Wikipedia  is asked for the ARTICLE ABOUT the named entity by exact title ("Louis XVI"), then for the topical articles around it. It is a tertiary source and suits background.
  arXiv      holds preprints in physics, mathematics, computer science, statistics, quantitative biology/finance and economics. It is not a general academic database: a history question
             is not sent to it unless the person asks, and when it is asked, a paper is kept only if its field and its abstract fit the question.
  SearXNG    is general web search. Its phrases are written per field: a history question quotes the entity and adds a topical word; a science question adds "review"; a software question adds
             "documentation".

An outbound phrase never holds more than four distinct content words, so it cannot be mistaken for a pasted sentence of the person's own text. Only words of the person's question and the
fixed topic hints of the outline are used. Pure functions, no network."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..textkit import STOPWORDS
from ..webaccess.queries import Focus, arxiv_id_in, arxiv_queries, build_provider_queries, topic_focus
from .understanding import DEMONYMS, fold, Entity, Frame

MAX_CONTENT_WORDS = 4
ARXIV_FIELDS = "physics, mathematics, computer science, statistics, quantitative biology and finance, and economics"
ARXIV_MODES = ("auto", "always", "never")
STRICT_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-.]*")


def _content_count(text: str) -> int:
    return len({w.lower() for w in STRICT_WORD.findall(text) if w.lower() not in STOPWORDS})


def cap_words(parts: Sequence[str], limit: int = MAX_CONTENT_WORDS) -> str:
    """Join phrases in priority order, dropping whole trailing phrases or words once more than `limit` distinct content words would be sent."""
    seen: List[str] = []
    out: List[str] = []
    for part in parts:
        part = " ".join(str(part).split())
        if not part:
            continue
        words = [w for w in STRICT_WORD.findall(part)]
        new = [w for w in words if w.lower() not in STOPWORDS and w.lower() not in seen]
        if len(seen) + len(new) <= limit:
            out.append(part)
            seen += [w.lower() for w in new]
            continue
        room = limit - len(seen)
        if room > 0:
            kept: List[str] = []
            for w in words:
                if w.lower() in STOPWORDS or w.lower() in seen:
                    kept.append(w)
                elif room > 0:
                    kept.append(w)
                    seen.append(w.lower())
                    room -= 1
            out.append(" ".join(kept))
    return " ".join(out).strip()


def provider_fit(frame: Frame, providers: Sequence[str], arxiv_mode: str = "auto") -> Tuple[List[str], List[Dict[str, str]]]:
    """(providers to use, providers left out with the reason). Only arXiv is ever left out for subject reasons; the person's own consent and choices bound the list first."""
    use: List[str] = []
    skipped: List[Dict[str, str]] = []
    mode = arxiv_mode if arxiv_mode in ARXIV_MODES else "auto"
    for p in providers:
        if p != "arxiv":
            use.append(p)
            continue
        if mode == "never":
            skipped.append({"provider": "arxiv", "reason": "You chose not to search arXiv for this research."})
        elif mode == "always":
            use.append(p)
        elif frame.arxiv_ids or (frame.quoted_titles and frame.scholarly) or frame.scholarly:
            use.append(p)
        elif frame.task == "methodology" and (frame.quoted_titles or frame.arxiv_ids):
            use.append(p)
        else:
            what = frame.domain.replace("_", " ")
            why = (f"This question is about {what if frame.domain != 'general' else 'a subject outside those fields'}" if frame.domain not in ("general",) else "No scientific field was recognised in the question")
            skipped.append({"provider": "arxiv", "reason": f"arXiv holds research papers in {ARXIV_FIELDS}. {why}, so arXiv was not searched. Choose “Always search arXiv” to search it anyway."})
    return use, skipped


def _anchor(frame: Frame, st: Mapping[str, Any]) -> str:
    key = st.get("entity")
    if key and key != "*":
        for e in frame.entities:
            if e.key == key:
                return e.name
    if key == "*":
        return " ".join(e.name for e in frame.identities[:2])
    if frame.identities:
        return frame.identities[0].name
    subjects = frame.subjects
    return subjects[0].name if subjects else ""


def exact_entities(frame: Frame) -> List[Entity]:
    return [e for e in frame.entities if e.role == "subject" and e.kind in ("ruler", "named")][:3]


def queries_for(provider: str, frame: Frame, st: Mapping[str, Any], focus: Focus, question: str, *, first: bool) -> List[Dict[str, Any]]:
    """The phrases for one provider and one part of the plan, most precise first."""
    anchor = _anchor(frame, st)
    place = frame.places[0].name if frame.places else ""
    hint = str(st.get("hint") or "")
    out: List[Dict[str, Any]] = []

    def add(q: str, role: str, **extra: Any) -> None:
        q = " ".join(q.split())
        if q and q.lower() not in (o["query"].lower() for o in out):
            out.append({"query": q[:300], "role": role, **extra})

    if provider == "wikipedia":
        if first:
            for e in exact_entities(frame):
                add(e.name, "primary", exact=True)
        add(cap_words([anchor, hint]) if anchor else cap_words([hint, *focus.phrases[:1]]), "additional" if first else "primary")
        if not anchor and not out:
            return build_provider_queries("wikipedia", focus, question, st.get("facet", ""))
        return out[:4]
    if provider == "arxiv":
        if frame.arxiv_ids:
            for i in frame.arxiv_ids[:2]:
                add(i, "primary")
            return out
        if frame.quoted_titles:
            for t in frame.quoted_titles[:2]:
                safe = " ".join(re.findall(r"[A-Za-z0-9\-]+", t))[:140]
                if safe:
                    add(f'ti:"{safe}"', "primary")
        for q in arxiv_queries(topic_focus(" ".join(x for x in (anchor, hint, *focus.phrases[:2]) if x)) if anchor else focus):
            add(q["query"], q["role"])
        where = place or next((pl.capitalize() for dem, pl in DEMONYMS.items() if re.search(rf"(?<![a-z]){dem}(?![a-z])", fold(frame.question))), "")
        topical: List[str] = []
        for w in re.findall(r"[a-z]{4,}", " ".join(st.get("vocab") or []).lower()):
            if all(w[:5] != t[:5] for t in topical):                                                       # economy / economic count once
                topical.append(w)
        if where and topical and not frame.identities:
            add(f"all:{where.lower()} AND (" + " OR ".join(f"all:{w}" for w in topical[:4]) + ")", "fallback")                   # a looser last resort: the place and four of the part's own topic words
        return out[:4]
    # general web search, written for the field
    domain = frame.domain
    phrase = anchor or (focus.phrases[0] if focus.phrases else "")
    if not phrase:
        return build_provider_queries(provider, focus, question, st.get("facet", ""))
    if domain == "history" or (frame.identities and domain in ("general", "arts")):
        quoted = f'"{anchor}"' if anchor else phrase
        add(cap_words([quoted, hint, place]), "primary")
        add(cap_words([anchor or phrase, place, "history"]), "fallback")
    elif domain in ("natural_science", "mathematics", "health"):
        add(cap_words([phrase, hint or "overview"]), "primary")
        add(cap_words([phrase, "review"]), "fallback")
    elif domain == "computing":
        add(cap_words([phrase, hint or "explained"]), "primary")
        add(cap_words([phrase, "documentation"]), "fallback")
    elif domain == "economics":
        add(cap_words([phrase, hint or "analysis", place]), "primary")
        add(cap_words([phrase, "statistics"]), "fallback")
    else:
        return build_provider_queries(provider, focus, question, st.get("facet", ""))
    return out[:2]
