"""What is worked out about one article when it arrives: topics, entities, tone and what kind of statement its headline and lead are. All of it is rule-based and carries its method
name and a version, so a later change in method can be told apart from a change in the news."""
from __future__ import annotations

import re
from typing import Any, Dict

from ..research.evidence import NEGATION, UP, DOWN, statement_kind
from ..textkit import numbers_in, sentences
from . import entities, tone, topics

DERIVED_VERSION = "world-derive/1"
OPINION_TITLE = re.compile(r"^\s*(opinion|editorial|analysis|comment|column|view|explainer|letters?)\b\s*[:|\-–]", re.I)


def lead_of(title: str, summary: str) -> str:
    first = (sentences(summary, 400) or [""])[0]
    return first if first and first.lower() != (title or "").lower() else ""


def derive(title: str, summary: str) -> Dict[str, Any]:
    text = f"{title}. {summary}" if summary else title
    lead = lead_of(title, summary)
    kind_text = f"{title}. {lead}" if lead else title
    kind = "opinion" if OPINION_TITLE.match(title or "") else statement_kind(kind_text, "external_research")
    basis = {"opinion": "the headline is labelled as opinion or analysis", "prediction": "the wording looks ahead (will, expected to, forecast)", "uncertain": "the wording hedges (reportedly, may, according to)",
             "stated": "plain wording, no hedge, forecast or opinion marker found"}[kind if kind in ("opinion", "prediction", "uncertain", "stated") else "stated"]
    if kind == "opinion" and not OPINION_TITLE.match(title or ""):
        basis = "the wording gives a view or a recommendation"
    return {"version": DERIVED_VERSION, "topics": topics.classify(title, summary), "entities": entities.extract(text), "tone": tone.score(title, summary),
            "statement": {"kind": kind, "basis": basis, "negated": bool(NEGATION.search(kind_text)), "direction": "up" if UP.search(kind_text) and not DOWN.search(kind_text) else "down" if DOWN.search(kind_text) and not UP.search(kind_text) else None,
                          "numbers": sorted(numbers_in(kind_text))[:10]}}
