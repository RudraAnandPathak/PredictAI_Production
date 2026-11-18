"""Report templates: which sections a kind of report has and how it is laid out. A template only chooses and orders sections; the words come from the evidence.
User-created templates use the same shape (see `validate_spec`)."""
from __future__ import annotations

from typing import Any, Dict, List

from ..p7 import choice, clean, invalid

SECTION_KEYS = {"summary": "Executive summary", "question": "Research question", "scope": "Scope", "objectives": "Objectives", "method": "Methodology", "background": "Background",
                "findings": "Detailed findings", "analysis": "Analysis", "discussion": "Discussion", "limitations": "Limitations", "unresolved": "Unresolved questions",
                "conclusion": "Conclusion", "recommendations": "Recommendations", "references": "References", "appendix": "Appendix: evidence register"}
DEFAULT_SETTINGS = {"audience": "general", "tone": "neutral", "length": "standard", "citation_style": "apa", "page": {"size": "A4", "margin_mm": 20}, "font": {"family": "Helvetica", "size": 11},
                    "header": "", "footer": "Page {page}", "branding": {"name": "", "show": False}}
AUDIENCES = ("general", "executive", "technical", "academic", "student")
TONES = ("neutral", "formal", "plain")
LENGTHS = ("brief", "standard", "detailed")
PAGE_SIZES = ("A4", "Letter")
FONTS = ("Helvetica", "Times", "Courier")


def _t(key: str, name: str, rtype: str, sections: List[str], about: str, **settings: Any) -> Dict[str, Any]:
    return {"key": key, "name": name, "report_type": rtype, "version": 1, "about": about, "spec": {"sections": sections, "settings": {**DEFAULT_SETTINGS, **settings}}}


SYSTEM_TEMPLATES: List[Dict[str, Any]] = [
    _t("research_report", "Research report", "research", ["summary", "question", "scope", "method", "findings", "analysis", "discussion", "limitations", "unresolved", "conclusion", "references", "appendix"], "A report of one saved research result."),
    _t("literature_review", "Academic literature review", "literature_review", ["summary", "question", "method", "background", "findings", "discussion", "limitations", "conclusion", "references"], "A review of the sources a research result read.", audience="academic", tone="formal", citation_style="apa"),
    _t("data_analysis", "Data analysis report", "data_analysis", ["summary", "objectives", "method", "findings", "analysis", "limitations", "unresolved", "conclusion", "references", "appendix"], "Profiling and analysis results for a dataset."),
    _t("predictive_modelling", "Predictive modelling report", "predictive", ["summary", "objectives", "method", "findings", "analysis", "limitations", "conclusion", "appendix"], "How a model was built, evaluated and what it can and cannot say."),
    _t("exam_intelligence", "Exam Intelligence report", "exam", ["summary", "scope", "method", "findings", "analysis", "limitations", "unresolved", "appendix"], "Curriculum coverage, topic recurrence and emphasis from past papers.", audience="student"),
    _t("scientific_research", "Scientific research report", "scientific", ["summary", "background", "objectives", "method", "findings", "discussion", "limitations", "conclusion", "references"], "A scientific write-up from research and calculations.", audience="academic", tone="formal"),
    _t("business_intelligence", "Business intelligence report", "business", ["summary", "objectives", "findings", "analysis", "recommendations", "limitations", "appendix"], "Findings and decisions for a business audience.", audience="executive", length="brief"),
    _t("operational", "Operational report", "operational", ["summary", "findings", "unresolved", "recommendations", "appendix"], "Status of work, with what is still open.", length="brief"),
    _t("model_evaluation", "Model evaluation report", "model_evaluation", ["summary", "method", "findings", "analysis", "limitations", "conclusion", "appendix"], "Validation and hold-out results for one or more model versions.", audience="technical"),
    _t("scenario_analysis", "Scenario-analysis report", "scenario", ["summary", "method", "findings", "analysis", "limitations", "unresolved", "appendix"], "Baseline versus alternative inputs, with sensitivity and uncertainty.", audience="technical"),
]
BY_KEY = {t["key"]: t for t in SYSTEM_TEMPLATES}
REPORT_TYPES = tuple(t["report_type"] for t in SYSTEM_TEMPLATES) + ("custom",)


def validate_settings(raw: Any, base: Dict[str, Any]) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    s = {**base}
    if "audience" in raw:
        s["audience"] = choice(raw["audience"], "audience", AUDIENCES)
    if "tone" in raw:
        s["tone"] = choice(raw["tone"], "tone", TONES)
    if "length" in raw:
        s["length"] = choice(raw["length"], "length", LENGTHS)
    if "citation_style" in raw:
        s["citation_style"] = choice(raw["citation_style"], "citation_style", ("apa", "mla", "chicago", "ieee", "harvard"))
    if "page" in raw:
        pg = raw["page"] if isinstance(raw["page"], dict) else {}
        margin = pg.get("margin_mm", s["page"]["margin_mm"])
        if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not 5 <= margin <= 50:
            raise invalid("page.margin_mm", "Use 5 to 50 millimetres.")
        s["page"] = {"size": choice(pg.get("size", s["page"]["size"]), "page.size", PAGE_SIZES), "margin_mm": margin}
    if "font" in raw:
        f = raw["font"] if isinstance(raw["font"], dict) else {}
        size = f.get("size", s["font"]["size"])
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not 8 <= size <= 16:
            raise invalid("font.size", "Use 8 to 16 points.")
        s["font"] = {"family": choice(f.get("family", s["font"]["family"]), "font.family", FONTS), "size": size}
    for k in ("header", "footer"):
        if k in raw:
            s[k] = clean(raw[k], k, maximum=120, required=False) or ""
    if "branding" in raw:
        b = raw["branding"] if isinstance(raw["branding"], dict) else {}
        s["branding"] = {"name": clean(b.get("name"), "branding.name", maximum=80, required=False) or "", "show": bool(b.get("show", False))}
    return s


def validate_spec(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("sections"), list) or not 1 <= len(raw["sections"]) <= 20:
        raise invalid("spec.sections", "List one to twenty sections.")
    sections = []
    for key in raw["sections"]:
        if key not in SECTION_KEYS:
            raise invalid("spec.sections", f"Unknown section '{key}'. Use: {', '.join(SECTION_KEYS)}.")
        if key in sections:
            raise invalid("spec.sections", f"Section '{key}' is listed twice.")
        sections.append(key)
    return {"sections": sections, "settings": validate_settings(raw.get("settings"), DEFAULT_SETTINGS)}
