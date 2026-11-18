"""The built-in provider: deterministic, rule-based and extractive. It is a label for the code paths PredictAI already has (planner, extractive summaries, lexicon classifiers,
hashed term vectors); it has no model, makes no network call and never writes a claim that is not quoted from a source. The router returns it whenever no model provider may or can be used."""
from __future__ import annotations

from typing import Set

from .base import Adapter

BUILTIN_TASKS = ("plan", "extract", "summarize", "classify", "sentiment", "entities", "synthesize")


class BuiltinAdapter(Adapter):
    kind = "builtin"

    def capabilities(self) -> Set[str]:
        return set()
