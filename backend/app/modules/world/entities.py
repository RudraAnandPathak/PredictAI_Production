"""Entity extraction by capitalisation and cue-word rules.

Method "capitalisation-heuristic/1". It finds runs of capitalised words, acronyms, percentages, money and years, and only assigns a kind (person, organisation, place) when a cue word in
the text says so ("Dr", "Ministry of", "in"). Everything else is plainly a "name". It does not look anything up, so it will miss lower-case names, split some names and wrongly keep a few
capitalised phrases; that is why every entity carries the method and why nothing downstream treats an entity as verified."""
from __future__ import annotations

import re
from typing import Any, Dict, List

METHOD = "capitalisation-heuristic/1"
TITLES = {"mr", "mrs", "ms", "dr", "prof", "professor", "minister", "president", "chief", "senator", "judge", "sir", "justice", "governor", "mayor", "secretary", "ceo"}
ORG_WORDS = {"inc", "ltd", "llc", "corp", "bank", "university", "ministry", "council", "commission", "association", "party", "agency", "authority", "department", "institute", "committee",
             "company", "group", "organisation", "organization", "court", "police", "board", "union", "fund", "service", "services", "trust", "forum"}
PLACE_CUES = {"in", "at", "near", "from", "across", "outside", "around", "to"}
NOT_NAMES = {"the", "a", "an", "this", "that", "these", "those", "it", "he", "she", "they", "we", "i", "his", "her", "their", "our", "its", "monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "on", "but", "and", "or", "if", "when",
             "after", "before", "while", "as", "for", "with", "by", "of", "however", "meanwhile", "according", "officials", "residents", "police", "reuters", "ap", "afp", "news", "photo", "image",
             "read", "also", "more", "new", "all", "some", "many", "no", "one", "two", "three", "there", "here", "what", "why", "how", "who", "where", "which", "so", "then", "now", "today"}
_WORD = r"[A-Z][A-Za-z'’\-]+"
_RUN = re.compile(rf"\b{_WORD}(?:\s+(?:of|the|for|and|de|van|von|al|bin|&)?\s*{_WORD})*\b")
_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")
_PERCENT = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?\s?%")
_MONEY = re.compile(r"(?:₹|\$|€|£|rs\.?|inr|usd|eur|gbp)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion|crore|lakh|thousand|trillion|bn|m|k))?", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _strip_possessive(s: str) -> str:
    return re.sub(r"['’]s$", "", s).strip(" -")


def extract(text: str, *, limit: int = 20) -> Dict[str, Any]:
    """{entities: [{text, kind, count}], figures: {percentages, money, years}, method, note}. Kinds: person, organisation, place, name, acronym."""
    text = (text or "")[:20000]
    found: Dict[str, Dict[str, Any]] = {}

    def add(raw: str, kind: str, start: int) -> None:
        name = _strip_possessive(raw)
        words = name.split()
        while words and words[0].lower() in NOT_NAMES:
            words = words[1:]
        while words and words[-1].lower() in NOT_NAMES:
            words = words[:-1]
        if not words:
            return
        name = " ".join(words)
        if len(name) < 2 or name.lower() in NOT_NAMES:
            return
        at_sentence_start = start == 0 or text[max(0, start - 2):start].strip(" \"'(") in ("", ".", "!", "?", ":")
        key = name.lower()
        entry = found.setdefault(key, {"text": name, "kind": kind, "count": 0, "_inner": 0})
        entry["count"] += 1
        if not at_sentence_start or len(words) > 1:
            entry["_inner"] += 1
        order = {"person": 4, "organisation": 3, "place": 2, "acronym": 1, "name": 0}
        if order[kind] > order[entry["kind"]]:
            entry["kind"] = kind

    for m in _RUN.finditer(text):
        raw = m.group(0)
        words = raw.split()
        before = re.findall(r"[A-Za-z]+", text[max(0, m.start() - 24):m.start()])
        cue = before[-1].lower() if before else ""
        kind = "name"
        if words[0].rstrip(".").lower() in TITLES and len(words) > 1:                           # "Dr Rao": the title is a cue, not part of the name
            kind, raw, start = "person", " ".join(words[1:]), m.start() + len(words[0]) + 1
        elif cue in TITLES:
            kind = "person"
        elif any(w.lower() in ORG_WORDS for w in words):
            kind = "organisation"
        elif cue in PLACE_CUES and len(words) <= 3:
            kind = "place"
        add(raw, kind, start if kind == "person" and words[0].rstrip(".").lower() in TITLES else m.start())
    for m in _ACRONYM.finditer(text):
        add(m.group(0), "acronym", m.start())
    entities = []
    for e in found.values():
        single_at_start = len(e["text"].split()) == 1 and e["_inner"] == 0 and e["kind"] == "name"
        if single_at_start:
            continue                                                                          # a lone capitalised word that only ever opens a sentence is just a sentence
        entities.append({"text": e["text"], "kind": e["kind"], "count": e["count"]})
    entities.sort(key=lambda e: (-e["count"], e["text"].lower()))
    return {"entities": entities[:limit], "figures": {"percentages": sorted({re.sub(r"\s", "", x) for x in _PERCENT.findall(text)})[:10], "money": sorted({x.strip() for x in _MONEY.findall(text)})[:10],
                                                       "years": sorted(set(_YEAR.findall(text)))[:10]}, "method": METHOD,
            "note": "Found by capitalisation and cue words, not looked up anywhere. Treat as hints; kinds are only given when the text says so."}


def entity_keys(derived: Dict[str, Any]) -> List[str]:
    """Lower-case entity names for matching articles to each other."""
    return [e["text"].lower() for e in (derived or {}).get("entities", []) if e["kind"] != "acronym" or len(e["text"]) >= 3]
