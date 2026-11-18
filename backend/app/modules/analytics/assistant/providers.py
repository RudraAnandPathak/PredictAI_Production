"""Optional explanation providers. The default is none: answers are assembled from verified facts by templates, with no model and no network.

A provider only REPHRASES facts the analysis engine already computed. It never receives rows, never receives a dataset, never decides
which calculation to run, and every number in its output is checked against the facts; an output with an untraceable number is discarded.
Providers are interchangeable behind `Explainer`; the only one shipped is a local Ollama server (free, runs on the user's machine)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence
from urllib.parse import urlparse

from .answer import allowed_numbers, numbers_supported


class Explainer(Protocol):
    name: str

    def describe(self) -> Dict[str, Any]: ...
    def explain(self, question: str, facts: Sequence[Mapping[str, Any]], draft: str) -> Dict[str, Any]: ...


class NoExplainer:
    name = "none"

    def describe(self) -> Dict[str, Any]:
        return {"provider": "none", "available": False, "data_leaves_server": False}

    def explain(self, question, facts, draft):  # pragma: no cover - never called when unavailable
        return {"text": None, "reason": "No AI provider is configured."}


PostJson = Callable[[str, Dict[str, Any], float], Dict[str, Any]]


def _post_json(url: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme and host are validated at configuration time
        return json.loads(resp.read(2_000_000).decode("utf-8"))


class OllamaExplainer:
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float, post: PostJson = _post_json) -> None:
        self.base_url, self.model, self.timeout, self.post = base_url.rstrip("/"), model, timeout, post

    @property
    def local(self) -> bool:
        return (urlparse(self.base_url).hostname or "") in ("localhost", "127.0.0.1", "::1")

    def describe(self) -> Dict[str, Any]:
        return {"provider": "ollama", "model": self.model, "available": True, "data_leaves_server": not self.local,
                "host": urlparse(self.base_url).hostname, "sends": "the question and the computed figures (labels and numbers) only: no rows and no dataset"}

    def explain(self, question: str, facts: Sequence[Mapping[str, Any]], draft: str) -> Dict[str, Any]:
        listing = "\n".join(f"- {f['label']}: {f['value']}" + (f" (n={f['n']})" if f.get("n") else "") for f in facts)
        prompt = ("You explain the result of a data analysis in two or three plain sentences for a non-expert.\n"
                  "Use ONLY the figures listed below, copied exactly. Do not add, compute or round any other number. Do not say one thing causes another. "
                  "Do not claim a p-value is the probability a hypothesis is true.\n\n"
                  f"Question: {question[:400]}\n\nFigures:\n{listing}\n\nDraft answer:\n{draft}\n\nExplanation:")
        try:
            raw = self.post(f"{self.base_url}/api/generate", {"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0, "seed": 7, "num_predict": 220}}, self.timeout)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return {"text": None, "reason": "The AI provider could not be reached; the standard explanation is shown."}
        text = str(raw.get("response", "")).strip()
        if not text:
            return {"text": None, "reason": "The AI provider returned nothing; the standard explanation is shown."}
        bad = numbers_supported(text, allowed_numbers(facts))
        if bad:
            return {"text": None, "reason": "The AI explanation contained numbers that are not in the analysis results (" + ", ".join(bad[:3]) + "), so it was discarded."}
        lowered = text.lower()
        if any(p in lowered for p in ("causes", "caused by", "leads to", "because of")):
            return {"text": None, "reason": "The AI explanation implied causation, so it was discarded."}
        return {"text": text[:1200], "reason": None}


def build_explainer(settings: Any, post: Optional[PostJson] = None) -> Explainer:
    if getattr(settings, "ai_provider", "none") == "ollama":
        return OllamaExplainer(settings.ai_base_url, settings.ai_model, float(settings.ai_timeout_s), post or _post_json)
    return NoExplainer()
