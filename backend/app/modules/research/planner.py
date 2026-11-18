"""A transparent, rule-based research planner. It turns a question and a depth level into subtopics, ordered tasks and the exact web queries that WOULD be sent if the person
approves them. Nothing is invented from the web at this stage: the plan is built from the words of the question only, so the person can read it, edit the question and approve it.
(A model provider is never needed to plan; `plan_hash` pins the approved queries.)"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from ..textkit import content_terms
from ..webaccess.queries import Focus, build_provider_queries, providers_for, topic_focus
from .intents import TITLES, dataset_intents
from .outlines import build_outline, generic_outline
from .queryplan import ARXIV_MODES, provider_fit, queries_for
from .safety import sanitize_query
from .understanding import Frame, understand

LEVELS: Dict[str, Dict[str, Any]] = {
    "basic": {"label": "Basic", "subtopics": 1, "passages": 5, "web_pages": 3, "min_sources": 1, "rounds": 1, "about": "A quick answer from your own documents, plus a couple of web sources if you allow them."},
    "topic": {"label": "Topic overview", "subtopics": 3, "passages": 6, "web_pages": 6, "min_sources": 2, "rounds": 1, "about": "Background, key facts and recent developments on one topic."},
    "comparative": {"label": "Comparative", "subtopics": 3, "passages": 8, "web_pages": 10, "min_sources": 2, "rounds": 1, "about": "Two or more things side by side, with differences and agreements."},
    "technical": {"label": "Technical", "subtopics": 4, "passages": 7, "web_pages": 8, "min_sources": 2, "rounds": 1, "about": "Definitions, how it works, methods and data, with the technical detail kept."},
    "academic": {"label": "Academic", "subtopics": 4, "passages": 7, "web_pages": 8, "min_sources": 3, "rounds": 1, "about": "Foundations, methods, findings and limitations, preferring scholarly sources."},
    "multi_step": {"label": "Multi-step", "subtopics": 4, "passages": 6, "web_pages": 8, "min_sources": 2, "rounds": 1, "about": "A question with several parts, answered one part at a time."},
    "in_depth": {"label": "In-depth", "subtopics": 6, "passages": 6, "web_pages": 8, "min_sources": 3, "rounds": 2, "about": "Several angles, a follow-up round on gaps, and a closer look at disagreements."},
}
TEMPLATES = {
    "topic": [("Overview", ""), ("Background and definitions", "definition background"), ("Key facts and figures", "facts statistics data"), ("Recent developments", "recent news update")],
    "technical": [("Definition", "definition"), ("How it works", "how it works mechanism"), ("Methods and data", "method data specification"), ("Limitations", "limitations problems")],
    "academic": [("Foundations", "theory foundations review"), ("Methods", "methods methodology"), ("Findings", "results findings evidence"), ("Criticism and limitations", "limitations criticism")],
    "in_depth": [("Overview", ""), ("Background", "background history definition"), ("Key facts and figures", "facts statistics data"), ("Evidence and studies", "study evidence research"),
                 ("Disagreements and criticism", "criticism controversy debate"), ("Recent developments", "recent news update")],
}
INTENTS = [(r"\b(compare|compared|versus|vs\.?|difference between|differences between|better than|\w+er than|similarities)\b", "comparison"), (r"\b(will|forecast|predict|projection|outlook|going to)\b", "forecast"),
           (r"\b(how many|how much|what percentage|what share|statistics|rate of|number of|average)\b", "data"), (r"\bwhy\b", "explanation"), (r"\bhow (do|does|did|to|can)\b", "process"),
           (r"\b(what is|what are|define|meaning of|explain)\b", "definition")]


def _intent(q: str) -> str:
    low = q.lower()
    return next((name for rx, name in INTENTS if re.search(rx, low)), "general")


_ENTITY_PATTERNS = [re.compile(p, re.I) for p in (
    r"(?:the )?(?:differences?|similarit(?:y|ies)|distinctions?|contrasts?) between\s+(?P<a>.+?)\s+and\s+(?P<b>.+)",
    r"\bcompare\s+(?P<a>.+?)\s+(?:and|with|to|versus|vs\.?|against)\s+(?P<b>.+)",
    r"(?P<a>.+?)\s+(?:compare[sd]?\s+(?:with|to|against)|versus|vs\.?|against|(?:\w+er|more \w+|less \w+) than|or)\s+(?P<b>.+)")]
_LEAD = re.compile(r"^(?:how (?:does|do|is|are|did)|what is|what are|which is|which are|is|are|does|do)\s+(?:(?:better|best|worse|faster|slower|cheaper|safer)[,:]?\s+)?", re.I)


def _entities(q: str) -> List[str]:
    """The two (or more) things a comparison question sets side by side, or [] when they cannot be told apart reliably. A qualifier after the last one ("for India") applies to all."""
    text = re.sub(r"[?.!]+$", "", q.strip())
    for rx in _ENTITY_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        a, b = _LEAD.sub("", m.group("a").strip()), m.group("b").strip()
        parts = [*re.split(r"\s*,\s*|\s+and\s+", a), *re.split(r"\s*,\s*|\s+and\s+", b)]
        qualifier = ""
        m2 = re.match(r"^(.+?)\s+((?:for|in|across|within|of|during|since)\s+.+)$", parts[-1], re.I)
        if m2:
            parts[-1], qualifier = m2.group(1), m2.group(2)
        out: List[str] = []
        for p in parts:
            p = re.sub(r"^(the|a|an)\s+", "", p.strip(), flags=re.I)
            if 1 <= len(p) <= 80 and p.lower() not in [o.lower() for o in out]:
                out.append(p)
        if len(out) >= 2:
            return [f"{o} {qualifier}".strip() for o in out[:4]]
    return []


def _steps(q: str) -> List[str]:
    parts = [p.strip(" ,;.") for p in re.split(r"\b(?:and then|then|after that|followed by)\b|;|\n|(?<=\?)\s+", q) if p and p.strip(" ,;.")]
    return parts if len(parts) > 1 else []


def _dataset_subtopics(question: str, datasets: List[str], start: int) -> List[Dict[str, Any]]:
    """One subtopic per thing the question asks of the selected dataset(s). They are answered by calculation (Phase 4 and 5 services), never by searching."""
    out: List[Dict[str, Any]] = []
    for i, intent in enumerate(dataset_intents(question, datasets=len(datasets)), start=start):
        out.append({"id": f"d{i}", "title": TITLES[intent].replace("{target}", "the outcome you named"), "query": " ".join(content_terms(question, 8)) or question[:80], "purpose": "calculated from your data",
                    "scope": "dataset", "intent": intent, "facet": "", "topic_terms": []})
    return out


def _carry_over(focus: Focus, question: str, parent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A follow-up such as 'and what are its limitations?' has no topic words of its own: it inherits the earlier question's topic, and the plan says so."""
    if not parent:
        return {}
    earlier = topic_focus(parent.get("question") or "")
    pronoun = re.search(r"\b(it|its|that|this|they|them|their|those|these|such|the same)\b", question, re.I)
    if focus.phrases and not (pronoun and len(focus.phrases) <= 1):
        return {"of": parent.get("session_id"), "question": parent.get("question"), "carried": []}
    carried = [p for p in earlier.phrases[:2] if p.lower() not in [x.lower() for x in focus.phrases]]
    focus.phrases[:0] = carried
    for p in carried:
        for w in p.split():
            if w.lower() not in focus.terms:
                focus.terms.append(w.lower())
    focus.natural = " ".join(focus.phrases)[:140]
    return {"of": parent.get("session_id"), "question": parent.get("question"), "carried": carried}


INTERPRET_TASKS = ("overview", "causes", "consequences", "comparison", "statistics", "methodology")
MAX_PARTS = 8


def apply_interpretation(subtopics: List[Dict[str, Any]], interp: Optional[Dict[str, Any]], frame: Frame) -> Optional[Dict[str, Any]]:
    """Fold a model's suggested extra parts into the plan. The model only ever suggests short titles and keywords; they are validated here, become ordinary parts with ordinary search phrases
    built by the same rules as every other part, and are part of the plan the person reads and approves. A model never adds a search phrase of its own."""
    if not interp:
        return None
    record = {"status": interp.get("status", "unavailable"), "provider": interp.get("provider"), "added_parts": [], "note": interp.get("note")}
    if interp.get("status") != "ok":
        return record
    subject = next((e.name for e in frame.entities if e.role == "subject"), "")
    general = [st for st in subtopics if st.get("scope", "general") == "general"]
    have = {st["title"].lower() for st in subtopics}
    for part in (interp.get("parts") or [])[:3]:
        if len(subtopics) >= MAX_PARTS:
            break
        title = " ".join(str(part.get("title") or "").split())[:70]
        words = [w.lower() for w in (part.get("keywords") or []) if isinstance(w, str) and re.fullmatch(r"[A-Za-z][A-Za-z\-]{2,19}", w)][:6]
        if not title or title.lower() in have or not re.fullmatch(r"[A-Za-z0-9 ,'’:\-]{3,70}", title) or not words or not general:
            continue
        clean_title, removed = sanitize_query(title)
        if removed:
            continue
        subtopics.append({"id": f"t{len(subtopics) + 1}", "title": clean_title, "query": " ".join(filter(None, [subject, *words[:2]])), "purpose": "an angle suggested by the local AI provider (shown for your approval)",
                          "facet": "", "topic_terms": [], "vocab": words, "hint": words[0], "scope": "general", "task": frame.task, "entity": None, "suggested_by": "ai"})
        have.add(title.lower())
        record["added_parts"].append(clean_title)
    return record


def build_plan(question: str, level: str, *, web: bool, providers: List[str], freshness_days: int | None = None, documents: List[str] | None = None, datasets: List[str] | None = None,
               parent: Optional[Dict[str, Any]] = None, arxiv_mode: str = "auto", interpretation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    q = " ".join(question.split())
    spec = LEVELS[level]
    terms = " ".join(content_terms(q, 8))
    intent = _intent(q)
    clean_q, removed_q = sanitize_query(q)                                              # only words of the question can ever shape an outbound query
    focus = topic_focus(clean_q)
    frame = understand(clean_q, has_documents=bool(documents or datasets))                         # who and what the question is about, before anything is searched
    follow = _carry_over(focus, clean_q, parent)
    about_data = bool(datasets) and bool(re.search(r"\b(dataset|datasets|data|file|files|table|spreadsheet|csv)\b", q, re.I))
    entities = _entities(q) if (level == "comparative" or intent == "comparison") and not about_data else []        # "compare this data with outside sources" has no two named things to look up
    notes: List[str] = []
    if level == "comparative" and len(entities) < 2 and not about_data:
        notes.append("The question does not name two things to compare in a way that could be told apart reliably, so it is treated as a topic overview. Name them like 'compare A and B'.")
    subtopics: List[Dict[str, str]] = []
    if level == "multi_step":
        steps = _steps(q) or [q, f"{terms} evidence", f"{terms} limitations"]
        for i, step in enumerate(steps[: spec["subtopics"]], start=1):
            subtopics.append({"id": f"t{i}", "title": f"Step {i}: {step[:90]}", "query": " ".join(content_terms(step, 8)) or terms, "purpose": "answer this part, using what earlier steps found", "facet": "",
                              "topic_terms": topic_focus(step).terms or focus.terms})
    elif len(frame.identities) >= 2 and not parent and not about_data and level not in ("basic", "multi_step") and (outline := build_outline(frame, spec, focus_phrases=focus.phrases)):
        subtopics.extend(outline)                                                       # two named people or things of the same kind: each gets its own parts, so their histories are never merged
    elif entities and len(entities) >= 2:
        for i, e in enumerate(entities, start=1):
            subtopics.append({"id": f"t{i}", "title": e, "query": e, "purpose": f"what the sources say about {e}", "facet": "", "topic_terms": topic_focus(e).terms or [w.lower() for w in e.split()]})
        subtopics.append({"id": f"t{len(subtopics) + 1}", "title": "Differences and similarities", "query": " ".join(entities[:2]) + " difference similarity comparison", "purpose": "where they differ or agree",
                          "facet": "", "topic_terms": [w for e in entities[:2] for w in topic_focus(e).terms]})
    elif not parent and not about_data and level not in ("basic", "multi_step") and (outline := build_outline(frame, spec, focus_phrases=focus.phrases)):
        subtopics.extend(outline)                                                       # an outline chosen by the kind of question (a ruler's reign, causes, consequences, a comparison ...)
        if frame.domain != "history" and len(subtopics) < spec["subtopics"] and frame.task != "comparison":
            have = {st["title"].lower() for st in subtopics}
            for extra_ in generic_outline(level, focus.phrases, q, spec):                # a deeper level than the outline needs adds the general angles the level promises
                if len(subtopics) >= spec["subtopics"]:
                    break
                if extra_["title"].lower() not in have:
                    extra_["id"] = f"t{len(subtopics) + 1}"
                    subtopics.append(extra_)
    elif parent and not about_data:
        # A follow-up is one narrow question about material already found. It is answered as asked: the level's fixed outline (definition, how it works, ...) would re-ask things the
        # person already has and leave the actual question without a part of its own.
        subtopics.append({"id": "t1", "title": "Answer", "query": terms or q[:80], "purpose": "answer the follow-up question", "facet": "", "topic_terms": list(focus.terms)})
    elif level == "topic" and not about_data:
        subtopics.extend(generic_outline(level, focus.phrases, q, spec)[: spec["subtopics"]])
        for st in subtopics:
            st["query"] = (terms + " " + " ".join(st["vocab"][:3])).strip()
            st["topic_terms"] = list(focus.terms)
            st.pop("hint", None)                                                       # the generic outline keeps the earlier per-facet search phrases
    elif level in TEMPLATES or level == "comparative":
        for i, (title, extra) in enumerate(TEMPLATES.get(level, TEMPLATES["topic"])[: spec["subtopics"]], start=1):
            subtopics.append({"id": f"t{i}", "title": title, "query": (terms + " " + extra).strip(), "purpose": title.lower(), "facet": title, "topic_terms": list(focus.terms)})
    else:
        subtopics.append({"id": "t1", "title": "Answer", "query": terms or q[:80], "purpose": "answer the question", "facet": "", "topic_terms": list(focus.terms)})
    for st in subtopics:
        st.setdefault("scope", "general")
    interpretation_record = apply_interpretation(subtopics, interpretation, frame)
    selected_datasets = list(datasets or [])
    if parent and parent.get("datasets_left_out"):
        n = int(parent["datasets_left_out"])
        notes.append(f"The {n} dataset{'s' if n != 1 else ''} used for the earlier question {'were' if n != 1 else 'was'} not used again, because this follow-up is not about the data. Ask about the data to include {'them' if n != 1 else 'it'}.")
    if len(selected_datasets) == 1 and re.search(r"\b(compare[sd]?|comparison|versus|vs\.?)\b.*\b(datasets?|files?|tables?)\b|\b(two|both|2) (datasets|files|tables)\b", q, re.I):
        notes.append("The question asks to compare datasets, but only one is selected. Select a second dataset to compare them; the selected one is described on its own.")
    if selected_datasets:
        ds = _dataset_subtopics(q, selected_datasets, 1)
        if web or documents:
            subtopics = [*ds, *subtopics]
        else:
            subtopics = ds                                                                # a question about the data alone does not need the generic "Answer" part, which would only report a gap
    web_queries: List[Dict[str, Any]] = []
    arxiv_mode = arxiv_mode if arxiv_mode in ARXIV_MODES else "auto"
    skipped_providers: List[Dict[str, str]] = []
    if web and providers:
        providers, skipped_providers = provider_fit(frame, providers, arxiv_mode)
        for sk in skipped_providers:
            notes.append(f"{sk['provider']}: {sk['reason']}")
    if web and providers:
        covered = set()
        wiki_first = True
        for st in subtopics:
            if st.get("scope") == "dataset":
                continue
            st_focus = topic_focus(st["title"]) if (entities and st["title"] in entities) else focus
            if entities and st["title"] not in entities and st["title"] != "Differences and similarities":
                st_focus = focus
            if st["title"] == "Differences and similarities":
                # the two things themselves, without a shared qualifier such as "for India" (which would otherwise appear twice in one search phrase)
                firsts = [(topic_focus(e).phrases or [e])[0] for e in entities[:2]]
                st_focus = Focus(phrases=firsts, terms=[w.lower() for f_ in firsts for w in f_.split()], natural=" ".join(firsts)[:140])
            if not st_focus.phrases:
                continue
            chosen = providers_for(st.get("facet", ""), providers)
            by_provider: Dict[str, List[Dict[str, str]]] = {}
            for p in chosen:
                if st.get("vocab") is not None and (st.get("hint") is not None):
                    variants = [{k: v[k] for k in v} for v in queries_for(p, frame, st, st_focus, clean_q if p == "searxng" else "", first=(p == "wikipedia" and wiki_first))]
                    if p == "wikipedia":
                        wiki_first = False
                else:
                    variants = [{"query": v["query"][:300], "role": v["role"]} for v in build_provider_queries(p, st_focus, clean_q if p == "searxng" else "", st.get("facet", ""))]
                variants = [v for v in variants if (p, v["query"].lower()) not in covered]
                if variants:
                    by_provider[p] = variants
                    covered.update((p, v["query"].lower()) for v in variants)
            if not by_provider:
                continue
            first = next(iter(by_provider.values()))[0]["query"]
            web_queries.append({"subtopic": st["id"], "query": first, "providers": list(by_provider), "removed": removed_q, "by_provider": by_provider})
    if web and providers and not web_queries and not selected_datasets:
        notes.append("The question has no topic words to search for outside (for example it only refers to 'this document'), so no web search is planned.")
    elif web and providers and not web_queries:
        notes.append("The question asks only about your data, so nothing is sent outside. Add a topic (for example 'and what do aviation sources say?') to include outside research.")
    tasks: List[Dict[str, Any]] = [{"id": "k1", "kind": "context", "depends_on": [], "description": "Collect what is in your project that relates to the question (documents, datasets, saved analyses and models)."}]
    last = "k1"
    if selected_datasets:
        tasks.append({"id": "k1b", "kind": "dataset_analysis", "depends_on": ["k1"], "description": f"Calculate verified statistics on {len(selected_datasets)} selected dataset(s) with the platform's own analysis and model services "
                                                                                             "(no data values leave the platform, and no number in the answer is written by a language model)."})
        last = "k1b"
    if web_queries:
        n_req = sum(len(v) for q_ in web_queries for v in q_["by_provider"].values())
        tasks.append({"id": "k2", "kind": "web_search", "depends_on": [last], "description": f"Search {', '.join(providers)} with up to {n_req} approved search phrase{'s' if n_req != 1 else ''}, then open the most relevant Wikipedia articles, "
                                                                                          "arXiv papers and web pages and read the sections that bear on the question."})
        last = "k2"
    tasks.append({"id": "k3", "kind": "extract", "depends_on": [last], "description": "Pick the passages that answer each subtopic and keep their exact wording and location."})
    before_reconcile = "k3"
    if spec["rounds"] > 1:
        tasks.append({"id": "k3b", "kind": "follow_up", "depends_on": ["k3"], "description": "Look again through the material already collected for any part that found nothing, with a looser match. No web request beyond the ones you approved is made."})
        before_reconcile = "k3b"
    tasks += [{"id": "k4", "kind": "reconcile", "depends_on": [before_reconcile], "description": "Group agreeing statements, flag disagreements, separate facts from uncertain or predicted statements, and keep calculated results apart from outside sources."},
              {"id": "k5", "kind": "conclude", "depends_on": ["k4"], "description": "Write the structured answer from quoted evidence, then check every citation and warn about gaps."}]
    plan = {"question": q, "level": level, "level_label": spec["label"], "intent": intent, "task": frame.task, "domain": frame.domain, "entities": entities, "understanding": frame.to_dict(),
            "clarification": frame.clarification.to_dict() if frame.clarification else None, "interpretation": interpretation_record, "focus": {"phrases": focus.phrases, "terms": focus.terms}, "subtopics": subtopics, "tasks": tasks,
            "web": {"enabled": bool(web_queries), "providers": providers if web_queries else [], "queries": web_queries, "max_pages": spec["web_pages"], "freshness_days": freshness_days,
                    "arxiv_mode": arxiv_mode, "skipped_providers": skipped_providers},
            "local": {"documents": documents or [], "datasets": selected_datasets, "passages_per_subtopic": spec["passages"], **({"reuse_sources_of": follow["of"]} if follow.get("of") else {})},
            "min_sources": spec["min_sources"], "rounds": spec["rounds"], **({"follow_up": follow} if follow else {}),
            "limitations": [*notes, "Findings quote the sources; the platform does not add facts of its own.", "A search returns what a provider ranks highly, not everything that exists.",
                            *(["Wikipedia is an encyclopedia (a tertiary source) and arXiv papers are preprints that may not be peer reviewed; both are labelled that way wherever they appear."] if {"wikipedia", "arxiv"} & set(providers if web_queries else []) else []),
                            *(["Results calculated from your data describe your data. Outside sources add context; they never change a calculated result, and an association is not proof of a cause."] if selected_datasets else []),
                            *(["This is a forecast question: predictions are shown as predictions, never as facts."] if intent == "forecast" else []),
                            *(["'Why' questions are answered with what sources say; a cause is only stated if a source states it."] if intent == "explanation" else [])]}
    plan["plan_hash"] = plan_digest(plan)
    return plan


def plan_digest(plan: Dict[str, Any]) -> str:
    """Fingerprint of everything the person approves: the question, the depth, the parts, the exact web queries and providers, and which of their files are searched."""
    body = {k: plan[k] for k in ("question", "level", "subtopics", "web", "local")}
    if plan.get("understanding"):
        body["understanding"] = plan["understanding"]                                  # the entities that decide which sources are accepted are part of what the person approves
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
