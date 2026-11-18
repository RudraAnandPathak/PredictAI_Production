"""What a question asks of a selected dataset, decided from its words alone (no model, no data). Used by the planner to lay out the plan and by the dataset researcher to choose which
verified analytics to run. The set is closed: every intent maps to operations that already exist in the Phase 4 and Phase 5 services, so a question can never cause a calculation
that those services do not offer."""
from __future__ import annotations

import re
from typing import Dict, List

INTENT_PATTERNS: Dict[str, "re.Pattern[str]"] = {name: re.compile(rx, re.I) for name, rx in {
    "overview": r"\b(tell us|tells us|tell me|summar\w*|overview|describe[sd]?|contains?|what is in|what's in|key findings?|most important|main findings?|findings?|insights?|learn from|about this)\b",
    "relationships": r"\b(relationships?|correlat\w*|associat\w*|relat(?:ed|es|ion\w*)|linked?|connect\w*|driv(?:e|es|en|ers?)|factors?|influenc\w*|affect\w*|determin\w*|depend\w*|strongest)\b",
    "quality": r"\b(missing|nulls?|blank|incomplete|suspicious|duplicates?|invalid|data quality|errors?|anomal\w*|outliers?|unusual|odd|strange|unexpected|clean)\b",
    "groups": r"\b(groups?|segments?|differ\w*|difference|differences|between|by (?:region|category|type|class|gender|country|plan|segment))\b",
    "prediction": r"\b(predict\w*|forecast\w*|models?|feature importance|important (?:variables|features|predictors)|useful for prediction|predictors?|what[- ]if|scenarios?)\b",
    "distribution": r"\b(distribution\w*|skew\w*|spread|shape|histogram|typical|range of)\b",
    "explain": r"\b(explain (?:these|the|my|this) (?:statistical )?(?:results?|numbers|output)|interpret\w*|p[- ]values?|significan\w*|confidence interval)\b",
}.items()}
DEFAULT_INTENTS = ["overview", "relationships", "quality"]
TITLES = {
    "overview": "What the dataset contains",
    "relationships": "Strongest relationships in the data",
    "target": "What is associated with {target}",
    "quality": "Missing, suspicious or unusual values",
    "groups": "Differences between groups",
    "prediction": "Predictive models and useful variables",
    "distribution": "How the values are distributed",
    "explain": "Statistical results already in the project",
    "compare_datasets": "Comparing the datasets",
}
MAX_INTENTS = 5


def dataset_intents(question: str, *, datasets: int = 1) -> List[str]:
    """The intents a dataset question carries, in a fixed order. Nothing matched means the person asked something open ("what does this dataset tell us?"), which gets the default trio."""
    found = [name for name, rx in INTENT_PATTERNS.items() if rx.search(question or "")]
    if datasets >= 2 and re.search(r"\b(compare[sd]?|comparison|versus|vs|difference|differences|similar\w*|both)\b", question or "", re.I):
        found.append("compare_datasets")
    if "compare_datasets" in found and "groups" in found and not re.search(r"\b(groups?|segments?)\b", question or "", re.I):
        found.remove("groups")                                                              # "differences between the two datasets" compares datasets, it does not ask for group tests
    if not found or set(found) <= {"overview", "compare_datasets"}:
        found = [*DEFAULT_INTENTS, *found]
    order = ["overview", "relationships", "groups", "prediction", "quality", "distribution", "explain", "compare_datasets"]
    return [i for i in order if i in found][:MAX_INTENTS]


DATA_WORDS = re.compile(r"\b(datasets?|data|files?|tables?|spreadsheets?|csv|columns?|rows?|variables?|records?)\b", re.I)


def asks_about_data(question: str) -> bool:
    """Whether a question is about the person's data: it names data (dataset, columns, rows ...) or matches one of the dataset intents (relationships, groups, quality ...).
    A follow-up that is not about the data (for example, about an article already read) does not run the calculations again."""
    q = question or ""
    return bool(DATA_WORDS.search(q)) or any(rx.search(q) for rx in INTENT_PATTERNS.values())
