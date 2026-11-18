"""Shared fixtures for the Brain and exam-prediction tests.

`GeminiFake` answers the way Google documents its OpenAI-compatible endpoint (POST .../v1beta/openai/chat/completions with a Bearer key, GET .../models listing names as "models/<name>").
It is a stand-in served from this machine: it shows how PredictAI treats those documented shapes, not how Google's live service behaves.
`GeminiFactory` points the real OpenAICompatAdapter at it; the provider row itself carries Google's real address and is classed 'external', exactly as in production, so the privacy rules are
exercised for real."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from app.modules.phase7 import build_phase7
from app.modules.providers import AdapterFactory
from app.modules.providers.http import JsonHttp
from app.modules.providers.openai_compat import OpenAICompatAdapter

from research_support import ResearchBase
from web_support import FakeWeb

KEY = "test-key-123-do-not-leak"
PUBLIC = lambda host, port: ["93.184.216.34"]  # noqa: E731


class GeminiFake:
    def __init__(self, web: FakeWeb, *, key: str = KEY, models=("models/gemini-fake-flash", "models/gemini-fake-lite")) -> None:
        self.web, self.key, self.models = web, key, list(models)
        self.requests: List[Dict[str, Any]] = []
        self.headers: List[Dict[str, str]] = []
        self.answer: Callable[[Dict[str, Any]], Any] = lambda req: "ready"       # request body -> text, or (status, body) to force a failure
        web.routes["/v1beta/openai/chat/completions"] = self._chat
        web.routes["/v1beta/openai/models"] = self._models

    def _models(self, h):
        if h.headers.get("Authorization") != f"Bearer {self.key}":
            return 403, {"Content-Type": "application/json"}, b"{}"
        return 200, {"Content-Type": "application/json"}, json.dumps({"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]}).encode()

    def _chat(self, h):
        if h.headers.get("Authorization") != f"Bearer {self.key}":
            return 401, {"Content-Type": "application/json"}, b"{}"
        req = json.loads(h.body)
        self.requests.append(req)
        self.headers.append({k.lower(): v for k, v in h.headers.items()})
        out = self.answer(req)
        if isinstance(out, tuple):
            return out[0], {"Content-Type": "application/json"}, json.dumps(out[1]).encode()
        return 200, {"Content-Type": "application/json"}, json.dumps({"choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}],
                                                                    "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180}}).encode()


class GeminiFactory(AdapterFactory):
    def __init__(self, fake: GeminiFake, env: Optional[Dict[str, str]] = None) -> None:
        super().__init__(env=env if env is not None else {"PREDICTAI_AI_KEY_GEMINI": fake.key}, resolver=PUBLIC)
        self.fake = fake

    def build(self, row):
        if row["key"] == "gemini":
            return OpenAICompatAdapter(self.fake.web.base + "/v1beta/openai", row.get("model"), row.get("embed_model"), JsonHttp(allow_private=True), self.secret(row))
        return super().build(row)


def slots_in(request: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The slots a question-writing request asks for, read from its prompt."""
    text = request["messages"][-1]["content"]
    return [{"slot": int(m.group(1)), "chapter": m.group(2), "qtype": m.group(3)} for m in re.finditer(r"Slot (\d+): chapter “([^”]+)”, type (\w+),", text)]


def good_questions(request: Dict[str, Any]) -> str:
    out = []
    for s in slots_in(request):
        n, t = s["slot"], s["qtype"]
        base = {"slot": n, "topic": "x"}
        if t == "mcq":
            out.append({**base, "text": f"Which statement about {s['chapter'].split('. ', 1)[-1].lower()} is correct (variant {n})?", "options": [{"label": "A", "text": f"first {n}"}, {"label": "B", "text": f"second {n}"},
                                                                                                            {"label": "C", "text": f"third {n}"}, {"label": "D", "text": f"fourth {n}"}], "answer": "B"})
        elif t == "true_false":
            out.append({**base, "text": f"Statement number {n} about {s['chapter'].split('. ', 1)[-1].lower()} holds in every case.", "answer": "False"})
        elif t == "fill_blank":
            out.append({**base, "text": f"The structure number {n} is called the _____ in this chapter.", "answer": "organelle"})
        else:
            out.append({**base, "text": f"Discuss aspect {n} of {s['chapter'].split('. ', 1)[-1].lower()} with examples.", "answer": "A model answer.", "marking": ["State the idea", "Give an example", "Link it to the cell"]})
    return "```json\n" + json.dumps(out) + "\n```"


class BrainBase(ResearchBase):
    settings_overrides = {"ai_allow_remote_host": True, "standard_max_documents": 50}

    def setUp(self):
        super().setUp()
        self.owner, _ = self.h.make_owner("owner@example.com")
        self.gem_web = FakeWeb()
        self.gem_web.__enter__()
        self.addCleanup(lambda: self.gem_web.__exit__())
        self.gem = GeminiFake(self.gem_web)
        self.rs.providers.resolver = PUBLIC
        self.rs.providers.factory = GeminiFactory(self.gem)
        self.p7 = build_phase7(self.ws, self.rs)
        self.brain = self.p7.brain

    def setup_gemini(self, **kw):
        return self.brain.setup_gemini(self.owner, **{"model": "gemini-fake-flash", "acknowledge_data_use": True, **kw})

    def allow_external(self, principal=None):
        self.svc.put_settings(principal or self.alice, None, {"external_ai": True})

    def switch_off(self, principal=None):
        self.svc.put_settings(principal or self.alice, None, {"external_ai": False})

    def gemini_requests(self) -> int:
        return len(self.gem.requests)
