"""Any server that speaks the OpenAI-style HTTP API (llama.cpp server, vLLM, LM Studio, Ollama's /v1, or a commercial service).
POST {base}/chat/completions -> choices[0].message.content, usage.prompt_tokens/completion_tokens; POST {base}/embeddings -> data[].embedding; GET {base}/models -> data[].id.
`base_url` includes the version prefix (for example http://127.0.0.1:8080/v1). The key is read from an environment variable at call time and is never stored or logged."""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, Optional, Sequence, Set

from .base import Adapter, Completion, Embedded, ProviderError, estimate_units
from .http import JsonHttp


class OpenAICompatAdapter(Adapter):
    kind = "openai_compatible"

    def __init__(self, base_url: str, model: Optional[str], embed_model: Optional[str], http: JsonHttp, secret: Callable[[], Optional[str]]) -> None:
        self.base, self.model, self.embed_model, self.http, self.secret = base_url.rstrip("/"), (model or "").strip(), (embed_model or "").strip(), http, secret

    def _auth(self) -> Dict[str, str]:
        key = self.secret()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def capabilities(self) -> Set[str]:
        return ({"chat"} if self.model else set()) | ({"embed"} if self.embed_model else set())

    def chat(self, messages, *, max_tokens: int, temperature: float, timeout: float) -> Completion:
        if not self.model:
            raise ProviderError("PROVIDER_NO_MODEL", "No chat model is configured for this provider.")
        t0 = time.monotonic()
        data = self.http.request("POST", f"{self.base}/chat/completions", headers=self._auth(), timeout=timeout,
                                 body={"model": self.model, "messages": list(messages), "temperature": temperature, "max_tokens": max_tokens, "stream": False})
        choices = data.get("choices")
        msg = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        text = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("PROVIDER_EMPTY", "The provider returned no text.")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        uin, uout = usage.get("prompt_tokens"), usage.get("completion_tokens")
        return Completion(text=text.strip(), model=self.model, units_in=uin if isinstance(uin, int) else estimate_units(*(m.get("content", "") for m in messages)),
                          units_out=uout if isinstance(uout, int) else estimate_units(text), latency_ms=int((time.monotonic() - t0) * 1000))

    def embed(self, texts: Sequence[str], *, timeout: float) -> Embedded:
        if not self.embed_model:
            raise ProviderError("PROVIDER_NO_MODEL", "No embedding model is configured for this provider.")
        t0 = time.monotonic()
        data = self.http.request("POST", f"{self.base}/embeddings", headers=self._auth(), body={"model": self.embed_model, "input": list(texts)}, timeout=timeout)
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts) or not all(isinstance(r, dict) and isinstance(r.get("embedding"), list) for r in rows):
            raise ProviderError("PROVIDER_BAD_ANSWER", "The provider's embeddings could not be used.")
        rows = sorted(rows, key=lambda r: r.get("index", 0) if isinstance(r.get("index"), int) else 0)
        vectors = [r["embedding"] for r in rows]
        if not all(v and all(isinstance(x, (int, float)) and math.isfinite(x) for x in v) for v in vectors) or len({len(v) for v in vectors}) != 1:
            raise ProviderError("PROVIDER_BAD_ANSWER", "The provider's embeddings could not be used.")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return Embedded(vectors=[[float(x) for x in v] for v in vectors], model=self.embed_model, units_in=usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else estimate_units(*texts),
                        latency_ms=int((time.monotonic() - t0) * 1000))

    def probe(self, timeout: float) -> Dict[str, Any]:
        data = self.http.request("GET", f"{self.base}/models", headers=self._auth(), timeout=timeout)
        names = [str(m.get("id")) for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
        names += [n[len("models/"):] for n in names if n.startswith("models/")]            # some services (Google's OpenAI-compatible endpoint) list a model as "models/<name>" but accept "<name>"
        caps, problems = [], []
        for label, wanted, cap in (("chat", self.model, "chat"), ("embedding", self.embed_model, "embed")):
            if wanted:
                (caps.append(cap) if wanted in names else problems.append(f"{label} model '{wanted}' is not listed by the server"))
        return {"ok": bool(caps) and not problems, "capabilities": caps, "models_available": len(names), "problems": problems, "has_key": bool(self.secret())}
