"""Topic classification by transparent keyword rules.

This is deliberately NOT a trained model: every label comes with the words that triggered it, so the person can see (and disagree with) the reason. Titles count three times as much as the
summary. An article that matches nothing is labelled "general" rather than forced into a topic."""
from __future__ import annotations

from typing import Any, Dict, List

from ..textkit import tokenize

METHOD = "keyword-rules/1"

LEXICON: Dict[str, Dict[str, str]] = {
    "economy": {"label": "Economy and business", "terms": "economy economic inflation gdp recession growth market markets stocks shares bank banks interest rate rates trade tariff budget tax taxes unemployment jobs wages earnings profit revenue investors currency rupee dollar exports imports"},
    "politics": {"label": "Politics and government", "terms": "government election elections minister ministers parliament president policy bill vote voters party coalition opposition senate congress legislation cabinet lawmakers campaign"},
    "law": {"label": "Law and crime", "terms": "police court judge arrest arrested trial crime lawsuit prosecutors verdict fraud investigation convicted sentence charged suspect robbery"},
    "technology": {"label": "Technology", "terms": "technology software artificial intelligence app startup cyber chip semiconductor internet platform digital robot cloud smartphone algorithm hackers"},
    "science": {"label": "Science", "terms": "research study scientists researchers experiment discovery space nasa telescope physics biology genome laboratory astronomers"},
    "health": {"label": "Health", "terms": "health hospital disease vaccine virus patients doctors medical cancer outbreak treatment drug clinic epidemic infection"},
    "environment": {"label": "Environment and climate", "terms": "climate emissions carbon pollution renewable solar wind forest wildlife biodiversity warming environmental conservation"},
    "disasters": {"label": "Disasters and weather", "terms": "flood floods earthquake storm cyclone hurricane wildfire drought heatwave rainfall landslide tsunami evacuated evacuation monsoon"},
    "sport": {"label": "Sport", "terms": "match tournament league cricket football coach player players score olympic championship goal wicket tennis final"},
    "education": {"label": "Education", "terms": "school schools university students exam exams teachers curriculum college scholarship admissions"},
    "international": {"label": "International affairs", "terms": "war conflict ceasefire sanctions treaty diplomat diplomats embassy summit border troops missile foreign refugees"},
    "transport": {"label": "Transport and infrastructure", "terms": "railway highway airport metro bridge road flight airline port construction train traffic infrastructure"},
    "culture": {"label": "Culture and entertainment", "terms": "film movie music festival actor actress award theatre book artist museum concert series"},
    "energy": {"label": "Energy", "terms": "oil gas electricity power grid fuel petrol coal nuclear refinery pipeline"},
}
TOPIC_LABELS = {k: v["label"] for k, v in LEXICON.items()}
TOPIC_LABELS["general"] = "General"
_STEMMED = {k: {} for k in LEXICON}
for _k, _v in LEXICON.items():
    for _w in _v["terms"].split():
        _t = (tokenize(_w, min_len=2) or [None])[0]
        if _t:
            _STEMMED[_k].setdefault(_t, _w)


def classify(title: str, summary: str, *, max_topics: int = 3) -> Dict[str, Any]:
    """{topics: [{topic, label, strength, matched, share}], method, note}. `matched` is the basis of the label."""
    counts: Dict[str, Dict[str, float]] = {}
    for weight, text in ((3.0, title), (1.0, summary)):
        for tok in tokenize(text or "", min_len=2):
            for topic, table in _STEMMED.items():
                if tok in table:
                    counts.setdefault(topic, {})
                    counts[topic][table[tok]] = counts[topic].get(table[tok], 0.0) + weight
    scored = sorted(((sum(m.values()), t, m) for t, m in counts.items()), key=lambda x: (-x[0], x[1]))
    total = sum(s for s, _, _ in scored)
    out: List[Dict[str, Any]] = []
    for score, topic, matched in scored[:max_topics]:
        if score < 3.0 and len(scored) > 1 and out:
            break                                                                              # a single weak mention is not a second topic
        out.append({"topic": topic, "label": LEXICON[topic]["label"], "strength": "strong" if len(matched) >= 3 else "weak" if len(matched) == 1 else "moderate",
                    "matched": sorted(matched)[:8], "share": round(score / total, 3) if total else 0.0})
    if not out:
        out = [{"topic": "general", "label": "General", "strength": "none", "matched": [], "share": 1.0}]
    return {"topics": out, "method": METHOD, "note": "Labels come from keyword rules, shown as the words that matched. They are a guide to browsing, not a judgement of what the article is about."}
