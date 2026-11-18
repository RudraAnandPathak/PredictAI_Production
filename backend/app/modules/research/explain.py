"""Universal Explanation for research outputs: what this is, what was found, which sources, how it was found, what is uncertain, and what to be careful about.
Written from the saved bundle by fixed rules (no model). Every number in the prose is checked against the bundle by the shared mechanism; the technical bundle is unchanged."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ..explanation.core import Draft, Say, explainer

TERMS = {
    "citation": "The exact place a passage came from: the page of your document, or the web address and the saved copy of the page that was read.",
    "corroborated": "More than one independent source states something similar. It makes a statement more likely to be reliable, but does not prove it.",
    "statement type": "Whether a source states something as fact, hedges it, gives an opinion, or makes a forecast. A forecast or a model estimate is never a fact.",
    "reliability signals": "Observable facts about a source (who runs the site, whether it names an author, whether it is dated, whether others agree). They are hints, not a score, and not proof.",
    "possible disagreement": "Two sources that seem to describe the same thing but give different numbers or opposite claims. It may be a real conflict or just a difference in dates, units or definitions.",
}


def _terms(*names: str) -> List[Dict[str, str]]:
    return [{"term": n, "meaning": TERMS[n]} for n in names]


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return one if n == 1 else (many or one + "s")


STATUS = {"complete": "The research found material for every part of the plan.", "partial": "The research found some of what was asked, with gaps listed below.",
          "insufficient": "The research did not find enough dependable material to answer the question."}
KIND = {"stated": "stated as fact by its source", "uncertain": "hedged by its source", "opinion": "an opinion", "prediction": "a forecast or estimate, not a fact", "computed": "calculated from your data"}


@explainer("research:bundle")
def _bundle(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    b = r["bundle"]
    sources, concl, conflicts, gaps = b["sources"], b["conclusions"], b["conflicts"], b["gaps"]
    n_src = len([s for s in sources if s.get("status") == "ok"])
    summary = f"{STATUS[b['status']]} It used {say.items(n_src)} {_plural(n_src, 'readable source')} and quotes {say.items(len(b['evidence']))} {_plural(len(b['evidence']), 'passage')}."
    pts: List[str] = [f"What this is: research on \"{say.lit(b['question'])}\" at {say.lit(b['level_label'].lower())} depth, split into {say.items(len(b['plan']['subtopics']))} {_plural(len(b['plan']['subtopics']), 'part')}."]
    if concl:
        top = concl[:2]
        pts.append("What was found (quoted from the sources, not rewritten): " + " ".join(f"\"{say.quote(c['text'])}\" ({KIND[c['kind']]})" + ("." if i == len(top) - 1 else ";") for i, c in enumerate(top)))
    else:
        pts.append("What was found: nothing relevant enough to draw a conclusion from.")
    own = len([s for s in sources if s["origin"] != "external_research" and s.get("status") == "ok"])
    web = len([s for s in sources if s["origin"] == "external_research" and s.get("status") == "ok"])
    bad = len([s for s in sources if s.get("status") != "ok"])
    line = f"Sources: {say.items(own)} from your project and {say.items(web)} from the web"
    if bad:
        line += f"; {say.items(bad)} could not be read"
    pts.append(line + ". Open any conclusion to see the exact passage and where it came from.")
    how = "How it was found: a program ranked sentences by how well they match each part of the plan and copied the best ones, each with its page or web address. No AI wrote the conclusions."
    if (b.get("ai") or {}).get("status") == "available":
        how += " A language model also wrote a short wording of the answer; it is shown separately and was checked sentence by sentence against the evidence."
    pts.append(how)
    weak = len([c for c in concl if c["confidence"] != "well_supported"])
    unc = []
    if weak:
        unc.append(f"{say.items(weak)} {_plural(weak, 'conclusion rests', 'conclusions rest')} on one source, hedged wording, a forecast or a disputed claim")
    if conflicts:
        unc.append(f"{say.items(len(conflicts))} possible {_plural(len(conflicts), 'disagreement')} between sources {_plural(len(conflicts), 'is', 'are')} not resolved")
    if gaps:
        unc.append(f"{say.items(len(gaps))} {_plural(len(gaps), 'part')} of the plan found nothing")
    pts.append("What is uncertain: " + ("; ".join(unc) + "." if unc else "nothing was flagged, but a source can still be wrong."))
    pts.append("Be careful: PredictAI quotes sources; it has not checked that they are right. Reliability signals are hints, and a model estimate or forecast is never a fact.")
    cautions = [w for w in b["quality"]["warnings"][:3]]
    cautions = [say.quote(c) for c in cautions]
    if not r.get("integrity_ok", True):
        cautions.insert(0, "This saved bundle no longer matches its fingerprint, so it may have been changed.")
    return Draft(summary, pts, _terms("citation", "corroborated", "statement type", "reliability signals", "possible disagreement"), cautions)


@explainer("research:plan")
def _plan(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    plan, web = r["plan"], r["web"]
    n = len(plan["subtopics"])
    q = web["will_send"]
    summary = f"This plan splits the question into {say.items(n)} {_plural(n, 'part')} and will look through your project's documents and data" + (f", then run {say.items(len(q))} web {_plural(len(q), 'search')} if you approve." if q else ".")
    pts = [f"The parts: " + "; ".join(say.lit(st["title"]) for st in plan["subtopics"][:6]) + "."]
    if q:
        pts.append("Only these search phrases, taken from your question, would leave the platform: " + "; ".join(f"\"{say.lit(x['query'])}\"" for x in q[:6]) + ". No text from your documents or data is ever sent.")
        pts.append("Approved." if r.get("approved") else "Nothing is sent until you approve the plan. If you change the question or the options, the plan changes and needs approval again.")
    else:
        pts.append("This plan uses only your own material. Nothing leaves the platform.")
    pts.append("The result will be a set of quoted passages with their sources, conclusions that quote them, any disagreements found, and a list of what could not be found.")
    return Draft(summary, pts, _terms("citation", "statement type"), [])


@explainer("research:comparison")
def _comparison(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    s, c = r["sources"], r["conclusions"]
    summary = (f"Compared with the earlier run, the later run has {say.items(len(s['added']))} new {_plural(len(s['added']), 'source')} and {say.items(len(s['removed']))} {_plural(len(s['removed']), 'source')} no longer used, "
               f"{say.items(len(c['new']))} new {_plural(len(c['new']), 'conclusion')} and {say.items(len(c['no_longer_present']))} {_plural(len(c['no_longer_present']), 'conclusion')} that {_plural(len(c['no_longer_present']), 'is', 'are')} gone.")
    pts = [f"{say.items(len(c['unchanged']))} {_plural(len(c['unchanged']), 'conclusion')} stayed the same."]
    if c["strength_changed"]:
        pts.append(f"{say.items(len(c['strength_changed']))} {_plural(len(c['strength_changed']), 'conclusion')} kept the same words but changed in how well it is supported.")
    if s["content_changed"]:
        pts.append(f"{say.items(len(s['content_changed']))} {_plural(len(s['content_changed']), 'page')} had different content when read again.")
    pts.append("Differences can come from changed pages, a different search or different settings. They do not show which run is right.")
    return Draft(summary, pts, [], [])
