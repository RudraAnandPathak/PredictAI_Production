"""Universal Explanation for world intelligence: events, coverage trends and influence analyses. Fixed rules, no model; every number in the prose comes from the result and is checked by the shared mechanism."""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from ..explanation.core import Draft, Say, explainer

TERMS = {
    "event": "A group of articles that appear to describe the same thing. Grouping is done by comparing headlines and opening sentences, so it can be wrong.",
    "confirmed": "Three or more outlets report matching facts in plain wording and none disagrees. It is agreement between outlets, not verification by PredictAI.",
    "reported": "Two or more outlets carry the story without that level of matching, plain-worded agreement.",
    "disputed": "Outlets give different numbers or opposite statements about what looks like the same thing.",
    "unverified": "Only one outlet carries it, or its wording is hedged, opinion or a forecast.",
    "tone": "How an article is worded (alarming, upbeat). It is not a measure of truth, importance or fairness.",
    "lag": "How many days coverage came before (positive) or after (negative) the change in your data.",
    "permutation test": "The data is shifted around in time many times to see how often a pattern this strong appears by chance, while keeping each series' day-to-day persistence.",
}


def _terms(*names: str) -> List[dict]:
    return [{"term": n, "meaning": TERMS[n]} for n in names]


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return one if n == 1 else (many or one + "s")


STATUS = {"confirmed": "Several outlets report matching facts, and no disagreement was found.", "reported": "More than one outlet carries this story.", "disputed": "Outlets give different accounts of this story.",
          "unverified": "This story rests on a single outlet or on hedged wording."}
KINDS = {"stated": "in plain wording", "uncertain": "with hedged wording", "opinion": "as opinion or analysis", "prediction": "as a forecast"}


@explainer("world:event")
def _event(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    e = r["event"]
    n, s = e["article_count"], e["source_count"]
    summary = f"{STATUS[e['status']]} It is covered by {say.items(s)} {_plural(s, 'outlet')} in {say.items(n)} {_plural(n, 'article')}."
    pts: List[str] = [f"What this is: a group of {say.items(n)} {_plural(n, 'article')} that seem to describe the same thing, first seen {say.lit(e['first_seen'] or 'at an unknown time')} and last seen {say.lit(e['last_seen'] or 'at an unknown time')}. The headline shown is quoted from \"{say.lit(e['label_from']['publisher'])}\"."]
    kinds = [(k, v) for k, v in (e.get("statement_kinds") or {}).items() if v and k in KINDS]
    if kinds:
        pts.append("How the outlets worded it: " + "; ".join(f"{say.items(v)} {_plural(v, 'article')} {KINDS[k]}" for k, v in kinds) + ".")
    dis = e.get("disagreements") or []
    if dis:
        pts.append(f"Where outlets differ: {say.items(len(dis))} {_plural(len(dis), 'pair')} of outlets give {say.lit(dis[0]['reason'])} about what looks like the same thing. Open the comparison to see each side by side; neither is judged right.")
    else:
        pts.append("Where outlets differ: no disagreement in numbers or direction was found, though subtle differences can be missed.")
    pts.append(f"Status: {say.lit(e['basis'])}")
    pts.append("How it was found: a program compared headlines and opening sentences and grouped similar ones that were published close together. No person or AI model decided the grouping, so it can split one story or join two similar ones.")
    pts.append("Be careful: agreement between outlets is not proof (many repeat one report), and tone describes wording, not truth. Only the feeds you added are counted.")
    cautions = []
    if e["status"] == "unverified":
        cautions.append("Treat this as one outlet's claim until others report it.")
    if e["status"] == "disputed":
        cautions.append("The accounts disagree; do not rely on any single number from this story.")
    if e.get("undated"):
        cautions.append(f"{say.items(e['undated'])} {_plural(e['undated'], 'article has', 'articles have')} no publication date, so the timeline is incomplete.")
    return Draft(summary, pts, _terms("event", e["status"], "tone"), cautions)


@explainer("world:trends")
def _trends(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    rec, prev = r["recent_articles"], r["previous_articles"]
    rising = [t["name"] for t in r["topics"] + r["entities"] if t["label"] == "rising"]
    falling = [t["name"] for t in r["topics"] + r["entities"] if t["label"] == "falling"]
    summary = f"In the last {say.items(r['window_days'])} {_plural(r['window_days'], 'day')} there were {say.items(rec)} {_plural(rec, 'article')} compared with {say.items(prev)} in the {say.items(r['window_days'])} before."
    pts = ["What this shows: how often each topic and name appeared in the articles you collected, this period against the one before."]
    pts.append("Rising: " + (", ".join(say.lit(x) for x in rising[:6]) + "." if rising else "nothing changed by more than chance would explain.") + " Falling: " + (", ".join(say.lit(x) for x in falling[:6]) + "." if falling else "nothing changed by more than chance would explain."))
    pts.append("How it was found: counting articles per topic and name, and calling a change rising or falling only when it is large compared with chance and there are enough articles.")
    pts.append("Be careful: more coverage does not mean more happened. These counts reflect only the feeds you added, and topics and names are found by simple rules.")
    cautions = [c for c in r.get("caveats", [])[:2]]
    if r.get("undated_articles"):
        cautions.append(f"{say.items(r['undated_articles'])} {_plural(r['undated_articles'], 'article has', 'articles have')} no date and could not be counted in either period.")
    return Draft(summary, pts, _terms("tone"), cautions)


@explainer("world:influence")
def _influence(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    ev, best = r["evidence"], r.get("best")
    if r.get("status") == "insufficient_data":
        return Draft(f"There was not enough overlapping data to test anything: only {say.items(r['overlap_days'])} {_plural(r['overlap_days'], 'day has', 'days have')} both coverage and a value.", ["Nothing was concluded."], [], [say.quote(w) for w in r.get("warnings", [])[:2]])
    head = {"none": "No association between coverage and your data survived the checks.", "statistical": "Coverage and your data move together, with no claim about which led.", "temporal": "Coverage tended to come before the change in your data."}[ev]
    pts = ["What this is: a test of whether daily coverage and a daily value from your dataset rise and fall together, at several time offsets."]
    if best and ev != "none":
        pts.append(f"What was found: the strongest pattern is at a lag of {say.num(best['lag'], 0, 'lag')} days, with a rank correlation of {say.num(best['rho'], 2, 'rho')} over {say.items(best['n'])} days. The chance of a pattern this strong across all lags tested, if there were no real link, is {say.num(best['p_adjusted'], 3, 'p')}.")
    else:
        pts.append(f"What was found: across {say.items(len(r['raw']['tested']))} time offsets, none was strong enough to rule out chance.")
    pts.append(f"How it was found: ranks were compared at each offset, and chance was judged by shifting the data in time {say.items(r['raw']['permutations'])} times (keeping each series' day-to-day persistence), correcting for having looked at many offsets.")
    pts.append("What this does NOT show: that one caused the other. Something else can move both, coverage depends on which feeds you collected, and a pattern in time is not a mechanism.")
    return Draft(head, pts, _terms("lag", "permutation test"), [say.quote(w) for w in r.get("warnings", [])[:3]])
