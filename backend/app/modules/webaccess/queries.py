"""Turning a question into the short queries each provider is actually good at.

Root cause this module fixes (Phase 6 corrective work): the planner used to send ONE keyword bag (the question's content words plus filler such as "background
history definition") to every provider. Wikipedia then ranked pages that merely contain those words, and arXiv received `all:a AND all:b AND ...` with six or more
ANDed terms, which almost never matches anything. Here a question is reduced to its topic phrases, and every provider gets queries written in its own syntax,
from most precise to broadest. Nothing but words of the person's own question is used (no document text, no dataset values).

Pure functions, no network."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from ..textkit import STOPWORDS

# Words that describe the QUESTION (what the person wants done) rather than the TOPIC (what it is about). They end a topic phrase.
SCAFFOLD = frozenset("""
factor factors associated association associations related relate relates relationship relationships relation relations correlated correlate correlates correlation correlations
source sources say says said tell tells told describe describes described explain explains explained mean means meaning evidence research researches study studies
literature paper papers article articles information info overview summary summarize summarise find finds found finding findings show shows shown showing analysis analyse
analyze analyses data dataset datasets compare compared comparison comparisons difference differences similarity similarities using use used uses main key important
major common typical general generally current currently recent latest new give gives list please help question questions answer answers based regarding concerning
within across around many much whether thing things way ways kind kinds role roles effect effects impact impacts influence influences affect affects affected
determine determines determined drive drives driven cause causes caused reason reasons explain predict predicts predicted prediction predictions behind
tell us let know want need needs like likely after before during while since until about into onto upon via per towards toward over under between among
show showing whose outside external online elsewhere independent
happen happens happened happening occur occurs occurred occurring begin begins began begun start starts started arise arises arose come comes came become becomes became take took taken place
""".split())
# Words that only point at the person's own material ("summarise this document"). Alone they are not a topic to look up outside, so a phrase that is just one of them is dropped; inside a longer phrase
# ("document databases", "periodic table") they stay.
REFERENTIAL = frozenset("document documents file files upload uploads uploaded attached attachment pdf spreadsheet csv table tables text report reports record records column columns row rows".split())
PUNCT_BREAK = re.compile(r"[,;:()\[\]{}?!\"“”]|\s[-–—]\s|\.\s")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-+&'’./]*[A-Za-z0-9+]|[A-Za-z0-9]")
MAX_PHRASE_WORDS = 5


@dataclass
class Focus:
    """What a question is about, in order of importance."""
    phrases: List[str] = field(default_factory=list)          # "business class seat availability", "airlines", "aviation"
    terms: List[str] = field(default_factory=list)            # distinct single words of the phrases, same order
    natural: str = ""                                         # a short, clean sentence-like version for general web search

    @property
    def multi(self) -> List[str]:
        return [p for p in self.phrases if len(p.split()) >= 2]

    @property
    def single(self) -> List[str]:
        return [p for p in self.phrases if len(p.split()) == 1]


def _is_boundary(word: str) -> bool:
    low = word.lower()
    return low in STOPWORDS or low in SCAFFOLD or (len(low) < 2 and not low.isdigit())


def topic_focus(question: str, *, max_phrases: int = 4) -> Focus:
    """Split a question into topic phrases: runs of content words between stop words, scaffolding words (factors, say, sources ...) and punctuation."""
    text = " ".join((question or "").split())
    if re.search(r"\bhow\s+(?:do|does|did|can|could)\b", text, re.I):
        text = re.sub(r"\s+work(?:s|ing)?\b(?=\s*(?:[?.!,;]|$|\band\b|\bin\b|\bat\b))", "", text, flags=re.I)     # "how does X work": 'work' is the verb being asked about, not the topic
    phrases: List[str] = []
    for clause in PUNCT_BREAK.split(text):
        run: List[str] = []

        def flush() -> None:
            if run:
                phrase = " ".join(run)
                if phrase.lower() not in (p.lower() for p in phrases):
                    phrases.append(phrase)
            run.clear()

        for m in WORD.finditer(clause):
            w = m.group(0).strip("'’.-/")
            if not w:
                continue
            if _is_boundary(w):
                flush()
                continue
            run.append(w)
            if len(run) >= MAX_PHRASE_WORDS:
                flush()
        flush()
    phrases = [p for p in phrases if p.lower() not in REFERENTIAL]
    # a phrase that is wholly contained in a longer one adds nothing ("seat" inside "business class seat availability")
    kept = [p for p in phrases if not any(p.lower() != q.lower() and re.search(rf"\b{re.escape(p.lower())}\b", q.lower()) for q in phrases)]
    # longer, more specific phrases first; ties keep the order in which the person wrote them
    order = {p: i for i, p in enumerate(kept)}
    kept.sort(key=lambda p: (-min(len(p.split()), 4), order[p]))
    kept = kept[:max_phrases]
    terms: List[str] = []
    for p in kept:
        for w in p.split():
            wl = w.lower()
            if wl not in terms:
                terms.append(wl)
    natural = " ".join(kept)[:140]
    return Focus(phrases=kept, terms=terms[:12], natural=natural)


# ------------------------------------------------------------------ provider syntax
def _safe_term(term: str) -> str:
    """One arXiv/Wikipedia-safe word: letters, digits and hyphens only (no field prefixes, quotes, operators or parentheses can be smuggled in)."""
    return re.sub(r"[^A-Za-z0-9\-.]", "", term).strip(".-")                                 # a decimal point inside a number stays (2.4 is not 24); a leading '-' would be a search operator


def _safe_phrase(phrase: str) -> str:
    return " ".join(w for w in (_safe_term(x) for x in phrase.split()) if w)


def wikipedia_queries(focus: Focus, limit: int = 3) -> List[Dict[str, str]]:
    """Short topic names, most specific first: Wikipedia's search is best at "business class" and worst at a pile of loose words."""
    out: List[Dict[str, str]] = []
    for i, p in enumerate(focus.phrases[:2]):
        q = _safe_phrase(p)
        if q:
            out.append({"query": q, "role": "primary" if i == 0 else "additional"})
    if len(focus.phrases) >= 2:
        joined = _safe_phrase(" ".join(focus.phrases[:2]))
        if joined and joined.lower() not in (o["query"].lower() for o in out):
            out.append({"query": joined, "role": "fallback"})
    return out[:limit]


def arxiv_queries(focus: Focus, limit: int = 3) -> List[Dict[str, str]]:
    """arXiv search_query strings, precise to broad. Phrases are searched in the title or abstract; only then are loose words tried, and the broadest form uses OR."""
    out: List[Dict[str, str]] = []

    def add(q: str, role: str) -> None:
        if q and q not in (o["query"] for o in out):
            out.append({"query": q, "role": role})

    multi = [_safe_phrase(p) for p in focus.multi if _safe_phrase(p)]
    single = [_safe_term(p) for p in focus.single if _safe_term(p)]
    if multi:
        parts = [f'(ti:"{p}" OR abs:"{p}")' for p in multi[:2]]
        if len(parts) < 2 and single:
            parts.append(f"(ti:{single[0]} OR abs:{single[0]})")
        add(" AND ".join(parts), "primary")
    terms = [t for t in (_safe_term(t) for t in focus.terms) if len(t) >= 3 or t.isupper()]
    if len(terms) >= 2:
        add(" AND ".join(f"(ti:{t} OR abs:{t})" for t in terms[:3]), "primary" if not out else "fallback")
        add(" AND ".join(f"all:{t}" for t in terms[:2]), "fallback")
        add(" OR ".join(f"all:{t}" for t in terms[:4]), "fallback")
    elif terms:
        add(f"ti:{terms[0]} OR abs:{terms[0]}", "primary")
    return out[:limit]


FACET_WORDS = {"background": "overview", "key facts and figures": "statistics", "evidence and studies": "study", "disagreements and criticism": "criticism",
               "recent developments": "latest news", "limitations": "limitations", "methods and data": "methods data"}


def web_queries(focus: Focus, question: str, facet: str = "", limit: int = 2) -> List[Dict[str, str]]:
    """General web search: the topic phrases (plus at most one facet word), then the question itself as a natural sentence."""
    out: List[Dict[str, str]] = []
    if not focus.phrases:
        return out                                                                          # nothing to search for outside (for example "what does this dataset tell us?"): never send the bare question
    base = " ".join(_safe_phrase(p) for p in focus.phrases[:3]).strip()
    extra = FACET_WORDS.get(facet.lower(), "")
    if base:
        out.append({"query": f"{base} {extra}".strip(), "role": "primary"})
    plain = re.sub(r"[^\w\s\-+&'’/.]", " ", question or "")
    plain = " ".join(plain.split())[:160]
    if plain and plain.lower() not in (o["query"].lower() for o in out) and len(plain.split()) <= 24:
        out.append({"query": plain, "role": "fallback"})
    return out[:limit]


def build_provider_queries(provider: str, focus: Focus, question: str, facet: str = "") -> List[Dict[str, str]]:
    if provider == "wikipedia":
        return wikipedia_queries(focus)
    if provider == "arxiv":
        return arxiv_queries(focus)
    return web_queries(focus, question, facet)


# Which providers are worth asking for which part of a plan. The person's own provider choice always bounds this list.
FACET_PROVIDERS: Dict[str, Sequence[str]] = {
    "overview": ("wikipedia", "arxiv", "searxng"), "background": ("wikipedia", "searxng"), "background and definitions": ("wikipedia", "searxng"), "definition": ("wikipedia", "searxng"),
    "key facts and figures": ("searxng", "wikipedia"), "evidence and studies": ("arxiv", "searxng"), "disagreements and criticism": ("searxng", "arxiv"),
    "recent developments": ("searxng", "arxiv"), "how it works": ("wikipedia", "arxiv", "searxng"), "methods and data": ("arxiv", "searxng"), "limitations": ("arxiv", "searxng"),
    "foundations": ("wikipedia", "arxiv"), "methods": ("arxiv", "wikipedia"), "findings": ("arxiv", "searxng"), "criticism and limitations": ("arxiv", "searxng"),
}


def providers_for(facet: str, chosen: Sequence[str]) -> List[str]:
    """The chosen providers that suit this part of the plan; if none suit it, all chosen providers (a gap is worse than a loose search)."""
    wanted = FACET_PROVIDERS.get(facet.lower())
    if not wanted:
        return list(chosen)
    picked = [p for p in wanted if p in chosen]
    return picked or list(chosen)


# ------------------------------------------------------------------ recognising direct references (used by the independent Wikipedia / arXiv search endpoints)
ARXIV_NEW = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(v\d+)?(?![\d])")
ARXIV_OLD = re.compile(r"(?<![\w/])([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?(?!\d)")
ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([A-Za-z0-9.\-/]+?)(?:\.pdf)?(?:[?#].*)?$", re.I)


def arxiv_id_in(text: str) -> str:
    """The arXiv identifier (with a version if one was written) inside a string or arxiv.org URL, or ''."""
    t = (text or "").strip()
    m = ARXIV_URL.search(t)
    if m:
        t = m.group(1)
    for rx in (ARXIV_NEW, ARXIV_OLD):
        m2 = rx.search(t)
        if m2:
            return m2.group(1) + (m2.group(2) or "")
    return ""
