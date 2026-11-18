"""Deciding whether a search result, a section of an article or a passage of the person's own document is ABOUT the question, rather than merely sharing words with it.

The earlier ranking counted shared words. This module asks four separate questions and a candidate must pass all of the ones that apply:

  1. Identity   Does it concern the entity the question names? 'Louis XVI' is matched as a whole name, in any of its written forms, and a different member of the same family ('Louis XVIII',
                'Louis XIV') is recognised as a different entity and refused, even though the two share words. A mention in passing counts for less than being the subject.
  2. Kind       Is it the kind of thing a question of this field is about? A television series, a furniture style or a company that carries a ruler's name is not a source on that ruler's reign.
  3. Topic      Does it talk about what was asked (government, taxation, society, ...) and not only contain the entity's name? Measured against the vocabulary of each part of the plan, with
                a word in the title or description counting for more than one in the body.
  4. Context    A place or period named in the question ('France', 1789) can support a candidate that is otherwise on topic, but never qualifies it alone: one generic word is not evidence.

Every refusal carries a code and a plain sentence, so the person can see why a source was left out. Pure functions, no network."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from ..textkit import stem, tokenize
from .understanding import COUNTRIES, DEMONYMS, Entity, Frame, Period, alias_regex, fold, mentions, numbers_named

# --------------------------------------------------------------------------------------------------------------------------------------------- thresholds
SEARCH_TOPIC_MIN = 0.30          # a search result (title, description, lead) must reach this topical fit unless its title names the subject
TITLE_SUBJECT_TOPIC_MIN = 0.12   # ... or this when the title itself names the subject
RELATED_TOPIC_MIN = 0.45         # a candidate that does not name the subject at all must be clearly on topic, and share the place or period
PASSAGE_TOPIC_MIN = 0.40
SENTENCE_MIN_VOCAB = 2           # distinct topic words a sentence needs when it does not name the subject
ARXIV_TOPIC_MIN = 0.30

# What a question about history, politics or society is NOT about. Applied to a short description, categories and a title.
OFF_KIND = {
    "history": re.compile(r"\b(television|tv series|tv show|web series|miniseries|limited series|sitcom|film|movie|album|song|single|soundtrack|video game|videogame|board game|musical|opera|ballet|"
                          r"novel|comic|manga|anime|furniture|decorative arts?|interior design|fashion|clothing|cocktail|recipe|antiques?|perfume|brand|boutiques?|company|corporation|band|rapper|footballer|"
                          r"football club|sports team|fictional|characters?)\b", re.I),
    "natural_science": re.compile(r"\b(television|tv series|tv show|film|movie|album|song|soundtrack|video game|musical|novel|comic|band|rapper|footballer|football club|fictional|fashion|recipe)\b", re.I),
    "computing": re.compile(r"\b(television|tv series|tv show|film|movie|album|song|soundtrack|musical|novel|band|rapper|footballer|football club|fictional|fashion|recipe)\b", re.I),
}
OFF_KIND["mathematics"] = OFF_KIND["natural_science"]
OFF_KIND["economics"] = OFF_KIND["computing"]
OFF_KIND["health"] = OFF_KIND["computing"]

# arXiv categories that can bear on a question that is not about science: economic history and political economy, finance, social physics, digital libraries, computers and society.
ARXIV_SOCIAL = ("econ.", "q-fin.", "physics.soc-ph", "cs.dl", "cs.cy", "stat.ap", "cs.si")
ARXIV_FAMILIES = {"natural_science": ("physics", "astro-ph", "cond-mat", "hep-", "gr-qc", "quant-ph", "nlin", "nucl-", "q-bio", "stat.", "math-ph", "eess", "cs."),
                  "mathematics": ("math", "stat.", "cs.it", "cs.dm", "cs.cc", "cs.ds", "cs.lo", "math-ph", "quant-ph", "physics"),
                  "computing": ("cs.", "stat.ml", "stat.", "eess", "math.oc", "q-bio", "physics.comp-ph"),
                  "economics": ("econ.", "q-fin.", "stat.", "cs.", "math.oc", "physics.soc-ph"),
                  "health": ("q-bio", "stat.", "cs.", "physics.med-ph", "eess")}
GENERIC_WORDS = frozenset("""history historical world great new old period time year years people state states country nation general main important major early late modern ancient story life work
    system systems study studies research data analysis case review overview introduction history""".split())


@dataclass
class Verdict:
    accepted: bool
    score: float = 0.0
    identity: str = "n/a"             # subject | mention | related | sibling | none | n/a
    topical: float = 0.0
    code: str = "accepted"            # accepted | other_entity | off_kind | no_entity | generic_match | low_topic | wrong_field | no_context
    reasons: List[str] = field(default_factory=list)
    matched: List[str] = field(default_factory=list)
    entity: str = ""                  # key of the identity entity the candidate is about ("louis#16"), when there is one

    @property
    def reason(self) -> str:
        return " ".join(self.reasons)

    def to_dict(self) -> Dict[str, Any]:
        return {"accepted": self.accepted, "score": round(self.score, 3), "identity": self.identity, "topical": round(self.topical, 3), "code": self.code, "reason": self.reason, "matched": self.matched[:8], "entity": self.entity}


# --------------------------------------------------------------------------------------------------------------------------------------------- frames from plans
def entity_from_dict(d: Mapping[str, Any]) -> Entity:
    return Entity(str(d.get("kind") or "named"), str(d.get("name") or ""), str(d.get("key") or ""), str(d.get("family") or ""), d.get("number"), tuple(d.get("aliases") or ()), str(d.get("role") or "subject"))


def frame_from_plan(plan: Mapping[str, Any]) -> Frame:
    u = plan.get("understanding") or {}
    period = u.get("period")
    return Frame(question=str(plan.get("question") or ""), entities=[entity_from_dict(e) for e in u.get("entities") or []],
                 period=Period(period.get("start"), period.get("end"), period.get("relation") or "during", period.get("text") or "") if period else None,
                 domain=str(u.get("domain") or "general"), task=str(u.get("task") or "overview"), refers_to_document=bool(u.get("refers_to_document")),
                 arxiv_ids=list(u.get("arxiv_ids") or []), quoted_titles=list(u.get("quoted_titles") or []))


def vocab_stems(words: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for w in words:
        for t in tokenize(str(w), drop_stopwords=True, min_len=3):
            out.add(t)
    return out


def subtopic_vocab(subtopic: Mapping[str, Any]) -> Set[str]:
    return vocab_stems(list(subtopic.get("vocab") or []) + list(subtopic.get("topic_terms") or []))


def frame_vocab(plan: Mapping[str, Any], frame: Frame) -> Set[str]:
    v: Set[str] = set()
    for st in plan.get("subtopics") or []:
        if st.get("scope", "general") == "general":
            v |= subtopic_vocab(st)
    context = {stem(w) for e in frame.entities for a in e.aliases or (e.name,) for w in fold(a).split()}
    return v - context


def _stems(text: str) -> Set[str]:
    return set(tokenize(text or "", drop_stopwords=True, min_len=3))


# --------------------------------------------------------------------------------------------------------------------------------------------- the four questions
def topic_fit(title_like: str, body: str, vocab: Set[str]) -> float:
    """0..1: how many of the plan's topic words appear, with a word in the title or description counting three times as much as one in the body."""
    if not vocab:
        return 0.0
    t = len(_stems(title_like) & vocab)
    b = len((_stems(body) - _stems(title_like)) & vocab)
    return 1.0 - math.exp(-0.5 * (3 * t + b))


def identity_of(frame: Frame, title: str, description: str, text: str) -> Dict[str, Any]:
    """How the candidate relates to the question's identity entities: subject | mention | sibling | none. `sibling` means it is about another member of the same family."""
    ids = frame.identities
    if not ids:
        return {"level": "n/a", "entity": None, "reasons": [], "sibling": None}
    wanted = {(e.family, e.number) for e in ids}
    best = ("none", None)
    rank = {"none": 0, "mention": 1, "subject": 2}
    for e in ids:
        level = "none"
        if mentions(title, e) or mentions(description, e):
            level = "subject"
        else:
            n = mentions(text, e)
            first = numbers_named(text, e.family)
            if n >= 2 or (n == 1 and first and first[0] == e.number):
                level = "subject" if (first and first[0] == e.number and n >= 2) else "mention"
            elif n == 1:
                level = "mention"
        if rank[level] > rank[best[0]]:
            best = (level, e)
    # a different member of the same family?
    other = None
    for e in ids:
        in_title = [n for n in numbers_named(title, e.family) if (e.family, n) not in wanted]
        in_title_own = any((e.family, n) in wanted for n in numbers_named(title, e.family))
        if in_title and not in_title_own:
            other = (e, in_title[0], "title")
            break
        lead = numbers_named((description + " " + text)[:500], e.family)
        own_first = lead and (e.family, lead[0]) in wanted
        foreign = [n for n in lead if (e.family, n) not in wanted]
        if foreign and not own_first and best[0] != "subject":
            other = (e, foreign[0], "lead")
            break
    if other and best[0] != "subject":
        return {"level": "sibling", "entity": other[0], "reasons": [], "sibling": other[1]}
    return {"level": best[0], "entity": best[1], "reasons": [], "sibling": None}


def context_expected(frame: Frame) -> bool:
    """Does the question carry a place or a period that a source could share? If not, sharing one cannot be asked of it."""
    asked = fold(frame.question)
    return bool(frame.places or (frame.period and (frame.period.start or frame.period.end)) or any(re.search(rf"(?<![a-z]){dem}(?![a-z])", asked) for dem in DEMONYMS))


def context_match(frame: Frame, title: str, text: str) -> bool:
    low = fold(f"{title} {text}")
    asked = fold(frame.question)
    implied = {place for dem, place in DEMONYMS.items() if re.search(rf"(?<![a-z]){dem}(?![a-z])", asked)}          # "the French Revolution" is about France even if France is not named
    places = [(e.name, e.key) for e in frame.places] + [(k.title(), k) for k in implied if k not in {e.key for e in frame.places}]
    for name, key in places:
        if re.search(rf"(?<![a-z]){re.escape(fold(name))}(?![a-z])", low):
            return True
        for dem, place in DEMONYMS.items():
            if place == key and re.search(rf"(?<![a-z]){dem}(?![a-z])", low):
                return True
    if frame.period and (frame.period.start or frame.period.end):
        lo = frame.period.start or (frame.period.end or 0) - 60
        hi = frame.period.end or (frame.period.start or 0) + 60
        for y in re.findall(r"\b(1[0-9]{3})\b", low):
            if lo - 20 <= int(y) <= hi + 20:
                return True
    return False


def off_kind(frame: Frame, description: str, categories: Sequence[str], title: str) -> Optional[str]:
    rx = OFF_KIND.get(frame.domain)
    if rx is None:
        return None
    asked = fold(frame.question)
    m = rx.search(description or "")
    if not m:
        paren = re.search(r"\(([^)]*)\)\s*$", title or "")
        m = rx.search(paren.group(1)) if paren else None
    if not m:
        hits = [c for c in categories if rx.search(c)]
        if len(hits) >= 2:
            m = rx.search(hits[0])
    if not m:
        m = rx.search(title or "")
    if m and fold(m.group(0)) not in asked:
        return m.group(0).lower()
    return None


# --------------------------------------------------------------------------------------------------------------------------------------------- one candidate
def assess(plan: Mapping[str, Any], frame: Frame, *, title: str = "", description: str = "", categories: Sequence[str] = (), text: str = "", provider: str = "web", rank: int = 1,
           subtopic: Optional[Mapping[str, Any]] = None, stage: str = "search", extra: Optional[Mapping[str, Any]] = None) -> Verdict:
    """Whether a search result (stage 'search': title, description, lead or snippet) or a passage (stage 'passage') is about the question."""
    extra = extra or {}
    cats = list(categories)
    vocab = subtopic_vocab(subtopic) if subtopic else frame_vocab(plan, frame)
    if subtopic is None and not vocab:
        vocab = vocab_stems(w for p in (plan.get("focus") or {}).get("terms") or [] for w in [p]) - {stem(fold(e.name)) for e in frame.places}
    head = " ".join([title or "", description or "", " ".join(cats[:6])])
    topical = topic_fit(head, text, vocab) if vocab else 0.0
    ident = identity_of(frame, title, description, text)
    level = ident["level"]
    ctx = context_match(frame, title, text) or not context_expected(frame)
    v = Verdict(False, 0.0, level, topical)
    if ident.get("entity") is not None and level in ("subject", "mention"):
        v.entity = ident["entity"].key

    # --- arXiv has its own field rules: a paper must belong to a field the question is about
    if provider == "arxiv":
        prim = str(extra.get("primary_category") or "").lower()
        allcats = [c.lower() for c in (extra.get("categories") or [prim])]
        if frame.domain in ARXIV_FAMILIES:
            fits = any(c.startswith(ARXIV_FAMILIES[frame.domain]) for c in allcats)
        else:
            fits = any(c.startswith(ARXIV_SOCIAL) for c in allcats) if frame.domain in ("history", "arts", "geography") else True
        if not fits:
            v.code, v.reasons = "wrong_field", [f"It is filed under {', '.join(allcats[:3]) or 'a field'} on arXiv, which is not a field this question belongs to ({frame.domain.replace('_', ' ')})."]
            return v
    # --- another member of the same family
    if level == "sibling":
        e, n = ident["entity"], ident["sibling"]
        from .understanding import int_to_roman
        v.code = "other_entity"
        v.reasons = [f"It is about {e.family.capitalize()} {int_to_roman(n)}, not {e.name}: a different person who shares part of the name."]
        return v
    # --- kind of thing
    kind = off_kind(frame, description, cats, title) if provider in ("wikipedia", "searxng", "web") else None
    if kind:
        v.code = "off_kind"
        v.reasons = [f"It is about a {kind}, not about the subject of this question ({frame.domain.replace('_', ' ')})."]
        return v
    # --- identity and topic
    matched = [e.name for e in frame.identities if mentions(f"{title} {description} {text}", e)] + [e.name for e in frame.entities if e.kind == "named" and
               re.search(rf"(?<![a-z]){re.escape(fold(e.name))}(?![a-z])", fold(f"{title} {description} {text}"))]
    v.matched = matched
    has_named = any(e.kind == "named" for e in frame.entities)
    named_hit = any(e.kind == "named" and re.search(rf"(?<![a-z]){re.escape(fold(e.name))}(?![a-z])", fold(f"{title} {description} {text}")) for e in frame.entities)
    if frame.identities:
        if level == "subject":
            floor = TITLE_SUBJECT_TOPIC_MIN if (mentions(title, ident["entity"]) or mentions(description, ident["entity"])) else SEARCH_TOPIC_MIN
            if stage == "passage":
                floor = PASSAGE_TOPIC_MIN * 0.5
            if topical < floor:
                v.code = "low_topic"
                v.reasons = [f"It names {ident['entity'].name} but says little about what was asked ({_vocab_hint(plan)})."]
                return v
        elif level == "mention":
            floor = SEARCH_TOPIC_MIN if stage == "search" else PASSAGE_TOPIC_MIN * 0.7
            if topical < floor:
                v.code = "low_topic"
                v.reasons = ["It mentions the subject only in passing and says little about what was asked."]
                return v
        else:                                                                                # does not name the subject at all
            if not (topical >= RELATED_TOPIC_MIN and ctx):
                v.code = "no_entity" if topical < RELATED_TOPIC_MIN else "no_context"
                v.reasons = [f"It does not mention {_names(frame)}." if topical < RELATED_TOPIC_MIN else f"It is on topic but shares no place or period with the question and never names {_names(frame)}."]
                return v
            level = "related"
            v.identity = "related"
    elif has_named:
        if not named_hit and not (topical >= RELATED_TOPIC_MIN and ctx):
            v.code = "no_entity" if topical < RELATED_TOPIC_MIN else "no_context"
            v.reasons = [f"It does not mention {_names(frame)}." if topical < RELATED_TOPIC_MIN else "It is on topic but shares no place or period with the question."]
            return v
        if named_hit and topical < TITLE_SUBJECT_TOPIC_MIN and stage == "search" and provider != "wikipedia":
            v.code = "low_topic"
            v.reasons = ["It names the subject but says little about what was asked."]
            return v
        v.identity = "subject" if named_hit else "related"
        level = v.identity
    else:
        # no named entity: the topic words themselves must carry the match, and not one generic word alone
        phrase_hit = _phrase_hit(plan, f"{title} {description} {text}")
        distinct = len(_stems(f"{title} {description} {text}") & vocab)
        if vocab and not (phrase_hit or distinct >= 2 or topical >= SEARCH_TOPIC_MIN):
            v.code = "generic_match" if distinct else "low_topic"
            v.reasons = ["It shares only a general word with the question." if distinct else "It does not talk about what was asked."]
            return v
        v.identity = "n/a"
    v.accepted = True
    id_score = {"subject": 1.0, "mention": 0.55, "related": 0.4, "n/a": 0.6}.get(v.identity, 0.5)
    prior = 1.0 / (1.0 + 0.4 * (max(1, rank) - 1))
    v.score = round(0.45 * id_score + 0.40 * topical + 0.05 * prior + 0.10 * (1.0 if ctx else 0.0), 4)
    v.code = "accepted"
    v.reasons = [{"subject": "It is about the subject of the question.", "mention": "It discusses the subject.", "related": "It covers the same topic, place and period.", "n/a": "It matches the topic."}[v.identity if v.identity in ("subject", "mention", "related") else "n/a"]]
    return v


def _names(frame: Frame) -> str:
    names = [e.name for e in frame.identities] or [e.name for e in frame.entities if e.kind == "named"]
    return " or ".join(names[:3]) or "the subject"


def _vocab_hint(plan: Mapping[str, Any]) -> str:
    titles = [st.get("title") for st in (plan.get("subtopics") or []) if st.get("scope", "general") == "general"][:3]
    return ", ".join(str(t).lower() for t in titles if t) or "the topic"


def _phrase_hit(plan: Mapping[str, Any], text: str) -> bool:
    low = fold(text)
    for p in (plan.get("focus") or {}).get("phrases") or []:
        if len(str(p).split()) >= 2 and re.search(rf"(?<![a-z]){re.escape(fold(str(p)))}(?![a-z])", low):
            return True
    return False


# --------------------------------------------------------------------------------------------------------------------------------------------- sentences and sections
def sentence_ok(frame: Frame, vocab: Set[str], sentence: str, *, source_identity: str = "n/a", section_words: Optional[Set[str]] = None, min_hits: int = SENTENCE_MIN_VOCAB) -> bool:
    """May this sentence be quoted as evidence? It must name the subject or contain at least SENTENCE_MIN_VOCAB distinct topic words, and must not be about another family member."""
    ids = frame.identities
    if ids:
        for e in ids:
            others = [n for n in numbers_named(sentence, e.family) if (e.family, n) not in {(x.family, x.number) for x in ids}]
            if others and not any(mentions(sentence, x) for x in ids):
                return False                                                                  # a sentence about Louis XVIII is not a statement about Louis XVI
            if others and numbers_named(sentence, e.family)[0] in others and source_identity != "subject":
                return False
    hits = len(_stems(sentence) & vocab) + (SENTENCE_MIN_VOCAB if (section_words and section_words & vocab) else 0)         # a sentence in a section whose heading is on topic is on topic
    named = any(mentions(sentence, e) for e in ids)
    return named and hits >= 1 or hits >= min_hits or (named and not vocab)


def names_other_entity(frame: Frame, text: str) -> bool:
    """True when the text names another member of the subject's family (Louis XVIII for Louis XVI) and never names the subject itself."""
    ids = frame.identities
    wanted = {(e.family, e.number) for e in ids}
    if not ids or any(mentions(text, e) for e in ids):
        return False
    return any((e.family, n) not in wanted for e in ids for n in numbers_named(text, e.family))


def section_fit(frame: Frame, section: Mapping[str, Any], vocab: Set[str]) -> float:
    return topic_fit(" ".join(section.get("path") or [section.get("heading") or ""]), section.get("text") or "", vocab)


def select_sections(structure: Mapping[str, Any], plan: Mapping[str, Any], frame: Frame, *, max_chars: int = 16000, per_part: int = 2, lead_chars: int = 3500, floor: float = 0.30) -> List[Dict[str, Any]]:
    """The lead plus, for every part of the plan, the sections of an article that fit it best. Section-level, not article-level: an article on the right subject contributes only what bears on the question."""
    secs = [s for s in structure.get("sections") or [] if s.get("text")]
    if not secs:
        return []
    chosen: Dict[str, Dict[str, Any]] = {}
    lead = structure["sections"][0]
    used = 0
    if lead.get("text"):
        chosen[lead["id"]] = lead
        used += min(len(lead["text"]), lead_chars)
    for s in secs:                                                                           # a paper's or article's own statement of what it is about
        head = (s.get("heading") or "").strip().lower()
        if re.fullmatch(r"(abstract|introduction|overview|summary)", head) and s["id"] not in chosen and used + min(len(s["text"]), 4000) <= max_chars:
            chosen[s["id"]] = s
            used += min(len(s["text"]), 4000)
    if frame.task == "methodology":
        per_part = max(per_part, 3)
    parts = [st for st in plan.get("subtopics") or [] if st.get("scope", "general") == "general"] or [{"vocab": list(frame_vocab(plan, frame))}]
    for st in parts:
        vocab = subtopic_vocab(st)
        ranked = sorted(((section_fit(frame, s, vocab), s) for s in secs if s["id"] != lead.get("id")), key=lambda x: (-x[0], x[1]["id"]))
        taken = 0
        for fit, s in ranked:
            if taken >= per_part or fit < floor:
                break
            if s["id"] in chosen:
                taken += 1
                continue
            size = min(len(s["text"]), 4000)
            if used + size > max_chars:
                continue
            chosen[s["id"]] = s
            used += size
            taken += 1
    order = {s["id"]: i for i, s in enumerate(structure["sections"])}
    return sorted(chosen.values(), key=lambda s: order[s["id"]])
