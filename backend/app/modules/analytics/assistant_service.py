"""The analytics assistant: a question becomes a validated plan, the engine computes, an independent check verifies the figures,
and the answer is presented with its evidence. A language model is optional and only ever rephrases already-verified figures."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from ...core.errors import AppError
from ..explanation import explain
from ..identity.authorization import Principal
from ..identity.constants import Perm
from ..identity.tokens import new_id
from .assistant.answer import compose
from .assistant.language import MAX_QUESTION, interpret
from .assistant.providers import Explainer, build_explainer
from .assistant.verify import verify_facts
from .constants import AEvent, Evidence, RunKind
from .errors import not_found, plan_invalid
from .executor import limits_for
from .service import AnalyticsService, _json


class AssistantService:
    def __init__(self, analytics: AnalyticsService, explainer: Optional[Explainer] = None) -> None:
        self.an = analytics
        self.repo = analytics.repo
        self.ctx = analytics.ctx
        self.explainer = explainer or build_explainer(analytics.ctx.settings)

    def provider(self) -> Dict[str, Any]:
        return self.explainer.describe()

    # ------------------------------------------------------------------ conversations
    def _conversation(self, principal: Principal, conversation_id: Any, asset: Mapping[str, Any]):
        row = self.repo.conversation(principal.user_id, conversation_id) if isinstance(conversation_id, str) and 0 < len(conversation_id) <= 64 else None
        if row is None or row["asset_id"] != asset["id"]:
            raise not_found("conversation")
        return row

    def conversations(self, principal: Principal, asset_id: str) -> Dict[str, Any]:
        asset = self.an.assets.get_owned(principal, asset_id)
        rows = self.repo.conversations_for_asset(principal.user_id, asset["id"], 50)
        return {"items": [{"id": r["id"], "title": r["title"], "created_at": r["created_at"], "updated_at": r["updated_at"]} for r in rows]}

    def conversation(self, principal: Principal, conversation_id: str) -> Dict[str, Any]:
        row = self.repo.conversation(principal.user_id, conversation_id) if isinstance(conversation_id, str) and 0 < len(conversation_id) <= 64 else None
        if row is None:
            raise not_found("conversation")
        self.an.assets.get_owned(principal, row["asset_id"])
        msgs = self.repo.messages(principal.user_id, row["id"])
        out = []
        for m in msgs:
            payload = _json(m["payload"], None)
            if m["role"] == "assistant" and isinstance(payload, dict) and payload.get("status") == "answered" and "explanation" not in payload and m["run_id"]:
                try:                                          # answers stored before Phase 5: explained from the stored run, nothing new is calculated
                    payload["explanation"] = self._explain(self.an.get_run(principal, m["run_id"]), payload.get("verification") or {}, payload.get("steps") or [])
                except AppError:
                    payload["explanation"] = explain("assistant_answer", {})
            out.append({"id": m["id"], "role": m["role"], "content": m["content"], "run_id": m["run_id"], "payload": payload, "created_at": m["created_at"]})
        return {"id": row["id"], "asset_id": row["asset_id"], "title": row["title"], "created_at": row["created_at"], "messages": out}

    @staticmethod
    def _clean_title(title: Any) -> str:
        import re
        t = re.sub(r"[\x00-\x1f\x7f]", " ", title).strip() if isinstance(title, str) else ""
        t = re.sub(r"\s+", " ", t)
        if not 1 <= len(t) <= 200:
            raise plan_invalid("title", "Give the conversation a title of 1 to 200 characters.")
        return t

    def create_conversation(self, principal: Principal, asset_id: str, title: Any = None) -> Dict[str, Any]:
        asset = self.an.assets.get_owned(principal, asset_id, writable=True)
        self.ctx.limiter.hit("assistant:user", principal.user_id)
        now = self.ctx.clock.iso(); cid = new_id()
        self.repo.create_conversation(id=cid, project_id=asset["project_id"], asset_id=asset["id"], owner=principal.user_id,
                                      title=self._clean_title(title) if title else "New conversation", now=now)
        return {"id": cid, "asset_id": asset["id"], "title": self.repo.conversation(principal.user_id, cid)["title"], "created_at": now, "updated_at": now}

    def rename_conversation(self, principal: Principal, conversation_id: str, title: Any) -> Dict[str, Any]:
        row = self.repo.conversation(principal.user_id, conversation_id) if isinstance(conversation_id, str) and 0 < len(conversation_id) <= 64 else None
        if row is None:
            raise not_found("conversation")
        self.an.assets.get_owned(principal, row["asset_id"], writable=True)
        self.repo.rename_conversation(principal.user_id, row["id"], self._clean_title(title), self.ctx.clock.iso())
        r = self.repo.conversation(principal.user_id, row["id"])
        return {"id": r["id"], "asset_id": r["asset_id"], "title": r["title"], "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def delete_conversation(self, principal: Principal, conversation_id: str) -> Dict[str, Any]:
        row = self.repo.conversation(principal.user_id, conversation_id) if isinstance(conversation_id, str) and 0 < len(conversation_id) <= 64 else None
        if row is None:
            raise not_found("conversation")
        self.repo.delete_conversation(principal.user_id, row["id"])      # the analysis runs behind the answers stay with the dataset; only the chat is removed
        return {"deleted": True, "id": row["id"]}

    # ------------------------------------------------------------------ ask
    def ask(self, principal: Principal, asset_id: str, question: Any, *, conversation_id: Optional[str] = None, choices: Optional[Mapping[str, Any]] = None,
            use_ai: bool = False, version_id: Optional[str] = None) -> Dict[str, Any]:
        self.ctx.limiter.hit("assistant:user", principal.user_id)
        if not isinstance(question, str) or not question.strip():
            raise plan_invalid("question", "Type a question about your data.")
        if len(question) > MAX_QUESTION:
            raise plan_invalid("question", f"Keep the question under {MAX_QUESTION} characters.")
        if choices is not None and (not isinstance(choices, dict) or len(choices) > 10 or any(not isinstance(k, str) or len(k) > 80 or not isinstance(v, str) or len(v) > 200 for k, v in choices.items())):
            raise plan_invalid("choices", "Answer the clarifying question with one of the options.")
        schema = self.an.schema(principal, asset_id, version_id)
        asset = self.an.assets.get_owned(principal, asset_id)
        now = self.ctx.clock.iso()
        if conversation_id:
            conv = self._conversation(principal, conversation_id, asset)
            conv_id = conv["id"]
        else:
            conv_id = new_id()
            self.repo.create_conversation(id=conv_id, project_id=asset["project_id"], asset_id=asset["id"], owner=principal.user_id, title=question.strip()[:80], now=now)
        self.repo.add_message(id=new_id(), conversation_id=conv_id, project_id=asset["project_id"], owner=principal.user_id, role="user", content=question.strip()[:MAX_QUESTION],
                              run_id=None, payload=json.dumps({"choices": dict(choices or {})}) if choices else None, now=now)
        interp = interpret(question, [{"name": c["name"], "role": c["role"], "type": c["type"]} for c in schema["columns"]], choices)
        if interp.clarification:
            payload = {"status": "needs_clarification", "clarification": interp.clarification, "steps": interp.steps}
            self._reply(principal, asset, conv_id, interp.clarification["prompt"], None, payload)
            self.ctx.audit.record(AEvent.ASKED, "success", actor=principal.user_id, target=conv_id, metadata={"asset_id": asset["id"], "outcome": "clarification"})
            return {"status": "needs_clarification", "conversation_id": conv_id, "clarification": interp.clarification, "interpretation": self._interp(interp, schema)}
        try:
            run = self.an.run(principal, asset_id, kind=RunKind.QUESTION, operations=interp.operations, filters=interp.filters, version_id=version_id,
                              label=question.strip()[:120])
        except AppError as error:
            if error.code != "VALIDATION_ERROR":
                raise
            msg = "I could not turn that into a valid analysis for these columns. Try naming the columns and what to calculate."
            self._reply(principal, asset, conv_id, msg, None, {"status": "cannot_answer", "steps": interp.steps})
            return {"status": "cannot_answer", "conversation_id": conv_id, "message": msg, "interpretation": self._interp(interp, schema)}
        answer = compose(interp, run)
        verification = self._verify(principal, asset, run, answer["facts"], interp.filters)
        evidence_status = run["evidence_status"] if verification["status"] != "FAILED" else Evidence.INCOMPLETE
        text = answer["text"]
        if verification["status"] == "FAILED":
            text = ("The independent check did not reproduce one or more figures, so this answer is not shown as established. "
                    "The run and its evidence are saved for inspection; nothing was changed.")
        ai: Dict[str, Any] = {"requested": bool(use_ai), "used": False}
        if use_ai:
            info = self.explainer.describe()
            ai["provider"] = info
            if not info.get("available"):
                ai["reason"] = "No AI provider is configured on this server, so the standard explanation is shown. Your data was not sent anywhere."
            elif verification["status"] == "FAILED":
                ai["reason"] = "Not used because the figures were not verified."
            else:
                out = self.explainer.explain(question.strip(), answer["facts"], text)
                ai.update({"used": bool(out.get("text")), "text": out.get("text"), "reason": out.get("reason")})
        explanation = self._explain(run, verification, interp.steps) if verification["status"] != "FAILED" else explain("assistant_answer", {})
        payload = {"status": "answered", "intent": interp.intent, "steps": interp.steps, "notes": interp.notes, "run_id": run["id"], "evidence_status": evidence_status,
                   "facts": answer["facts"], "caveats": answer["caveats"], "verification": verification, "ai": {k: v for k, v in ai.items() if k != "provider"}, "explanation": explanation}
        mid = self._reply(principal, asset, conv_id, text, run["id"], payload)
        self.ctx.audit.record(AEvent.ASKED, "success", actor=principal.user_id, target=run["id"],
                              metadata={"asset_id": asset["id"], "intent": interp.intent, "ai_requested": bool(use_ai), "ai_used": ai["used"], "verification": verification["status"], "ops": len(interp.operations)})
        return {"status": "answered", "conversation_id": conv_id, "message_id": mid,
                "answer": {"text": text, "caveats": answer["caveats"], "facts": answer["facts"], "verification": verification, "ai": ai, "explanation": explanation},
                "interpretation": self._interp(interp, schema), "run": {"id": run["id"], "evidence_status": evidence_status, "plan_hash": run["plan_hash"], "result_hash": run["result_hash"]},
                "evidence_status": evidence_status}

    @staticmethod
    def _explain(run: Mapping[str, Any], verification: Mapping[str, Any], steps: List[Any]) -> Dict[str, Any]:
        """The Explanation of an answer: how the question was read, whether the figures were re-checked, and the plain-English reading of the main result."""
        primary = next((r.get("explanation") for r in (run.get("result") or {}).get("results", []) if r.get("status") == "ok" and (r.get("explanation") or {}).get("status") == "available"), None)
        return explain("assistant_answer", {"primary_explanation": primary, "verification": dict(verification), "steps": [str(x) for x in steps]})

    def _interp(self, interp, schema) -> Dict[str, Any]:
        return {"intent": interp.intent, "steps": interp.steps, "notes": interp.notes, "filters": interp.filters, "operations": interp.operations, "columns": interp.roles,
                "dataset_version": {"version_id": schema["version_id"], "version_number": schema["version_number"]}}

    def _reply(self, principal: Principal, asset: Mapping[str, Any], conv_id: str, content: str, run_id: Optional[str], payload: Dict[str, Any]) -> str:
        mid = new_id()
        now = self.ctx.clock.iso()
        self.repo.add_message(id=mid, conversation_id=conv_id, project_id=asset["project_id"], owner=principal.user_id, role="assistant", content=content[:4000], run_id=run_id,
                              payload=json.dumps(payload, default=str), now=now)
        self.repo.touch_conversation(conv_id, now)
        return mid

    def _verify(self, principal: Principal, asset: Mapping[str, Any], run: Mapping[str, Any], facts: List[Dict[str, Any]], filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Step 8: recompute each checkable figure from the stored table with an independent implementation."""
        _, version, declared, _ = self.an._open(principal, run["asset_id"], run["version_id"])
        limits = limits_for(principal.can(Perm.COMPUTE_EXTENDED))
        try:
            with self.an.storage.open(version["canonical_key"]) as stream:
                return verify_facts(stream, declared, filters, facts, int(limits["max_rows"]))
        except Exception:
            for f in facts:
                f["verification"] = "not_checked"
            return {"method": "independent recomputation", "checked": 0, "matched": 0, "mismatched": 0, "not_checked": len(facts), "status": "NOT_CHECKED",
                    "reason": "The independent check could not read the table."}
