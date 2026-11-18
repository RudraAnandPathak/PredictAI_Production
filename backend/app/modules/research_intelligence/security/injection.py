"""Prompt-injection handling for imported material.

Imported documents and web pages are DATA. Three layers: (1) text that looks like an instruction to an AI is FLAGGED at ingestion (the passage stays searchable and quotable, with a
warning, because a paper may legitimately discuss prompt injection); (2) when text is sent to a model it goes only inside delimited <source> blocks with the standing rule that block
contents are never instructions (providers.base.build_messages / neutralise); (3) a model's answer is never trusted: every sentence must be backed by a retrieved passage or it is
dropped (citations/verify.py). Nothing in imported text can change a limit, a permission, a provider choice or a workspace setting, because none of those are read from text."""
from __future__ import annotations

import re
from typing import List

PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts?|rules|messages)",
    r"disregard (all |any |the )?(previous|prior|above|earlier|system)\b( (instructions|prompts?|rules))?",
    r"forget (everything|all|your) (above|previous|instructions|rules)",
    r"you are (now|no longer) (an?|the)? ?(assistant|ai|chatbot|dan|jailbroken|system)",
    r"(reveal|print|show|output|repeat) (your|the) (system|hidden|initial) (prompt|instructions|message)",
    r"new (system )?instructions?:",
    r"\bsystem prompt\b",
    r"<\|?(im_start|im_end|system|assistant|endoftext)\|?>",
    r"\[\s*(inst|/inst|system)\s*\]",
    r"^\s*(system|assistant)\s*:\s",
    r"do not (cite|mention|tell) (the )?(user|sources?)",
    r"(send|post|upload|exfiltrate) .{0,40}(api[_ -]?key|password|token|secret)",
]
_COMPILED = [re.compile(p, re.I | re.M) for p in PATTERNS]


def scan(text: str) -> List[str]:
    """Return the patterns that matched (empty when the text looks like ordinary prose)."""
    if not text:
        return []
    return [p.pattern for p in _COMPILED if p.search(text)]


def is_flagged(text: str) -> bool:
    return bool(scan(text))
