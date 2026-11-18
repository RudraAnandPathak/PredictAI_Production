"""Understanding a research question before anything is searched: who or what it is about (entities), when and where (period, place), what kind of question it is, which field it
belongs to, and whether it is clear enough to research at all.

Why this exists. The earlier planner reduced a question to its content words and everything downstream ranked by how many of those words a page shared. "How was France under Louis XVI?"
became {france, louis, xvi}; a page about Louis XVIII, a furniture style named after Louis XVI, a television series set in St. Louis and a physics paper by an author called Louis all
"matched". Here the question is first read for IDENTITY: "Louis XVI", "Louis 16", "Louis the Sixteenth" and "King Louis XVI" are one entity; "Louis XVIII" and "Louis XIV" are different ones
of the same family and are recognised as such, so a page about them can be refused even though it shares words with the question.

Pure functions, no network, no language model: the same question always gives the same frame, which is what lets a person read and approve the plan. (An optional AI reading of the question
may add to the frame; it can never remove an identity check or approve a search.)"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..webaccess.queries import arxiv_id_in

# ------------------------------------------------------------------------------------------------------------------------------- numerals
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
ORDINAL_WORDS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth",
                 "seventeenth", "eighteenth", "nineteenth", "twentieth", "twenty-first", "twenty-second", "twenty-third", "twenty-fourth", "twenty-fifth", "twenty-sixth", "twenty-seventh",
                 "twenty-eighth", "twenty-ninth", "thirtieth"]


def int_to_roman(n: int) -> str:
    out = ""
    for value, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += sym
            n -= value
    return out


def roman_to_int(text: str) -> Optional[int]:
    """The value of a well-formed Roman numeral, else None ('XVIIII' and 'IIX' are not numerals)."""
    s = (text or "").upper()
    if not s or any(c not in ROMAN_VALUES for c in s):
        return None
    total = 0
    for i, c in enumerate(s):
        v = ROMAN_VALUES[c]
        total += -v if i + 1 < len(s) and ROMAN_VALUES[s[i + 1]] > v else v
    return total if total > 0 and int_to_roman(total) == s else None


def ordinal_word(n: int) -> str:
    return ORDINAL_WORDS[n - 1] if 1 <= n <= len(ORDINAL_WORDS) else ""


def _ordinal_suffix(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def fold(text: str) -> str:
    """Lower-case text without accents, so 'Régime', 'Regime' and 'REGIME' are one word."""
    return "".join(c for c in unicodedata.normalize("NFKD", (text or "").lower()) if not unicodedata.combining(c))


# ------------------------------------------------------------------------------------------------------------------------------- vocabulary
# Names that kings, queens, emperors and popes share across many reigns: "Louis" alone does not say which one.
REGNAL_NAMES = frozenset("""louis henry henri charles george edward james john william richard philip philippe frederick frederic ferdinand alexander nicholas napoleon leopold peter paul pius benedict
gregory clement innocent leo urban boniface stephen elizabeth victoria mary anne catherine isabella maria margaret christian gustav gustavus otto conrad frederick albert alfonso alphonse carlos
francis francois joseph franz wilhelm ludwig charlemagne""".split())
TITLE_WORDS = frozenset("king queen emperor empress pope tsar czar prince princess saint st sultan kaiser duke duchess pharaoh".split())
COUNTRIES = frozenset("""france germany italy spain portugal england britain scotland wales ireland russia china india japan korea egypt greece rome turkey persia iran iraq syria israel poland austria hungary
sweden norway denmark finland netherlands holland belgium switzerland brazil mexico canada australia africa europe asia america argentina chile peru cuba haiti prussia bavaria bohemia flanders
burgundy normandy brittany provence navarre castile aragon venice florence genoa paris london berlin madrid moscow vienna versailles lyon marseille bordeaux rome athens ottoman byzantium""".split())
DEMONYMS = {"french": "france", "german": "germany", "italian": "italy", "spanish": "spain", "english": "england", "british": "britain", "russian": "russia", "chinese": "china", "indian": "india",
            "japanese": "japan", "greek": "greece", "roman": "rome", "turkish": "turkey", "persian": "persia", "dutch": "netherlands", "swedish": "sweden", "austrian": "austria", "prussian": "prussia",
            "american": "america", "mexican": "mexico", "brazilian": "brazil", "egyptian": "egypt"}
LEADING_WORDS = frozenset("""how what why when where who whom which whose explain describe compare tell give list summarize summarise discuss analyze analyse is are was were did do does can could should
would will please show find define identify outline evaluate assess contrast examine in on of the a an and or but if then so also now i you we they it this that these those after before during
under between among from for with without about into onto over per via versus vs""".split())
CONNECTORS = frozenset("of the de du des la le von van di del el al bin".split())

# French kings named Louis, offered when a question says "Louis" and "France" without a number. Reign dates are the standard ones.
KNOWN_RULERS: Dict[str, List[Tuple[int, str]]] = {
    "louis": [(9, "1226–1270"), (11, "1461–1483"), (14, "1643–1715"), (15, "1715–1774"), (16, "1774–1792"), (18, "1814–1815 and 1815–1824")],
}
# Names that mean several different things. A bare one ("Mercury", "Python") is asked about before anything is searched; a word that shows which sense is meant ends the doubt.
HOMONYMS: Dict[str, Dict[str, List[str]]] = {
    "mercury": {"the planet": ["planet", "orbit", "solar", "sun", "astronomy", "space"], "the chemical element": ["element", "metal", "toxic", "periodic", "poisoning", "thermometer"],
                "the Roman god": ["god", "roman", "myth", "messenger"], "the singer Freddie Mercury": ["freddie", "queen", "singer", "band"]},
    "python": {"the programming language": ["programming", "code", "language", "library", "script", "software", "pandas", "django", "package"], "the snake": ["snake", "reptile", "constrictor", "zoo"]},
    "java": {"the programming language": ["programming", "code", "language", "jvm", "software", "spring"], "the island of Java": ["island", "indonesia", "jakarta"], "Java coffee": ["coffee"]},
    "jaguar": {"the animal": ["animal", "cat", "predator", "rainforest", "wildlife"], "the car maker": ["car", "cars", "vehicle", "engine", "motor"]},
    "apple": {"the technology company": ["iphone", "mac", "company", "ios", "tim", "cook", "stock", "ipad"], "the fruit": ["fruit", "tree", "orchard", "eat", "cider", "pie"]},
    "amazon": {"the rainforest and river": ["river", "rainforest", "brazil", "forest", "basin"], "the company": ["company", "aws", "prime", "retail", "bezos", "ecommerce"]},
    "washington": {"the US capital": ["dc", "capital", "congress", "white"], "the US state": ["state", "seattle", "olympia"], "George Washington": ["george", "president", "revolution", "founding"]},
    "mars": {"the planet": ["planet", "rover", "nasa", "orbit", "red", "space"], "the god of war": ["god", "roman", "myth"], "the company": ["chocolate", "company", "candy"]},
    "venus": {"the planet": ["planet", "orbit", "space", "atmosphere"], "the Roman goddess": ["goddess", "roman", "myth", "love"]},
    "turkey": {"the country": ["country", "ankara", "istanbul", "ottoman", "republic"], "the bird": ["bird", "thanksgiving", "poultry", "roast"]},
    "cell": {"the biological cell": ["biology", "membrane", "nucleus", "organelle", "dna", "tissue"], "the prison cell": ["prison", "jail", "inmate"], "the battery cell": ["battery", "electrode", "lithium"]},
    "bank": {"the financial institution": ["money", "loan", "deposit", "finance", "account"], "the river bank": ["river", "erosion", "stream"]},
}
# Words that point at the person's own material rather than at a topic.
DOCUMENT_WORDS = re.compile(r"\b(?:this|my|attached|uploaded|above)\s+(?:[\w\-]+\s+){0,3}?(?:document|paper|file|pdf|article|report|chapter|text|study|dataset|spreadsheet|table|book|essay|thesis|manuscript)\b|"
                            r"\b(?:in|from|of|according to|summari[sz]e|read|reading)\s+the\s+(?:document|paper|file|pdf|report|upload)\b|\buploaded\b", re.I)
SCHOLARLY_PAPER = re.compile(r"\b(paper|preprint|arxiv|study|manuscript|thesis|article)\b", re.I)

TASKS: List[Tuple[str, str]] = [
    ("comparison", r"\b(compare|compared|comparison|versus|vs\.?|difference between|differences between|similarit(?:y|ies) between|contrast|better than|\w+er than|how does .{3,60} differ from)\b"),
    ("methodology", r"\b(methodology|methods?|approach|architecture|experimental setup|training procedure|how (?:did|do) the authors|implementation details|algorithm used|techniques? used)\b"),
    ("statistics", r"\b(how many|how much|what percentage|what share|statistics|statistical|rate of|number of|average|median|per cent|percent|figures? for|figures on|gdp|population of)\b"),
    ("causes", r"\b(why (?:did|was|were|do|does|is|are)|what caused|causes? of|reasons? (?:for|why|behind)|what led to|origins? of|how did .{3,80} (?:happen|begin|start|come about|come to)|root causes?)\b"),
    ("consequences", r"\b(consequences?|effects? of|impacts? of|results? of|what happened (?:after|next)|legacy|outcome|aftermath|implications?|what followed|influence of)\b"),
    ("document", r"\b(summari[sz]e|what does|according to)\b.{0,40}\b(this|the|my)\s+(document|paper|file|pdf|report)\b"),
    ("overview", r"\b(how (?:was|were|is|are)|what (?:was|were|is|are)|tell me about|describe|overview|explain|outline|what do we know|who (?:was|were|is|are)|background)\b"),
]
DOMAIN_CUES: Dict[str, List[str]] = {
    "history": """king queen emperor empress monarch monarchy reign kingdom empire dynasty revolution republic medieval ancient colonial feudal absolutism regime ancien century crusade treaty
        napoleon estates assembly bourbon tsar pharaoh renaissance reformation enlightenment industrial war battle conquest independence abolition parlement aristocracy nobility clergy peasantry""".split(),
    "natural_science": """physics chemistry biology quantum particle molecule molecular gene genome protein cell enzyme climate astronomy galaxy planet atom atomic alloy magnetic crystal spectroscopy
        thermodynamics neuron species evolution ecosystem geology laser photon electron superconductor semiconductor""".split(),
    "mathematics": """theorem lemma proof conjecture manifold topology algebra geometry calculus equation matrix eigenvalue probability combinatorics graph polynomial integral derivative""".split(),
    "computing": """machine learning neural network deep learning algorithm software model transformer training dataset gpu api programming database compiler cryptography blockchain cybersecurity python java javascript sql linux
        reinforcement classifier embedding llm architecture inference benchmark""".split(),
    "economics": """gdp inflation unemployment monetary fiscal market stock bond interest trade tariff recession supply demand wage productivity economy economic finance financial taxation""".split(),
    "health": """disease clinical patient therapy drug vaccine symptom diagnosis cancer diabetes cardiac surgery epidemic pandemic nutrition mental""".split(),
    "arts": """film movie television series album song music novel painting sculpture poetry theatre opera furniture fashion architecture style design""".split(),
    "geography": """river mountain ocean island climate population capital city region border""".split(),
}
SCHOLARLY_DOMAINS = frozenset({"natural_science", "mathematics", "computing", "economics"})


# ------------------------------------------------------------------------------------------------------------------------------- data classes
@dataclass(frozen=True)
class Entity:
    kind: str                         # ruler | place | named
    name: str                         # display form, e.g. "Louis XVI"
    key: str                          # identity key, e.g. "louis#16"
    family: str = ""                  # "louis" for every Louis; entities of one family with different numbers are siblings, never the same
    number: Optional[int] = None
    aliases: Tuple[str, ...] = ()
    role: str = "subject"             # subject | context

    @property
    def identity(self) -> bool:
        return self.number is not None

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "key": self.key, "family": self.family, "number": self.number, "aliases": list(self.aliases), "role": self.role}


@dataclass
class Period:
    start: Optional[int] = None
    end: Optional[int] = None
    relation: str = "during"          # during | before | after
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"start": self.start, "end": self.end, "relation": self.relation, "text": self.text}


@dataclass
class Clarification:
    reason: str
    message: str
    options: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"needed": True, "reason": self.reason, "message": self.message, "options": list(self.options)}


@dataclass
class Frame:
    question: str
    entities: List[Entity] = field(default_factory=list)
    period: Optional[Period] = None
    domain: str = "general"
    domain_scores: Dict[str, int] = field(default_factory=dict)
    task: str = "overview"
    refers_to_document: bool = False
    arxiv_ids: List[str] = field(default_factory=list)
    quoted_titles: List[str] = field(default_factory=list)
    clarification: Optional[Clarification] = None

    @property
    def subjects(self) -> List[Entity]:
        return [e for e in self.entities if e.role == "subject"]

    @property
    def identities(self) -> List[Entity]:
        return [e for e in self.entities if e.identity]

    @property
    def places(self) -> List[Entity]:
        return [e for e in self.entities if e.kind == "place"]

    @property
    def scholarly(self) -> bool:
        return self.domain in SCHOLARLY_DOMAINS

    def to_dict(self) -> Dict[str, Any]:
        return {"entities": [e.to_dict() for e in self.entities], "period": self.period.to_dict() if self.period else None, "domain": self.domain, "task": self.task,
                "refers_to_document": self.refers_to_document, "arxiv_ids": list(self.arxiv_ids), "quoted_titles": list(self.quoted_titles),
                "clarification": self.clarification.to_dict() if self.clarification else None}


# ------------------------------------------------------------------------------------------------------------------------------- aliases and matching
def regnal_aliases(name: str, number: int) -> Tuple[str, ...]:
    roman = int_to_roman(number)
    word = ordinal_word(number)
    out = [f"{name} {roman}", f"{name} {number}", f"{name} {number}{_ordinal_suffix(number)}", f"{name} {roman}th", f"{name} the {word.capitalize()}" if word else "", f"{name} the {number}{_ordinal_suffix(number)}"]
    return tuple(dict.fromkeys(a for a in out if a))


def _alias_pattern(alias: str) -> str:
    """A regex that finds the alias as whole words and never as the start of a longer numeral ('Louis XVI' must not match inside 'Louis XVIII')."""
    parts = [re.escape(fold(p)) for p in alias.split()]
    return r"(?<![a-z0-9])" + r"\s+".join(parts) + r"(?![a-z0-9])"


def alias_regex(entity: Entity) -> "re.Pattern[str]":
    return re.compile("|".join(_alias_pattern(a) for a in sorted(set(entity.aliases) | {entity.name}, key=len, reverse=True)))


def mentions(text: str, entity: Entity) -> int:
    """How many times the entity is named in the text (accent- and case-insensitive; a sibling's name does not count)."""
    return len(alias_regex(entity).findall(fold(text)))


def _family_regex(family: str) -> "re.Pattern[str]":
    word = re.escape(fold(family))
    ords = "|".join(re.escape(w) for w in ORDINAL_WORDS)
    return re.compile(rf"(?<![a-z0-9]){word}\s+(?:the\s+)?(?P<num>[ivxlcdm]{{1,8}}(?![a-z0-9])|\d{{1,2}}(?:st|nd|rd|th)?(?![a-z0-9])|(?:{ords})(?![a-z]))")


def numbers_named(text: str, family: str) -> List[int]:
    """The numbers of every '<family> <number>' in the text, in order of appearance: 'Louis XVIII ... Louis XVI' -> [18, 16]."""
    out: List[int] = []
    for m in _family_regex(family).finditer(fold(text)):
        raw = m.group("num")
        n: Optional[int]
        if raw.isdigit() or re.match(r"^\d", raw):
            n = int(re.sub(r"\D", "", raw))
        elif raw in ORDINAL_WORDS:
            n = ORDINAL_WORDS.index(raw) + 1
        else:
            n = roman_to_int(raw)
        if n:
            out.append(n)
    return out


# ------------------------------------------------------------------------------------------------------------------------------- reading the question
_NAMES_ALT = "|".join(sorted(REGNAL_NAMES, key=len, reverse=True))
_TITLES_ALT = "|".join(sorted(TITLE_WORDS, key=len, reverse=True))
_ORD_ALT = "|".join(re.escape(w) for w in sorted(ORDINAL_WORDS, key=len, reverse=True))
REGNAL_RX = re.compile(rf"(?<![A-Za-z])(?:(?P<title>{_TITLES_ALT})\.?\s+)?(?P<name>{_NAMES_ALT})\s+(?:the\s+)?(?P<num>[IVXLCDMivxlcdm]{{1,8}}|\d{{1,2}}(?:st|nd|rd|th)?|{_ORD_ALT})(?![A-Za-z0-9])", re.I)
BARE_REGNAL_RX = re.compile(rf"(?<![A-Za-z])(?:(?P<title>{_TITLES_ALT})\.?\s+)?(?P<name>{_NAMES_ALT})(?![A-Za-z])", re.I)
YEAR = re.compile(r"(?<![\w.])(1[0-9]{3}|20[0-4][0-9])(?![\w])|(?:\bin|\bby|\bsince|\baround|\bcirca|\bc\.|\bAD|\bCE)\s+(\d{3})(?![\w%])", re.I)
RANGE = re.compile(r"(?<![\w.])(1[0-9]{3}|20[0-4][0-9])\s*(?:-|–|—|to|and|until)\s*(1[0-9]{3}|20[0-4][0-9])(?![\w])", re.I)
CENTURY = re.compile(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)[\s-]+century\b|\b(?:the\s+)?(" + "|".join(ORDINAL_WORDS[:21]) + r")[\s-]+century\b", re.I)
QUOTED = re.compile(r"[\"“‘']([^\"”’'\n]{8,160})[\"”’']")


def _number_of(raw: str, *, source_upper: bool) -> Optional[int]:
    r = raw.strip()
    low = r.lower()
    if low in ORDINAL_WORDS:
        return ORDINAL_WORDS.index(low) + 1
    if re.match(r"^\d", r):
        n = int(re.sub(r"\D", "", r))
        return n if 1 <= n <= 40 else None
    if len(r) == 1 and not source_upper:
        return None                                                                         # a lone lower-case 'i', 'v' or 'x' is an ordinary word, not a numeral
    n = roman_to_int(r)
    return n if n and n <= 40 else None


def _regnal_entities(text: str) -> Tuple[List[Entity], List[Tuple[int, int]]]:
    found: List[Entity] = []
    spans: List[Tuple[int, int]] = []
    seen = set()
    for m in REGNAL_RX.finditer(text):
        n = _number_of(m.group("num"), source_upper=m.group("num").isupper())
        if n is None:
            continue
        name = m.group("name").capitalize()
        key = f"{name.lower()}#{n}"
        spans.append(m.span())
        if key in seen:
            continue
        seen.add(key)
        title = (m.group("title") or "").capitalize()
        aliases = list(regnal_aliases(name, n))
        if title:
            aliases += [f"{title} {a}" for a in aliases[:2]]
        found.append(Entity("ruler", f"{name} {int_to_roman(n)}", key, name.lower(), n, tuple(dict.fromkeys(aliases))))
    return found, spans


def _in_spans(pos: int, spans: List[Tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def _capitalised_runs(text: str, skip: List[Tuple[int, int]]) -> List[str]:
    words = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[^\W\d_][\w'’\-]*|\d+", text) if not _in_spans(m.start(), skip)]
    runs: List[List[str]] = []
    cur: List[str] = []
    prev_end = -1

    def flush() -> None:
        while cur and cur[-1].lower() in CONNECTORS:
            cur.pop()
        if cur:
            runs.append(list(cur))
        cur.clear()
    for i, (w, s, e) in enumerate(words):
        gap = text[prev_end:s] if prev_end >= 0 else ""
        breaks = prev_end >= 0 and bool(re.search(r"[,;:.?!()\[\]]", gap))
        sentence_start = i == 0 or bool(re.search(r"[.?!]\s*$", text[:s]))
        cap = w[0].isupper() and not (len(w) > 1 and w.isupper() and len(w) <= 3 and w.upper() not in COUNTRIES)
        if breaks:
            flush()
        if cap and not (sentence_start and w.lower() in LEADING_WORDS) and w.lower() not in LEADING_WORDS:
            cur.append(w)
        elif cur and w.lower() in CONNECTORS and i + 1 < len(words) and words[i + 1][0][0].isupper() and not _in_spans(words[i + 1][1], skip):
            cur.append(w)
        else:
            flush()
        prev_end = e
    flush()
    return [" ".join(r) for r in runs]


ARXIV_TEXT = re.compile(r"(?:arxiv[:\s]*)?\b\d{4}\.\d{4,5}(?:v\d+)?\b|\b(?:arxiv\.org|doi\.org)/\S+|(?<![\w/])[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?", re.I)


def _plain(question: str) -> str:
    """The question without quoted titles and identifiers: they are looked up as they are written and are not names to be found in running text."""
    text = ARXIV_TEXT.sub(" ", question or "")
    text = QUOTED.sub(" ", text)
    return " ".join(text.split())


SOURCE_NAMES = frozenset("wikipedia arxiv searxng google bing duckduckgo web internet online pubmed youtube twitter reddit".split())              # where to look, not what the question is about


def extract_entities(question: str) -> List[Entity]:
    text = _plain(question)
    rulers, spans = _regnal_entities(text)
    out: List[Entity] = list(rulers)
    seen = {e.key for e in out}
    for run in _capitalised_runs(text, spans):
        run = re.sub(r"['’]s\b", "", run)
        low = fold(run)
        tokens = low.split()
        while tokens and tokens[0] in TITLE_WORDS:
            tokens = tokens[1:]
        if not tokens:
            continue
        name = " ".join(w.capitalize() if w not in CONNECTORS else w for w in run.split() if fold(w) not in TITLE_WORDS)
        core = " ".join(tokens)
        if core in DEMONYMS and len(tokens) == 1:
            place = DEMONYMS[core]
            if place in seen:
                continue
            seen.add(place)
            out.append(Entity("place", place.capitalize(), place, "", None, (place.capitalize(),), "context"))
            continue
        if core in COUNTRIES:
            if core not in seen:
                seen.add(core)
                out.append(Entity("place", name, core, "", None, (name,), "context"))
            continue
        if core in SOURCE_NAMES:
            continue
        if len(core) < 3 or core in seen or (len(tokens) == 1 and tokens[0] in REGNAL_NAMES):
            continue                                                                       # a bare 'Louis' is a name still to be pinned down (see find_clarification), not an entity
        seen.add(core)
        aliases = [name]
        if " of " in name.lower():
            aliases.append(re.sub(r"\s+of\s+the\s+|\s+of\s+", " ", name, flags=re.I))
        out.append(Entity("named", name, core, "", None, tuple(dict.fromkeys(aliases)), "subject"))
    return out


def extract_period(question: str) -> Optional[Period]:
    text = _plain(question)
    m = RANGE.search(text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return Period(min(a, b), max(a, b), "during", m.group(0))
    c = CENTURY.search(text)
    if c:
        n = int(c.group(1)) if c.group(1) else ORDINAL_WORDS.index(c.group(2).lower()) + 1
        return Period((n - 1) * 100 + 1, n * 100, "during", c.group(0))
    y = YEAR.search(text)
    if y:
        year = int(y.group(1) or y.group(2))
        before = re.search(rf"\b(before|prior to|until|up to|leading up to|on the eve of)\s+(?:the\s+)?(?:year\s+)?{year}", text, re.I)
        after = re.search(rf"\b(after|since|following|from)\s+(?:the\s+)?(?:year\s+)?{year}", text, re.I)
        return Period(None if before else year, year if before else (None if after else year), "before" if before else "after" if after else "during", y.group(0))
    return None


def classify_domain(question: str, entities: List[Entity], period: Optional[Period]) -> Tuple[str, Dict[str, int]]:
    words = set(re.findall(r"[a-z]+", fold(question)))
    low = fold(question)
    scores: Dict[str, int] = {}
    for domain, cues in DOMAIN_CUES.items():
        n = 0
        for cue in cues:
            if " " in cue:
                n += 2 if cue in low else 0
            elif cue in words:
                n += 1
        if n:
            scores[domain] = n
    if any(e.kind == "ruler" for e in entities):
        scores["history"] = scores.get("history", 0) + 3
    if period and (period.start or period.end) and min(x for x in (period.start, period.end) if x) < 1900:
        scores["history"] = scores.get("history", 0) + 2
    demonym_hist = {"french revolution", "american revolution", "industrial revolution", "world war", "roman empire", "cold war", "middle ages"}
    if any(d in low for d in demonym_hist):
        scores["history"] = scores.get("history", 0) + 3
    if not scores:
        return "general", {}
    best = max(scores.items(), key=lambda kv: (kv[1], kv[0] == "history"))
    return best[0], scores


def classify_task(question: str, *, entities: Optional[List[Entity]] = None) -> str:
    low = " ".join((question or "").lower().split())
    for name, rx in TASKS:
        if re.search(rx, low):
            if name == "comparison" and not re.search(r"\b(compare|versus|vs\.?|difference|differ|similar|contrast|than)\b", low):
                continue
            return name
    return "overview"


# ------------------------------------------------------------------------------------------------------------------------------- clarification
def _bare_regnal(text: str, has_numbered: set) -> Optional[Tuple[str, str]]:
    """('Louis', title) when a regnal name stands alone in a context that names a ruler ('under Louis', 'King Louis', 'the reign of Louis')."""
    for m in BARE_REGNAL_RX.finditer(text):
        name = m.group("name")
        after = text[m.end():m.end() + 24]
        if re.match(r"\s*(?:the\s+)?(?:[IVXLCDM]{1,8}\b|\d{1,2}(?:st|nd|rd|th)?\b|(?:" + _ORD_ALT + r")\b)", after, re.I):
            continue                                                                       # a numbered ruler: handled as an entity
        if re.match(r"\s+(?:of\s+[A-Z]\w+\s+)?(?:[A-Z][a-zé]+)", after) and not re.match(r"\s+of\b", after):
            continue                                                                       # 'Louis Pasteur', 'Charles Darwin': a person's full name, not a regnal name
        before = text[max(0, m.start() - 40):m.start()].lower()
        titled = bool(m.group("title")) or bool(re.search(r"\b(under|reign of|rule of|era of|age of|during the time of|the court of|time of)\s*(?:king\s+)?$", before))
        if titled and name.lower() not in has_numbered:
            return name.capitalize(), (m.group("title") or "")
    return None


def find_clarification(question: str, entities: List[Entity], period: Optional[Period], *, task: str, refers_to_document: bool, has_documents: bool, arxiv_ids: List[str],
                       quoted_titles: List[str]) -> Optional[Clarification]:
    text = " ".join((question or "").split())
    numbered_families = {e.family for e in entities if e.identity}
    bare = _bare_regnal(text, numbered_families)
    if bare and not (period and (period.start or period.end)):
        name = bare[0]
        key = name.lower()
        france = bool(re.search(r"\b(france|french|paris|versailles)\b", text, re.I))
        opts = []
        if key in KNOWN_RULERS and france:
            for n, years in KNOWN_RULERS[key]:
                rn = int_to_roman(n)
                opts.append({"label": f"{name} {rn} ({years})", "question": re.sub(rf"\b{name}\b", f"{name} {rn}", text, count=1, flags=re.I)})
        return Clarification("ambiguous_ruler", f"“{name}” alone does not say which ruler you mean: several kings and queens shared that name. Which one should be researched?" + ("" if opts else f" Add the number, for example “{name} II”, or a period."), opts)
    low = fold(text)
    words = set(re.findall(r"[a-z]+", low))
    for term, senses in HOMONYMS.items():
        if term in words and not any(e.identity for e in entities):
            others = {w for s, ws in senses.items() for w in ws}
            named_with_context = any(e.name.lower() != term and term in e.name.lower().split() for e in entities)           # 'Mercury Records', 'Apple Inc': part of a longer name
            if not (words & others) and not named_with_context and len(words - {term}) <= 12 and not entities_other_than(entities, term):
                return Clarification("ambiguous_term", f"“{term.capitalize()}” can mean several different things. Which do you mean?",
                                     [{"label": s[:1].upper() + s[1:], "question": f"{text.rstrip('?.! ')} ({s})"} for s in senses])
    if refers_to_document and not has_documents and not arxiv_ids and not quoted_titles:
        asked = "paper" if re.search(r"\bpaper|preprint|study|manuscript|thesis\b", text, re.I) else "document"
        return Clarification("no_document", f"The question refers to “this {asked}”, but no file is selected for this research and no title or arXiv identifier is given. Attach the file to the project and select it, "
                                            "or write the title or the arXiv identifier (for example arXiv:2101.00001) in the question.", [])
    return None


def entities_other_than(entities: List[Entity], term: str) -> bool:
    return any(e.name.lower() != term.lower() and e.kind != "place" for e in entities)


# ------------------------------------------------------------------------------------------------------------------------------- the frame
def understand(question: str, *, has_documents: bool = False) -> Frame:
    q = " ".join((question or "").split())
    entities = extract_entities(q)
    period = extract_period(q)
    domain, scores = classify_domain(q, entities, period)
    task = classify_task(q, entities=entities)
    refers = bool(DOCUMENT_WORDS.search(q))
    if refers and task in ("overview", "document") and has_documents and re.search(r"\b(summari[sz]e|what does|according to|in (?:the|this|my))\b", q, re.I):
        task = "document"
    ids: List[str] = []
    found = arxiv_id_in(q)
    if found:
        ids.append(found)
    titles = [t.strip() for t in QUOTED.findall(q) if len(t.split()) >= 3]
    clar = find_clarification(q, entities, period, task=task, refers_to_document=refers, has_documents=has_documents, arxiv_ids=ids, quoted_titles=titles)
    if task == "comparison" and len([e for e in entities if e.role == "subject"]) < 2 and not clar and not has_documents:
        pass                                                                                # the planner explains this case; it is not a reason to stop
    return Frame(q, entities, period, domain, scores, task, refers, ids, titles, clar)
