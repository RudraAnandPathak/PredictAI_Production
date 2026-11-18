"""The parts a research question is broken into, chosen by what KIND of question it is (an overview of a ruler's reign, the causes of an event, a comparison, statistics, the method of a paper)
instead of one fixed outline for everything.

An outline is a set of questions to ask of the sources, not a set of findings: nothing in it is claimed to be true. A part for which no source says anything is reported as a gap. Each part carries
the vocabulary that a passage on that part would use (`vocab`). The vocabulary is used in two places only: to shape the search phrase for that part, and to judge whether a source or sentence
is about that part. It is never shown as a finding.

Pure data and functions, no network."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .understanding import Entity, Frame

Part = Dict[str, Any]


def _p(title: str, vocab: str, purpose: str, hint: str = "") -> Part:
    """`hint` is one or two words added to an outbound search phrase. Search phrases are kept to four distinct content words in all, so a phrase is never mistaken for pasted text."""
    return {"title": title, "vocab": vocab.split(), "purpose": purpose, "hint": hint}


# --- history, politics and society -------------------------------------------------------------------------------------------------------------------------------------------
STATE_UNDER_RULER: List[Part] = [
    _p("Political system and royal authority", "government politics authority power monarchy king crown ministers court council parlement absolute reform constitution law rule royal", "how power was held and used", "government"),
    _p("Economic conditions, debt and taxation", "economy economic finance financial debt deficit taxation tax taxes revenue budget treasury loans trade harvest bread prices poverty", "the state of the economy and the public finances", "finances taxation"),
    _p("Society and the estates", "society social estates nobility clergy peasants bourgeoisie class privilege inequality population towns labour third estate", "how society was organised and who bore what", "estates society"),
    _p("Foreign policy, wars and their financial consequences", "war wars foreign diplomacy treaty army navy alliance borrowing cost military colonies", "foreign policy and what it cost", "foreign policy"),
    _p("Crises, reform attempts and what they led to", "crisis revolution unrest opposition reform failure collapse estates general causes assembly overthrow", "the problems of the reign and where they led", "crisis"),
]
STATE_PERIOD: List[Part] = [
    _p("Political system and government", "government politics authority power rulers ministers parliament assembly law institutions constitution", "how the state was governed", "government"),
    _p("Economy, finance and taxation", "economy economic finance debt taxation tax revenue trade agriculture prices wages", "the economy and the public finances", "economy taxation"),
    _p("Society and social structure", "society social class nobility clergy peasants workers inequality population towns privilege", "how society was organised", "society"),
    _p("Foreign policy and wars", "war wars foreign diplomacy treaty army navy alliance military", "foreign policy and conflicts", "wars"),
    _p("Crises, reforms and outcomes", "crisis unrest opposition reform collapse revolution causes outcome", "the crises and what followed", "crisis"),
]
CAUSES_HISTORY: List[Part] = [
    _p("Political causes", "political government monarchy authority reform legitimacy crisis conflict power parlement assembly ministers", "political factors", "political causes"),
    _p("Economic causes", "economic economy debt taxation finance prices harvest famine trade bankruptcy deficit revenue", "economic factors", "economic causes"),
    _p("Social causes", "social class inequality privilege estates grievances nobility peasants bourgeoisie population", "social factors", "social causes"),
    _p("Intellectual and ideological causes", "ideas enlightenment philosophy ideology philosophes rights liberty press writers criticism reason", "ideas and ideology", "Enlightenment"),
    _p("Long-term causes and immediate triggers", "trigger immediate long-term spark events crisis summer harvest meeting estates general bastille", "what made the crisis break out when it did", "causes"),
]
CAUSES_GENERIC: List[Part] = [
    _p("Main factors", "factor cause reason because led contributed", "the factors sources name", "causes"),
    _p("How the factors worked", "mechanism process effect influence led resulted", "how the factors had their effect", "mechanism"),
    _p("Evidence for these explanations", "evidence study data finding observed measured", "what supports the explanations", "evidence"),
    _p("Long-term conditions and immediate triggers", "trigger immediate long-term underlying precipitated", "underlying conditions versus triggering events", "triggers"),
]
CONSEQUENCES: List[Part] = [
    _p("Immediate consequences", "immediate short-term result outcome aftermath followed", "what followed at once", "aftermath"),
    _p("Political consequences", "political government power law institutions regime state", "political results", "political consequences"),
    _p("Economic consequences", "economic economy cost trade finance debt prices", "economic results", "economic consequences"),
    _p("Social consequences", "social society class population culture lives", "social results", "social consequences"),
    _p("Long-term legacy", "legacy long-term influence later historians memory today", "lasting effects", "legacy"),
]
STATISTICS: List[Part] = [
    _p("Figures and measurements", "number figures statistics percent rate total average estimate measured", "the numbers sources give", "statistics"),
    _p("Where the figures come from", "source survey census data estimate method reported", "how the figures were obtained", "sources data"),
    _p("Changes over time and comparisons", "trend increase decrease change growth decline compared", "how the figures changed", "trends"),
    _p("Caveats and uncertainty", "uncertainty error margin limitation estimate disputed revised", "how far the figures can be trusted", "uncertainty"),
]
METHODOLOGY: List[Part] = [
    _p("Problem and goal", "problem task goal objective motivation question aim propose", "what the work sets out to do", "problem"),
    _p("Data", "data dataset corpus samples training benchmark collection preprocessing", "what data was used", "dataset"),
    _p("Method or model", "method model architecture approach algorithm network layer training objective loss procedure", "how the method works", "method"),
    _p("Experiments and evaluation", "experiment evaluation results baseline metric accuracy ablation comparison", "how it was tested", "experiments"),
    _p("Limitations", "limitation limitations future work weakness assumption caveat", "what the authors say it cannot do", "limitations"),
]
OVERVIEW_GENERIC: List[Part] = [
    _p("Overview", "", "a first account of the subject"),
    _p("Background and definitions", "definition background origin history meaning", "what it is and where it comes from", "definition"),
    _p("Key facts and figures", "facts statistics data figures numbers", "the main facts", "facts"),
    _p("Recent developments", "recent news update latest new", "what has changed lately", "recent"),
]
RECENCY = ("recent", "latest", "news", "today", "current", "now", "2024", "2025", "2026", "update", "trend", "emerging")


def _entity_phrase(entity: Optional[Entity]) -> str:
    return entity.name if entity else ""


def _part_dicts(parts: Sequence[Part], first_id: int, *, anchor: str, task: str, entity: Optional[Entity] = None, scope: str = "general") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(parts, start=first_id):
        query = " ".join([anchor] + p["vocab"][:5]).strip()
        item: Dict[str, Any] = {"id": f"t{i}", "title": p["title"], "query": query, "purpose": p["purpose"], "facet": p["title"], "topic_terms": [], "vocab": list(p["vocab"]), "hint": p.get("hint", ""),
                                "scope": scope, "task": task}
        if entity is not None:
            item["entity"] = entity.key
        out.append(item)
    return out


def build_outline(frame: Frame, level_spec: Mapping_like, *, focus_phrases: Sequence[str], parent: bool = False) -> List[Dict[str, Any]]:
    """The subtopics for this question. `level_spec['subtopics']` is how many parts the chosen depth allows; a question about a ruler or a state needs the five parts that cover it."""
    n_allowed = int(level_spec["subtopics"])
    anchor = frame.identities[0].name if frame.identities else (frame.subjects[0].name if frame.subjects else (focus_phrases[0] if focus_phrases else ""))
    place = frame.places[0].name if frame.places else ""
    history = frame.domain == "history"
    task = frame.task
    if parent:
        return []                                                                           # a follow-up is answered as asked (see planner)
    # --- comparison of two identity entities
    ids = frame.identities
    if task == "comparison" and len(ids) >= 2:
        out: List[Dict[str, Any]] = []
        sides = ids[:3]
        for i, e in enumerate(sides, start=1):
            parts = STATE_UNDER_RULER if history else OVERVIEW_GENERIC
            reign = ["reign", "ruled", "rule", "throne", "king", "restoration", "charter", "exile", "succeeded", "policy", "policies", "period"] if history else []
            vocab = sorted({w for p in parts for w in p["vocab"]} | set(reign))[:80]
            q = " ".join([e.name, place, "reign" if history else "", *([] if history else vocab[:4])]).strip()
            out.append({"id": f"t{i}", "title": f"{e.name}: rule and policies" if history else f"{e.name}", "query": q, "purpose": f"what the sources say about {e.name}, and only about {e.name}", "facet": "",
                        "topic_terms": [], "vocab": vocab, "hint": "", "scope": "general", "task": task, "entity": e.key})
        names = " ".join(e.name for e in sides)
        contrast = ["difference", "similarity", "compared", "contrast", "unlike", "whereas", "while"]
        out.append({"id": f"t{len(out) + 1}", "title": "Differences and similarities", "query": f"{names} {place} difference similarity".strip(), "purpose": "where they differ or agree, using only passages that keep them apart",
                    "facet": "", "topic_terms": [], "vocab": contrast + vocab, "hint": "differences", "scope": "general", "task": task, "entity": "*"})
        return out
    if task == "methodology":
        return _part_dicts(METHODOLOGY[:max(n_allowed, 4)], 1, anchor=anchor or " ".join(focus_phrases[:1]), task=task)
    if task == "statistics":
        return _part_dicts(STATISTICS[:n_allowed], 1, anchor=" ".join(x for x in (anchor, place) if x) or " ".join(focus_phrases[:2]), task=task)
    if task == "causes":
        parts = CAUSES_HISTORY if history else CAUSES_GENERIC
        a = " ".join(x for x in (anchor, place) if x) or " ".join(focus_phrases[:2])
        return _part_dicts(parts[:max(n_allowed, len(parts))] if history else parts[:n_allowed], 1, anchor=a, task=task)
    if task == "consequences":
        a = " ".join(x for x in (anchor, place) if x) or " ".join(focus_phrases[:2])
        return _part_dicts(CONSEQUENCES[:n_allowed], 1, anchor=a, task=task)
    if history and (ids or frame.period or frame.subjects):
        parts = STATE_UNDER_RULER if ids else STATE_PERIOD
        a = " ".join(x for x in (anchor, place) if x) or " ".join(focus_phrases[:2])
        # an economic or social focus in the question narrows the outline to the parts that bear on it
        low = frame.question.lower()
        if any(w in low for w in ("econom", "financ", "tax", "debt", "trade")) and not ids:
            parts = [STATE_PERIOD[1], STATE_PERIOD[2], STATE_PERIOD[0]]
            parts = [{**parts[0], "title": "Financial and taxation evidence", "vocab": parts[0]["vocab"] + ["bankruptcy", "borrowing", "interest"]}] + parts[1:]
            return _part_dicts(parts[:max(n_allowed, 3)], 1, anchor=a, task=task)
        return _part_dicts(parts[:max(n_allowed, 5)] if n_allowed > 1 else parts[:1], 1, anchor=a, task=task)
    return []


Mapping_like = Dict[str, Any]


def generic_outline(level: str, focus_phrases: Sequence[str], question: str, level_spec: Mapping_like) -> List[Dict[str, Any]]:
    """The earlier fixed outline for questions that need no special one. 'Recent developments' is kept only when the question asks about recent matters."""
    parts = OVERVIEW_GENERIC
    low = question.lower()
    if not any(w in low for w in RECENCY):
        parts = [p for p in parts if p["title"] != "Recent developments"]
    return _part_dicts(parts[: level_spec["subtopics"]], 1, anchor=" ".join(focus_phrases[:2]), task="overview")
