"""Tokenising, light stemming, sentence splitting and number extraction. Deterministic, English-leaning, safe on any Unicode input.
`stem` is a plain suffix stripper (not the Porter algorithm); it only needs to map obvious inflections to one form so that "rivers" matches "river"."""
from __future__ import annotations

import re
from typing import List, Set

STOPWORDS = frozenset("a an the and or of to in on for with by from at as is are was were be been being this that these those it its into about what which who whom how why when "
                      "where does do did can could should would will shall may might than then there their them they you your we our i me my not no yes also over under between "
                      "among per via versus vs has have had having but if so such more most other some any each both either neither very just only own same too s t".split())

_TOKEN = re.compile(r"\d+(?:\.\d+)+|[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)                 # 2.4 stays one token
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")
_ABBREV = frozenset("mr mrs ms dr prof sr jr st vs etc e.g i.e fig eq no al cf approx inc ltd co".split())


def stem(word: str) -> str:
    w = word
    if len(w) <= 3 or not w.isascii() or not w.isalpha():
        return w
    for suffix, repl, minlen in (("fulness", "ful", 8), ("iveness", "ive", 8), ("ingly", "", 7), ("ments", "ment", 7), ("ities", "ity", 6), ("ness", "", 6), ("ings", "", 6), ("ies", "y", 5), ("sses", "ss", 5),
                                 ("ing", "", 6), ("edly", "", 7), ("ed", "", 5), ("ly", "", 6), ("es", "", 5), ("s", "", 4)):
        if w.endswith(suffix) and len(w) >= minlen and not (suffix == "s" and w.endswith(("ss", "us", "is"))):
            w = w[: -len(suffix)] + repl
            break
    if len(w) > 3 and w.endswith("e"):                                            # rise/rising -> ris, measure/measured -> measur
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeioulsz":                 # running -> runn -> run
        w = w[:-1]
    return w


def tokenize(text: str, *, stemming: bool = True, drop_stopwords: bool = True, min_len: int = 2) -> List[str]:
    out: List[str] = []
    for raw in _TOKEN.findall(_THOUSANDS.sub("", (text or "").lower())):
        t = raw.replace("’", "'").strip("'-")
        if len(t) < min_len or (drop_stopwords and t in STOPWORDS):
            continue
        out.append(stem(t) if stemming else t)
    return out


def content_terms(query: str, limit: int = 6) -> List[str]:
    """Distinct unstemmed content words of a short query, in order (used to build provider queries)."""
    out: List[str] = []
    for t in tokenize(query, stemming=False, min_len=3):
        if t not in out:
            out.append(t)
    return out[:limit]


def sentences(text: str, max_len: int = 600) -> List[str]:
    """Split on . ! ? followed by space and a capital/digit/quote, without splitting abbreviations or decimals."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts: List[str] = []
    start = 0
    for m in re.finditer(r"[.!?]+[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])", text):
        end = m.end()
        before = text[start:m.start() + 1]
        last = re.findall(r"[A-Za-z.]+$", before.rstrip(".!?"))
        if last and last[-1].lower().rstrip(".") in _ABBREV:
            continue
        if re.search(r"\b[A-Z]\.$", before):                                      # an initial: "J. Smith"
            continue
        parts.append(text[start:end].strip())
        start = end
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    out: List[str] = []
    for p in parts:
        while len(p) > max_len:                                                   # never return an unbounded run-on sentence
            cut = p.rfind(" ", 0, max_len)
            cut = cut if cut > max_len // 2 else max_len
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def numbers_in(text: str) -> Set[str]:
    """Numbers as normalised strings ('1,200' -> '1200', '5.0' -> '5', '12%' -> '12%')."""
    out: Set[str] = set()
    for m in _NUMBER.findall(text or ""):
        pct = m.endswith("%")
        n = m.rstrip("%").replace(",", "").lstrip("+")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n + ("%" if pct else ""))
        if pct:
            out.add(n)
    return out


def sentence_spans(text: str, max_chars: int = 700):
    """(start, end) offsets of sentences in the ORIGINAL text, so that text[start:end] is always an exact quote. Paragraph breaks always split; abbreviations and initials do not."""
    text = text or ""
    spans = []
    para_start = 0
    boundaries = [m for m in re.finditer(r"\n[ \t]*\n+", text)]
    paragraphs = []
    for m in boundaries:
        paragraphs.append((para_start, m.start()))
        para_start = m.end()
    paragraphs.append((para_start, len(text)))
    for ps, pe in paragraphs:
        chunk = text[ps:pe]
        start = 0
        pieces = []
        for m in re.finditer(r"[.!?]+[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])", chunk):
            before = chunk[start:m.start() + 1]
            last = re.findall(r"[A-Za-z.]+$", before.rstrip(".!?"))
            if last and last[-1].lower().rstrip(".") in _ABBREV:
                continue
            if re.search(r"\b[A-Z]\.$", before):
                continue
            pieces.append((start, m.end()))
            start = m.end()
        pieces.append((start, len(chunk)))
        for a, b in pieces:
            while b - a > max_chars:                                                  # a run-on with no sentence end: cut at a space, never mid-word
                cut = chunk.rfind(" ", a, a + max_chars)
                cut = cut if cut > a + max_chars // 2 else a + max_chars
                spans.append((ps + a, ps + cut))
                a = cut
            spans.append((ps + a, ps + b))
    out = []
    for a, b in spans:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            out.append((a, b))
    return out
