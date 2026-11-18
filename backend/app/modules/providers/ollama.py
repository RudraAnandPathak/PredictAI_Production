"""Ollama (https://ollama.com), a free local model server. API (documented): POST /api/chat {model, messages, stream:false, options} -> message.content, prompt_eval_count, eval_count;
POST /api/embed {model, input} -> embeddings; GET /api/tags -> models[].name."""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Sequence, Set

from .base import Adapter, Completion, Embedded, ProviderError, estimate_units
from .http import JsonHttp


def model_listed(wanted: str, names: Sequence[str]) -> bool:
    w = (wanted or "").strip().lower()
    return bool(w) and any(n.lower() in (w, f"{w}:latest") or n.lower().split(":")[0] == w for n in names)


class OllamaAdapter(Adapter):
    kind = "ollama"

    def __init__(self, base_url: str, model: Optional[str], embed_model: Optional[str], http: JsonHttp) -> None:
        self.base, self.model, self.embed_model, self.http = base_url.rstrip("/"), (model or "").strip(), (embed_model or "").strip(), http

    def capabilities(self) -> Set[str]:
        return ({"chat"} if self.model else set()) | ({"embed"} if self.embed_model else set())

    def chat(self, messages, *, max_tokens: int, temperature: float, timeout: float) -> Completion:
        if not self.model:
            raise ProviderError("PROVIDER_NO_MODEL", "No chat model is configured for this provider.")
        t0 = time.monotonic()
        data = self.http.request("POST", f"{self.base}/api/chat", body={"model": self.model, "messages": list(messages), "stream": False,
                                                                     "options": {"temperature": temperature, "seed": 7, "num_predict": max_tokens}}, timeout=timeout)
        if data.get("error"):
            raise ProviderError("PROVIDER_ERROR", "The provider reported an error.")
        msg = data.get("message")
        text = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("PROVIDER_EMPTY", "The provider returned no text.")
        uin, uout = data.get("prompt_eval_count"), data.get("eval_count")
        return Completion(text=text.strip(), model=self.model, units_in=uin if isinstance(uin, int) else estimate_units(*(m.get("content", "") for m in messages)),
                          units_out=uout if isinstance(uout, int) else estimate_units(text), latency_ms=int((time.monotonic() - t0) * 1000))

    def embed(self, texts: Sequence[str], *, timeout: float) -> Embedded:
        if not self.embed_model:
            raise ProviderError("PROVIDER_NO_MODEL", "No embedding model is configured for this provider.")
        t0 = time.monotonic()
        data = self.http.request("POST", f"{self.base}/api/embed", body={"model": self.embed_model, "input": list(texts)}, timeout=timeout)
        vectors = data.get("embeddings")
        ok = (isinstance(vectors, list) and len(vectors) == len(texts) and all(isinstance(v, list) and v and all(isinstance(x, (int, float)) and math.isfinite(x) for x in v) for v in vectors)
              and len({len(v) for v in vectors}) == 1)
        if not ok:
            raise ProviderError("PROVIDER_BAD_ANSWER", "The provider's embeddings could not be used.")
        return Embedded(vectors=[[float(x) for x in v] for v in vectors], model=self.embed_model, units_in=data.get("prompt_eval_count") if isinstance(data.get("prompt_eval_count"), int) else estimate_units(*texts),
                        latency_ms=int((time.monotonic() - t0) * 1000))

    def probe(self, timeout: float) -> Dict[str, Any]:
        data = self.http.request("GET", f"{self.base}/api/tags", timeout=timeout)
        names = [str(m.get("name") or m.get("model")) for m in (data.get("models") or []) if isinstance(m, dict) and (m.get("name") or m.get("model"))]
        caps: List[str] = []
        problems: List[str] = []
        if self.model:
            (caps.append("chat") if model_listed(self.model, names) else problems.append(f"chat model '{self.model}' is not installed on the server"))
        if self.embed_model:
            (caps.append("embed") if model_listed(self.embed_model, names) else problems.append(f"embedding model '{self.embed_model}' is not installed on the server"))
        return {"ok": bool(caps) and not problems, "capabilities": caps, "models_available": len(names), "problems": problems}
