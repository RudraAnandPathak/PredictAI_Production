"""Citation verification. A model's answer is never trusted: every sentence must cite at least one passage that was actually supplied, and the words and numbers of the sentence must be
supported by the cited passage. Sentences that fail are removed (and counted), never repaired or guessed. Quotes shown to the person are exact substrings of the stored passage."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..ingestion.intelligence import tokens

LABEL = re.compile(r"\[(S\d{1,2})\]")
NUM = re.compile(r"\d+(?:[.,]\d+)?")
MIN_SUPPORT = 0.5


def split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|(?<=\])\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if p.strip()]


def best_quote(passage: str, sentence: str, max_len: int = 360) -> str:
    """The sentence of the passage that shares the most content words with the claim (an exact substring of the passage)."""
    want = set(tokens(sentence))
    best, best_score = "", -1.0
    for s in re.split(r"(?<=[.!?])\s+", passage.replace("\n", " ")):
        s = s.strip()
        if len(s) < 15:
            continue
        score = len(want & set(tokens(s))) / (1 + 0.002 * len(s))
        if score > best_score:
            best, best_score = s, score
    out = best or passage.strip()[:max_len]
    return out[:max_len]


def supported(sentence: str, passages: Sequence[str]) -> bool:
    body = LABEL.sub("", sentence)
    content = set(tokens(body))
    joined = " ".join(passages)
    nums = NUM.findall(body)
    if any(n.replace(",", "") not in joined.replace(",", "") for n in nums):
        return False                                                                       # a number that appears in no cited passage is never accepted
    if len(content) < 3:
        return True
    have = set(tokens(joined))
    return len(content & have) / len(content) >= MIN_SUPPORT


@dataclass
class Verified:
    text: str
    kept: List[Tuple[str, List[str]]] = field(default_factory=list)      # (sentence, labels)
    removed: int = 0
    labels: List[str] = field(default_factory=list)


def verify_answer(answer: str, allowed: Dict[str, str]) -> Verified:
    """`allowed` maps a label (S1..) to the passage text it stands for."""
    out = Verified("")
    used: List[str] = []
    lines = []
    for sentence in split_sentences(answer):
        labels = [l for l in dict.fromkeys(LABEL.findall(sentence))]
        valid = [l for l in labels if l in allowed]
        if not valid:
            out.removed += 1
            continue
        if not supported(sentence, [allowed[l] for l in valid]):
            out.removed += 1
            continue
        clean = LABEL.sub(lambda m: m.group(0) if m.group(1) in allowed else "", sentence)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
        out.kept.append((clean, valid))
        lines.append(clean)
        for l in valid:
            if l not in used:
                used.append(l)
    out.text, out.labels = " ".join(lines), used
    return out
