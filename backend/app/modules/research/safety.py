"""Treating retrieved text as data, and keeping private material off the wire.

1. Injection scan. Documents, web pages and news can contain sentences written to steer an AI ("ignore your instructions", "reveal your system prompt"). PredictAI never obeys
   text it retrieves: it only quotes it. As a second layer, sentences that look like instructions are flagged, shown to the person, and kept out of findings and model prompts.
2. Outbound query filter. A web search is sent to someone else's server. Only the question the person typed (and the plan they approved) may leave: no document text, no emails,
   phone or account-like numbers, no key-like strings. A query that shares a run of words with the person's private material is refused."""
from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from ..textkit import tokenize

_FLAGS = re.I | re.S
INJECTION: List[Tuple["re.Pattern[str]", str]] = [(re.compile(p, _FLAGS | (re.M if m else 0)), label) for p, label, m in [
    (r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|any|system|these|your)\b.{0,30}\b(instructions?|prompts?|rules?|messages?|directions?|guidelines?)\b", "tells the reader to ignore its instructions", 0),
    (r"\b(reveal|show|print|repeat|leak|output|disclose)\b.{0,40}\b(system prompt|hidden prompt|your instructions|your rules|api key|password|secret|token|credentials?)\b", "asks for hidden instructions or secrets", 0),
    (r"\byou are (now|no longer)\b", "tries to change the assistant's role", 0),
    (r"\b(new|updated|additional|real) (instructions?|rules?|task)\b\s*[:\-]", "introduces new instructions", 0),
    (r"^\s*(system|assistant|developer)\s*[:>]", "imitates a system or assistant message", 1),
    (r"</?\s*(system|assistant|instructions?|tool_call|function_call)\b", "contains control markup", 0),
    (r"\[/?(INST|SYS)\]|<\|(im_start|im_end|system|endoftext)\|>", "contains chat-template control tokens", 0),
    (r"\b(do not|don't|never) (tell|inform|mention|reveal to) (the )?(user|human|person|reader)\b", "asks to hide something from the person", 0),
    (r"\b(send|post|upload|forward|exfiltrate|email)\b.{0,60}\b(to|at)\b.{0,10}(https?://|\S+@\S+)", "asks for data to be sent elsewhere", 0),
    (r"!\[[^\]]*\]\(https?://[^)]*\?[^)]*\)", "contains a markdown image that could leak data", 0),
    (r"\b(delete|drop|erase|wipe|remove)\b.{0,30}\b(all|every|the)\b.{0,20}\b(files?|projects?|data|database|documents?|records?|accounts?)\b", "asks for destructive actions", 0),
    (r"\b(call|invoke|run|execute) (the )?(tool|function|command|shell|code|script)\b", "asks the reader to run something", 0),
]]


def scan_sentence(sentence: str) -> List[str]:
    return [label for rx, label in INJECTION if rx.search(sentence)]


def screen(sentences: Sequence[str]) -> Tuple[List[str], List[Dict[str, object]]]:
    """(kept sentences, removed: [{text, reasons}]). Removed text is only ever shown to the person as a warning, never used."""
    kept: List[str] = []
    removed: List[Dict[str, object]] = []
    for s in sentences:
        reasons = scan_sentence(s)
        if reasons:
            removed.append({"text": s[:300], "reasons": reasons})
        else:
            kept.append(s)
    return kept, removed


_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().\-]{8,}\d(?!\w)")
_LONGNUM = re.compile(r"\b\d{9,}\b")
_KEYLIKE = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}\b|\b(?:sk|pk|ghp|xox[bap]|AKIA)[A-Za-z0-9_\-]{12,}\b")
_URLCRED = re.compile(r"https?://[^\s/]*:[^\s/]*@\S+")
MAX_QUERY = 200


def sanitize_query(query: str, max_len: int = MAX_QUERY) -> Tuple[str, List[str]]:
    """Strip anything that should not leave the platform from a search query. Returns (clean query, what was removed). `max_len` is longer for field queries such as arXiv's."""
    removed: List[str] = []
    q = re.sub(r"[\x00-\x1f\x7f]", " ", query or "")
    for rx, label in ((_URLCRED, "a link with a password"), (_EMAIL, "an email address"), (_KEYLIKE, "a key-like string"), (_PHONE, "a phone-like number"), (_LONGNUM, "a long number")):
        if rx.search(q):
            removed.append(label)
            q = rx.sub(" ", q)
    return " ".join(q.split())[:max_len], removed


WINDOW, BAG_N = 14, 5


def shares_private_text(query: str, private_texts: Sequence[str], n: int = 6, content_n: int = 5) -> bool:
    """True when the query repeats a run of the person's own material: n consecutive words as typed, or content_n consecutive content words (stop words ignored on both sides),
    or BAG_N or more of its distinct content words all inside one short stretch (WINDOW content words) of the person's text.
    The second and third forms matter because planned search phrases are cut from the question and stripped of stop words and of words such as 'on Tuesday', so a pasted sentence no longer
    looks like the original word for word, yet most of it would still leave the platform."""
    def grams(words, size):
        return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)} if len(words) >= size else set()

    raw_q = tokenize(query, stemming=False, drop_stopwords=False, min_len=1)
    key_q = tokenize(query, stemming=False, drop_stopwords=True, min_len=1)
    raw_grams, key_grams = grams(raw_q, n), grams(key_q, content_n)
    if not raw_grams and not key_grams:
        return False
    wanted = set(key_q)
    for text in private_texts:
        if raw_grams and raw_grams & grams(tokenize(text, stemming=False, drop_stopwords=False, min_len=1), n):
            return True
        key_t = tokenize(text, stemming=False, drop_stopwords=True, min_len=1)
        if key_grams and key_grams & grams(key_t, content_n):
            return True
        if len(wanted) >= BAG_N:
            for i in range(0, max(1, len(key_t) - WINDOW + 1)):
                if len(wanted & set(key_t[i:i + WINDOW])) >= BAG_N:
                    return True
    return False
