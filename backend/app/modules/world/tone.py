"""Tone of wording, from a small hand-made word list.

Tone is how an article is worded (alarming, upbeat), NOT whether it is true, important or fair. A flood report is negative in tone and may be perfectly accurate; a promotional article can be
positive and wrong. The result lists the words that produced it. Negation ("not good") flips a word, intensifiers ("very") strengthen it, and an article with many positive and negative
words is called "mixed" rather than averaged into neutral."""
from __future__ import annotations

import re
from typing import Any, Dict, List

METHOD = "word-list-tone/1"
POSITIVE = set("""good great better best strong gain gains growth grow improved improve improves improvement success successful win wins won boost boosts rally recover recovery rescued rescue relief
progress agreement agree deal peace safe safely benefit benefits helped help support supported welcome welcomed praise praised hope optimistic stable steady strengthen strengthens
breakthrough celebrate celebrated approve approved launch launched award awarded cure resolve resolved restore restored reopen reopened ease eased calm""".split())
NEGATIVE = set("""bad worse worst weak loss losses fall falls fell decline declines declined crash crisis collapse collapsed fail failed failure kill kills killed dead death deaths died injured injuries damage damaged
destroyed threat threatens threatened fear fears warn warns warned warning attack attacked clash clashes violence violent protest protests dispute disputed conflict war flood floods flooded
evacuated evacuate shortage shortages ban banned fraud scandal accused arrest arrested cancel cancelled delay delayed risk risks concern concerns worried worry fire fires blaze drought
outbreak epidemic surge surges plunge plunged slump slumped lawsuit penalty fined sanction sanctions missing trapped stranded""".split())
NEGATORS = {"not", "no", "never", "without", "hardly", "barely", "neither", "nor", "cannot", "isn't", "wasn't", "aren't", "weren't", "doesn't", "didn't", "won't", "don't"}
BOOSTERS = {"very", "extremely", "severely", "sharply", "highly", "significantly", "massive", "huge", "major", "serious", "seriously", "heavy", "heavily"}


def score(title: str, summary: str) -> Dict[str, Any]:
    """{label, score, positive, negative, basis, method, note}; score is in [-1, 1]. `basis` lists the words (negation-aware) that produced it."""
    toks: List[str] = re.findall(r"[a-z][a-z'’]+", f"{title}. {title}. {summary}".lower().replace("’", "'"))
    pos = neg = 0.0
    pos_words: List[str] = []
    neg_words: List[str] = []
    for i, w in enumerate(toks):
        polarity = 1 if w in POSITIVE else -1 if w in NEGATIVE else 0
        if not polarity:
            continue
        window = toks[max(0, i - 3):i]
        if any(x in NEGATORS for x in window):
            polarity = -polarity
            tag = f"not {w}"
        else:
            tag = w
        weight = 1.5 if any(x in BOOSTERS for x in window) else 1.0
        if polarity > 0:
            pos += weight
            pos_words.append(tag)
        else:
            neg += weight
            neg_words.append(tag)
    total = pos + neg
    value = round((pos - neg) / (total + 2.0), 3)                                             # +2 keeps one stray word from looking extreme
    if total >= 4 and min(pos, neg) / total >= 0.35:
        label = "mixed"
    elif value >= 0.2:
        label = "positive"
    elif value <= -0.2:
        label = "negative"
    else:
        label = "neutral"
    return {"label": label, "score": value, "positive": round(pos, 1), "negative": round(neg, 1), "basis": {"positive": sorted(set(pos_words))[:8], "negative": sorted(set(neg_words))[:8]},
            "method": METHOD, "note": "Tone describes how the text is worded, not whether it is true or important."}
