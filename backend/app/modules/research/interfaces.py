"""The two ways other parts of the platform reach Phase 6 work.

1. `EvidenceHub` (implements the `EvidenceProvider` protocol): the one call an assistant, an analysis, a prediction page or a report uses to get research that is safe to reuse. It returns
   only bundles that pass their integrity check, only labelled conclusions with their citations, and world events as *reported* statements, never as facts. A consumer must keep the labels.

2. `ExamResearchInterface`: what Phase 6 can do for education today (source-grounded topic research over the person's own course documents, no outside access unless they allow it) and,
   in plain words, what Exam Intelligence will need in Phase 7. Nothing here analyses syllabuses or papers, writes questions or marks answers; those features do not exist yet and the
   interface says so instead of pretending."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from ..identity.authorization import Principal
from ..workspace.errors import invalid

EVIDENCE_SCHEMA = "predictai.evidence_for_consumer/1"
REUSE_RULES = [
    "Keep each conclusion's statement type (quoted, uncertain, opinion, forecast, calculated) and its origin label when you show it.",
    "Never present a forecast or a news report as an established fact.",
    "Show the citations with the text. A conclusion without its citation is not evidence.",
    "Reported news is what outlets said, not what PredictAI verified.",
]


@runtime_checkable
class EvidenceProvider(Protocol):
    def evidence_for_project(self, principal: Principal, project_id: str, *, session_id: Optional[str] = ..., include_world: bool = ..., limit: int = ...) -> Dict[str, Any]:
        """Reusable evidence for one project of the caller's. Raises the usual not-found error for a project that is not theirs."""
        ...


class EvidenceHub:
    def __init__(self, research: Any, world: Any) -> None:
        self.research, self.world = research, world

    def evidence_for_project(self, principal: Principal, project_id: str, *, session_id: Optional[str] = None, include_world: bool = True, limit: int = 5) -> Dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise invalid("limit", "Enter a whole number from 1 to 20.")
        listing = self.research.list_bundles(principal, project_id, session_id)["items"]
        research: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        for entry in listing:
            if len(research) >= limit:
                break
            try:
                research.append(self.research.summary(principal, entry["id"]))
            except Exception as exc:  # noqa: BLE001  a damaged bundle must not hide the others; the reason is reported, not the content
                skipped.append({"bundle_id": entry["id"], "reason": getattr(exc, "message", None) or "This bundle could not be reused."})
        world: List[Dict[str, Any]] = []
        if include_world:
            for ev in self.world.list_events(principal, project_id, limit_=limit)["items"]:
                if ev["status"] in ("confirmed", "reported", "disputed"):
                    world.append(self.world.event_summary(principal, ev["id"]))
        return {"schema": EVIDENCE_SCHEMA, "project_id": project_id, "research": research, "world": world, "skipped": skipped, "rules": REUSE_RULES,
                "consumers": ["assistant", "analytics", "predictive", "exam_intelligence", "reports"]}


# ---------------------------------------------------------------------------------------------- exam research
EXAM_NOW = [
    {"key": "topic_research", "label": "Source-grounded topic research", "about": "Explain a topic using only the course documents you choose, with a citation for every statement. Outside sources are used only if you allow them."},
    {"key": "educational_evidence", "label": "Educational evidence retrieval", "about": "Find the passages in your own documents that bear on a topic, ranked, with the page they came from."},
]
EXAM_PHASE7 = [
    {"id": "OUT-01a", "feature": "Syllabus analysis", "needs": "A syllabus parser that turns a document into units and topics, checked against the person's own text, and an agreed definition of a topic."},
    {"id": "OUT-01b", "feature": "Past-paper analysis, topic recurrence and weight", "needs": "Question segmentation for scanned and typed papers, a topic labelling method with a measured error rate, and rules for what counts as the same topic across years."},
    {"id": "OUT-01c", "feature": "Question generation (multiple choice, short answer, long answer)", "needs": "Generation grounded in cited source text, checks that the answer key is supported by that text, and a review step before anything is shown as an exam question."},
    {"id": "OUT-01d", "feature": "Mock examinations, answer keys and marking schemes", "needs": "A marking-scheme format, a way to record the person's answers, and rules for partial credit that a teacher can inspect."},
    {"id": "OUT-01e", "feature": "Assessment and evaluation of answers", "needs": "A method for scoring free-text answers that has been checked against human marking, with its error reported; until then answers can only be compared with the cited source text."},
]


class ExamResearchInterface:
    def __init__(self, research: Any) -> None:
        self.research = research

    def capabilities(self) -> Dict[str, Any]:
        return {"available_now": EXAM_NOW, "not_available": EXAM_PHASE7, "phase": "Exam Intelligence is a Phase 7 requirement. None of the features listed under not_available exists yet.",
                "how_to_use": "Create a topic research session with course documents; it is planned first and runs only after you approve it."}

    def topic_research(self, principal: Principal, project_id: str, *, topic: Any, course_documents: Sequence[str] = (), level: str = "topic") -> Dict[str, Any]:
        if not isinstance(topic, str) or not 3 <= len(" ".join(topic.split())) <= 200:
            raise invalid("topic", "Enter a topic of 3 to 200 characters.")
        if isinstance(course_documents, (str, bytes)) or not isinstance(course_documents, (list, tuple)) or len(course_documents) > 20:
            raise invalid("course_documents", "Choose up to 20 of your documents.")
        topic = " ".join(topic.split())
        question = topic if topic.endswith("?") else f"What do the course documents say about {topic}?"
        session = self.research.create_session(principal, project_id, {"question": question, "level": level, "purpose": "education", "title": f"Topic: {topic}"[:120],
                                                                       "options": {"documents": list(course_documents), "web": False}})
        return {"session": session, "note": "A plan was made and nothing has run. Review the plan, then approve and run it. Only the documents you chose are read; no outside source is used unless you turn it on."}
