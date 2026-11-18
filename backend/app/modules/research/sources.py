"""Source records: reliability indicators and freshness.

Indicators describe a source; they do not prove it right or wrong. Each one lists its basis so the person can disagree with it. There is deliberately no numeric 'trust score'."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..webaccess.urlguard import registrable_domain

OFFICIAL_SUFFIXES = (".gov", ".mil", ".int", ".gov.uk", ".gov.in", ".nic.in", ".gov.au", ".gc.ca", ".europa.eu")
ACADEMIC_SUFFIXES = (".edu", ".ac.uk", ".ac.in", ".edu.au", ".ac.jp", ".edu.in")


def domain_class(domain: Optional[str], source_type: str = "web") -> str:
    d = (domain or "").lower()
    if source_type == "preprint" or d.endswith("arxiv.org"):
        return "preprint"
    if source_type == "encyclopedia" or d.endswith("wikipedia.org"):
        return "encyclopedia"
    if d.endswith(OFFICIAL_SUFFIXES):
        return "official"
    if d.endswith(ACADEMIC_SUFFIXES):
        return "academic"
    return "general web" if d else "unknown"


def source_class(src: Dict[str, Any]) -> str:
    """The machine-readable kind of a source: document | calculated | model | earlier_research | wikipedia | arxiv_preprint | web."""
    kind = src.get("kind")
    if kind == "document":
        return "document"
    if kind in ("dataset", "analysis"):
        return "calculated"
    if kind == "model":
        return "model"
    if kind == "evidence":
        return "earlier_research"
    if src.get("provider") == "wikipedia" or src.get("domain_class") == "encyclopedia":
        return "wikipedia"
    if src.get("provider") == "arxiv" or src.get("domain_class") == "preprint":
        return "arxiv_preprint"
    return "web"


def source_label(src: Dict[str, Any]) -> str:
    """How a source is described to the person, always naming what kind of evidence it is."""
    cls = source_class(src)
    meta = src.get("meta") or {}
    if cls == "document":
        return "Your document"
    if cls == "calculated":
        return "Calculated from your data"
    if cls == "model":
        return "Model estimate (a prediction, not a fact)"
    if cls == "earlier_research":
        return "Earlier research in this project"
    if cls == "wikipedia":
        return "Wikipedia (encyclopedia: a tertiary source, edited by volunteers)"
    if cls == "arxiv_preprint":
        if meta.get("peer_review") == "published_claim":
            return "arXiv preprint (the authors list a later publication; PredictAI has not verified it was peer reviewed)"
        return "arXiv preprint (not peer reviewed)"
    return "General web page"


def parse_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            d = datetime.fromisoformat(v) if fmt is None else datetime.strptime(value.strip(), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def freshness(published: Optional[str], now: datetime, max_age_days: Optional[int]) -> Dict[str, Any]:
    d = parse_date(published)
    if d is None:
        return {"published": None, "age_days": None, "label": "undated", "outdated": None, "note": "The source gives no publication date, so how current it is cannot be judged."}
    age = max(0, (now - d).days)
    label = "current" if age <= 30 else "recent" if age <= 365 else "older"
    return {"published": d.date().isoformat(), "age_days": age, "label": label, "outdated": (age > max_age_days) if max_age_days else None,
            "note": f"Published about {age} days before it was read." + (f" That is older than the {max_age_days}-day limit you set." if max_age_days and age > max_age_days else "")}


def reliability(src: Dict[str, Any], corroborated_by: int = 0) -> Dict[str, Any]:
    signals: List[Dict[str, str]] = []

    def add(name: str, effect: str, basis: str) -> None:
        signals.append({"name": name, "effect": effect, "basis": basis})
    kind, dclass = src.get("kind"), src.get("domain_class")
    if kind in ("document", "dataset"):
        add("your own file", "0", "You provided it. PredictAI has not checked whether its content is accurate.")
    elif kind in ("analysis", "model"):
        add("calculated from your data", "0", "Computed from your data by PredictAI; it is only as good as the data and the method.")
    else:
        if dclass in ("official", "academic"):
            add(f"{dclass} domain", "+", "The address ends in a government or academic suffix. That is a hint about who runs the site, not proof of accuracy.")
        if dclass == "preprint":
            add("preprint", "-", (src.get("meta") or {}).get("peer_review_note") or "Preprints may not have been peer reviewed.")
        if dclass == "encyclopedia":
            add("community-edited encyclopedia", "-", "Anyone can edit it; it is a tertiary source: good for background and for finding primary sources, not a primary source itself.")
        scope = src.get("content_scope") or ""
        if "abstract only" in scope or "summary only" in scope:
            add("only part was read", "-", "Only the " + ("abstract" if "abstract" in scope else "search-result summary") + " could be read, not the full text, so details and limits of the work are not known.")
        if src.get("url", "").startswith("https://"):
            add("secure connection", "+", "The page was fetched over HTTPS.")
        add("author named", "+" if src.get("author") else "-", "An author or publisher is named." if src.get("author") else "No author is named on the page.")
        fr = src.get("freshness") or {}
        add("dated", "+" if fr.get("published") else "-", "A publication date is given." if fr.get("published") else "No publication date is given.")
        if fr.get("outdated"):
            add("older than your limit", "-", fr.get("note", ""))
        if corroborated_by >= 1:
            add("corroborated", "+", f"{corroborated_by} other source(s) in this research state something similar.")
        else:
            add("not corroborated", "-", "No other source in this research states something similar.")
        if src.get("hidden_blocks_removed"):
            add("hidden content removed", "-", f"{src['hidden_blocks_removed']} hidden block(s) on the page were ignored.")
    pos, neg = sum(s["effect"] == "+" for s in signals), sum(s["effect"] == "-" for s in signals)
    level = "your file" if kind in ("document", "dataset") else "calculated" if kind in ("analysis", "model") else "stronger signals" if pos - neg >= 2 else "weak signals" if neg - pos >= 1 else "mixed signals"
    return {"level": level, "signals": signals, "note": "These are indicators, not a verdict. Check important claims against the source itself."}
