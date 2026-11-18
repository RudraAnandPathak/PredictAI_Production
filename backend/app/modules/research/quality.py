"""Research quality control. Runs on the finished bundle, after everything else, and judges the bundle by re-reading it, not by trusting how it was built.

Each check returns pass / warn / fail / info and a plain sentence. The aim is warnings, not silence: a weak result is still delivered, with the reasons next to it.
  fail  something that must not be relied on (a citation that does not resolve, a conclusion stronger than its evidence, an outbound query that was not approved)
  warn  something the reader should weigh (a source that could not be read, an unresolved conflict, a gap, undated or old pages)
  info  context (sentences that looked like instructions were set aside)
Verdict: any fail -> 'insufficient'; else any warn -> 'caution'; else 'good'."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Sequence

from ..textkit import numbers_in, tokenize
from ..providers.verify import CAUSAL
from . import relevance as rel
from .sources import source_class

SCHEMA = "predictai.research_quality/1"
Verify = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


def _check(cid: str, title: str, status: str, detail: str, items: Sequence[Dict[str, str]] = ()) -> Dict[str, Any]:
    return {"id": cid, "title": title, "status": status, "detail": detail, "items": list(items)[:20]}


def check_bundle(bundle: Mapping[str, Any], verify: Verify) -> Dict[str, Any]:
    sources = {s["id"]: s for s in bundle["sources"]}
    evidence = {e["id"]: e for e in bundle["evidence"]}
    plan = bundle["plan"]
    checks: List[Dict[str, Any]] = []

    # 1. every citation resolves to the thing it names
    bad, ok_n = [], 0
    calc_ok, calc_bad = 0, []
    for e in bundle["evidence"]:
        src = sources.get(e["source"])
        if src is None:
            bad.append({"ref": e["id"], "note": "The passage points to a source that is not in the bundle."})
            continue
        res = verify(e, src)
        is_calc = (e.get("citation") or {}).get("kind") in ("calculation", "model") and bool((e.get("citation") or {}).get("calc"))
        if res.get("verified"):
            ok_n += 1
            calc_ok += int(is_calc)
        else:
            bad.append({"ref": e["id"], "note": res.get("reason") or "The citation could not be checked."})
            if is_calc:
                calc_bad.append({"ref": e["id"], "note": res.get("reason") or "The calculation could not be rebuilt."})
    if not bundle["evidence"]:
        checks.append(_check("citations_exist", "Citations exist", "info", "There is no evidence to cite."))
    elif bad:
        checks.append(_check("citations_exist", "Citations exist", "fail", f"{len(bad)} of {len(bundle['evidence'])} citations could not be confirmed. Do not rely on those passages.", bad))
    else:
        checks.append(_check("citations_exist", "Citations exist", "pass", f"All {ok_n} cited passages were found again at the recorded place and match what was quoted."))
    failed_ids = {b["ref"] for b in bad}

    # 2. every conclusion is backed by passages that exist and that say it
    unsupported: List[Dict[str, str]] = []
    for c in bundle["conclusions"]:
        cited = [evidence[i] for i in c["evidence"] if i in evidence]
        if not cited:
            unsupported.append({"ref": c["id"], "note": "No cited passage exists for this conclusion."})
            continue
        if any(e["id"] in failed_ids for e in cited):
            unsupported.append({"ref": c["id"], "note": "A passage it relies on could not be confirmed."})
            continue
        quotes = [e["quote"] for e in cited]
        if c.get("authored_by", "extracted") == "extracted":
            if c["text"] not in quotes:
                unsupported.append({"ref": c["id"], "note": "The conclusion is not a quotation of its cited passages."})
        else:
            pool = " ".join(quotes)
            stray = sorted(n for n in numbers_in(c["text"]) if n not in numbers_in(pool))
            if stray:
                unsupported.append({"ref": c["id"], "note": f"The wording contains a number that is not in the evidence ({', '.join(stray[:3])})."})
            elif CAUSAL.search(c["text"]) and not CAUSAL.search(pool):
                unsupported.append({"ref": c["id"], "note": "The wording states a cause that no cited passage states."})
    checks.append(_check("claims_supported", "Claims are supported", "fail" if unsupported else "pass" if bundle["conclusions"] else "info",
                         (f"{len(unsupported)} conclusion(s) go beyond their evidence." if unsupported else f"All {len(bundle['conclusions'])} conclusions quote or closely follow passages that were found.") if bundle["conclusions"] else "There are no conclusions.", unsupported))

    # 3. sources that could not be read
    broken = [s for s in bundle["sources"] if s.get("status") != "ok"]
    checks.append(_check("sources_accessible", "Sources could be read", "warn" if broken else "pass",
                         f"{len(broken)} source(s) could not be read, so anything they might say is missing." if broken else "Every source was read successfully.",
                         [{"ref": s["id"], "note": s.get("failure") or "Not readable."} for s in broken]))

    # 4. dated / old pages
    web = [s for s in bundle["sources"] if s["kind"] in ("web", "news") and s.get("status") == "ok"]
    old = [s for s in web if (s.get("freshness") or {}).get("outdated")]
    undated = [s for s in web if (s.get("freshness") or {}).get("label") == "undated"]
    if not web:
        checks.append(_check("freshness", "Information is current", "info", "No web pages were used, so freshness does not apply."))
    elif old or undated:
        checks.append(_check("freshness", "Information is current", "warn", f"{len(old)} page(s) are older than your limit and {len(undated)} give no date.",
                             [{"ref": s["id"], "note": s["freshness"]["note"]} for s in old + undated]))
    else:
        checks.append(_check("freshness", "Information is current", "pass", "Every web page used is dated and within your limit."))

    # 5. conflicts
    open_conflicts = [c for c in bundle["conflicts"] if c.get("status") == "unresolved"]
    checks.append(_check("conflicts", "Disagreements are shown", "warn" if open_conflicts else "pass",
                         f"{len(open_conflicts)} possible disagreement(s) between sources are not resolved. They are listed beside the conclusions." if open_conflicts else "No disagreement between sources was detected (this check can miss subtle ones).",
                         [{"ref": c["id"], "note": c["explanation"]} for c in open_conflicts]))

    # 6. sufficiency
    cited_sources = {e["source"] for e in bundle["evidence"]}
    used = [s for s in bundle["sources"] if s["id"] in cited_sources]
    independent = len({s.get("site") or s["id"] for s in used})
    gaps = bundle["gaps"]
    if not bundle["evidence"]:
        checks.append(_check("sufficiency", "Enough evidence", "fail", "Nothing relevant was found, so no conclusion can be drawn."))
    elif independent < plan.get("min_sources", 1) or gaps:
        bits = []
        if independent < plan.get("min_sources", 1):
            bits.append(f"{independent} independent source(s) were used; this depth of research aims for at least {plan.get('min_sources', 1)}")
        if gaps:
            bits.append(f"{len(gaps)} part(s) of the plan found nothing")
        checks.append(_check("sufficiency", "Enough evidence", "warn", "; ".join(bits) + ".", [{"ref": g["subtopic"], "note": g["note"]} for g in gaps]))
    else:
        checks.append(_check("sufficiency", "Enough evidence", "pass", f"{independent} independent source(s) cover every part of the plan."))

    # 7. strength of each conclusion is not more than the evidence allows
    over: List[Dict[str, str]] = []
    weak: List[Dict[str, str]] = []
    for c in bundle["conclusions"]:
        cited = [evidence[i] for i in c["evidence"] if i in evidence]
        n_sources = len({e["source"] for e in cited})
        origins = {e["origin"] for e in cited}
        if c["confidence"] == "well_supported" and (n_sources < 2 or c["kind"] not in ("stated", "computed")):
            over.append({"ref": c["id"], "note": "Labelled well supported without two independent sources stating it plainly."})
        if "model_prediction" in origins and c["kind"] != "prediction":
            over.append({"ref": c["id"], "note": "A model's estimate is presented as something other than a prediction."})
        if c["kind"] == "prediction" and c["confidence"] != "prediction":
            over.append({"ref": c["id"], "note": "A forecast is labelled as if it were established."})
        if c["confidence"] in ("single_source", "uncertain_wording", "contested", "prediction"):
            weak.append({"ref": c["id"], "note": "; ".join(c["caveats"]) or c["confidence"]})
    if over:
        checks.append(_check("conclusion_strength", "Conclusions are no stronger than the evidence", "fail", f"{len(over)} conclusion(s) are labelled more strongly than their evidence allows.", over))
    elif weak:
        checks.append(_check("conclusion_strength", "Conclusions are no stronger than the evidence", "warn", f"{len(weak)} conclusion(s) rest on one source, hedged wording, a prediction or a disputed claim; each is labelled that way.", weak))
    elif bundle["conclusions"]:
        checks.append(_check("conclusion_strength", "Conclusions are no stronger than the evidence", "pass", "Every conclusion is corroborated by more than one source."))
    else:
        checks.append(_check("conclusion_strength", "Conclusions are no stronger than the evidence", "info", "There are no conclusions."))

    # 8. uncertainty is visible
    hidden = [{"ref": c["id"], "note": "Hedged, opinion or forecast wording without a caveat."} for c in bundle["conclusions"] if c["kind"] in ("uncertain", "opinion", "prediction") and not c["caveats"]]
    checks.append(_check("uncertainty", "Uncertainty is stated", "fail" if hidden else "pass", "Some uncertain statements carry no caveat." if hidden else "Hedged, opinion and forecast statements are all labelled.", hidden))

    # 9. fact versus interpretation
    unlabeled = [{"ref": e["id"], "note": "Missing its origin or statement type."} for e in bundle["evidence"] if not e.get("origin") or not e.get("kind")]
    mixed = [{"ref": e["id"], "note": "A model estimate is labelled as a plain statement."} for e in bundle["evidence"] if e.get("origin") == "model_prediction" and e.get("kind") not in ("prediction",)]
    checks.append(_check("fact_vs_interpretation", "Facts are kept apart from interpretation", "fail" if (unlabeled or mixed) else "pass",
                         "Some passages are not labelled as stated, opinion, forecast or calculated." if (unlabeled or mixed) else "Every passage is labelled as stated, uncertain, opinion, forecast or calculated, and keeps its origin (your file, calculated, model estimate or external).", unlabeled + mixed))

    # 10. untrusted content
    removed = sum(s.get("injection_removed", 0) for s in bundle["sources"])
    hid = sum(s.get("hidden_blocks_removed", 0) for s in bundle["sources"])
    checks.append(_check("untrusted_content", "Retrieved text was treated as data", "warn" if (removed or hid) else "pass",
                         (f"{removed} sentence(s) that looked like instructions to an AI and {hid} hidden page block(s) were set aside and not used." if (removed or hid) else "No retrieved text tried to give instructions. All retrieved text is only ever quoted, never obeyed."),
                         [{"ref": r.get("source", ""), "note": "; ".join(r.get("reasons", []))} for r in bundle.get("removed", [])]))

    # 11. what was sent out
    w = bundle.get("web") or {}
    if not bundle.get("used_web"):
        checks.append(_check("web_privacy", "Only approved queries left the platform", "pass", "No web request was made for this research."))
    else:
        approved = set(w.get("approved_queries") or [])
        sent = [q for q in (w.get("queries_sent") or [])]
        extra = [q for q in sent if q not in approved]
        if not w.get("approved") or extra:
            checks.append(_check("web_privacy", "Only approved queries left the platform", "fail", "A web query was sent that the person had not approved." if extra else "The plan was not approved before web access.", [{"ref": "query", "note": q} for q in extra]))
        else:
            checks.append(_check("web_privacy", "Only approved queries left the platform", "pass", f"{len(sent)} search request(s) were sent, all from the plan you approved. No document text was sent."))

    # 12. relevance: every passage is about the subject of the question (identity first, then topic), checked again on the finished bundle
    general = [e for e in bundle["evidence"] if e.get("origin") in ("user_provided", "external_research")]
    if plan.get("understanding") and general:
        frame = rel.frame_from_plan(plan)
        vocab = rel.frame_vocab(plan, frame)
        other, off = [], []
        for e in general:
            ident = (sources.get(e["source"]) or {}).get("identity") or {}
            if rel.names_other_entity(frame, e["quote"]):
                other.append({"ref": e["id"], "note": "It is about a different person who shares part of the subject's name."})
            elif not rel.sentence_ok(frame, vocab, e["quote"], source_identity=ident.get("level") or "n/a",
                                     section_words=rel._stems(" ".join((e.get("location") or {}).get("section_path") or [str((e.get("location") or {}).get("section") or "")]))):
                off.append({"ref": e["id"], "note": "It does not name the subject or say enough about the question's topic."})
        bad_sources = [s for s in bundle["sources"] if s["id"] in {e["source"] for e in general} and (s.get("identity") or {}).get("level") in ("sibling", "none")]
        if other or bad_sources:
            checks.append(_check("relevance", "Passages are about the question", "fail", f"{len(other)} passage(s) are about a different entity with a similar name. Do not rely on them.",
                                 other + [{"ref": s["id"], "note": "This source is about a different entity."} for s in bad_sources]))
        else:
            share = 1 - len(off) / len(general)
            checks.append(_check("relevance", "Passages are about the question", "pass" if share >= 0.9 else "warn",
                                 f"{len(general) - len(off)} of {len(general)} passages name the subject or cover the question's topic, and none is about a similarly named entity." + ("" if share >= 0.9 else " The rest may be only loosely related."), off))
    else:
        focus = {t for t in tokenize(" ".join((plan.get("focus") or {}).get("terms") or []))}
        if not focus or not general:
            checks.append(_check("relevance", "Passages are about the question", "info", "There were no topic words or no outside or document passages to compare."))
        else:
            off = [e for e in general if not (focus & set(tokenize(e["quote"] + " " + str((sources.get(e["source"]) or {}).get("title") or ""))))]
            share = 1 - len(off) / len(general)
            checks.append(_check("relevance", "Passages are about the question", "pass" if share >= 0.8 else "warn",
                                 f"{len(general) - len(off)} of {len(general)} passages mention the question's topic words." + ("" if share >= 0.8 else " The rest may be only loosely related."),
                                 [{"ref": e["id"], "note": "Does not mention the question's topic words."} for e in off]))

    # 12b. every provider the plan asked for did what it was asked, or says plainly that it did not
    providers = (bundle.get("collection") or {}).get("providers") or []
    if providers:
        bad = [p for p in providers if p["status"] in ("failed", "partial")]
        empty = [p for p in providers if p["status"] in ("no_results", "no_relevant_results")]
        skipped = [p for p in providers if p["status"] == "skipped"]
        notes = [{"ref": p["provider"], "note": f"{p['status'].replace('_', ' ')}: {p['note']}"} for p in bad + empty + skipped]
        if bad:
            checks.append(_check("providers", "Search providers answered", "warn", "; ".join(f"{p['provider']} {'could not be reached or failed' if p['status'] == 'failed' else 'failed for some searches'}" for p in bad) +
                                 ". Nothing from it is in this result, and no claim is made that it was searched.", notes))
        elif empty or skipped:
            checks.append(_check("providers", "Search providers answered", "info", "; ".join(f"{p['provider']}: {p['status'].replace('_', ' ')}" for p in empty + skipped) + ".", notes))
        else:
            checks.append(_check("providers", "Search providers answered", "pass", "Every provider asked answered: " + ", ".join(p["provider"] for p in providers) + "."))

    # 13. coverage: every part of the plan has evidence
    parts = plan.get("subtopics") or []
    missing = [g for g in bundle["gaps"]]
    if parts:
        checks.append(_check("coverage", "Every part of the question was covered", "warn" if missing else "pass",
                             (f"{len(parts) - len(missing)} of {len(parts)} parts have evidence; {len(missing)} found nothing." if missing else f"All {len(parts)} parts have evidence."),
                             [{"ref": g["subtopic"], "note": g["note"]} for g in missing]))

    # 14. numerical accuracy of calculated statements
    n_calc = sum(1 for e in bundle["evidence"] if (e.get("citation") or {}).get("calc"))
    if n_calc:
        checks.append(_check("numerical_accuracy", "Calculated numbers match their stored results", "fail" if calc_bad else "pass",
                             (f"{len(calc_bad)} of {n_calc} calculated statements could not be rebuilt from their stored results. Do not rely on those numbers." if calc_bad
                              else f"All {calc_ok} calculated statements were rebuilt from the stored results of your own analyses and match exactly. No language model wrote any number."), calc_bad))

    # 15. calculated and external material stay apart
    mixed_conc = []
    ev_by = {e["id"]: e for e in bundle["evidence"]}
    for c in bundle["conclusions"]:
        kinds = {("calc" if (ev_by.get(i) or {}).get("origin") in ("calculated", "model_prediction") else "ext" if (ev_by.get(i) or {}).get("origin") == "external_research" else "doc") for i in c["evidence"]}
        if "calc" in kinds and "ext" in kinds:
            mixed_conc.append({"ref": c["id"], "note": "A conclusion mixes a calculated result with an outside source."})
    rep = bundle.get("report") or {}
    if any(e.get("origin") == "calculated" for e in bundle["evidence"]) and any(e.get("origin") == "external_research" for e in bundle["evidence"]):
        checks.append(_check("calculated_vs_external", "Calculated results and outside sources are kept apart", "fail" if mixed_conc else "pass",
                             "A conclusion combines calculated and outside material." if mixed_conc else f"Each conclusion rests on one kind of evidence. {len(rep.get('comparison') or [])} comparison(s) place calculated findings beside outside passages without merging them.", mixed_conc))

    # 16. what kinds of source were used
    cited_ids = {e["source"] for e in bundle["evidence"]}
    outside = [s for s in bundle["sources"] if s["id"] in cited_ids and s.get("origin") == "external_research"]
    if outside:
        by_cls: Dict[str, int] = {}
        for s in outside:
            by_cls[source_class(s)] = by_cls.get(source_class(s), 0) + 1
        words = {"wikipedia": "Wikipedia article(s) (encyclopedia, a tertiary source)", "arxiv_preprint": "arXiv preprint(s) (not peer reviewed unless stated)", "web": "general web page(s)"}
        detail = "Outside evidence used: " + ", ".join(f"{n} {words.get(k, k)}" for k, n in sorted(by_cls.items())) + "."
        weak_only = set(by_cls) <= {"wikipedia", "arxiv_preprint"} and plan.get("level") in ("academic", "technical", "in_depth")
        partial = [s for s in outside if any(m in str(s.get("content_scope") or "") for m in ("abstract only", "summary only", "not read"))]
        if weak_only or partial:
            bits = []
            if weak_only:
                bits.append("Only an encyclopedia and preprints were used for a question that calls for primary or peer-reviewed work")
            if partial:
                bits.append(f"{len(partial)} source(s) were read only in part")
            checks.append(_check("source_quality", "Source types are stated", "warn", detail + " " + "; ".join(bits) + ".", [{"ref": s["id"], "note": s.get("content_scope") or ""} for s in partial]))
        else:
            checks.append(_check("source_quality", "Source types are stated", "pass", detail))

    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    verdict = "insufficient" if fails or not bundle["evidence"] else "caution" if warns else "good"
    return {"schema": SCHEMA, "verdict": verdict, "checks": checks, "warnings": [f"{c['title']}: {c['detail']}" for c in fails + warns],
            "counts": {"pass": sum(c["status"] == "pass" for c in checks), "warn": len(warns), "fail": len(fails), "info": sum(c["status"] == "info" for c in checks)},
            "note": "These checks re-read the finished result. They catch missing or broken citations and over-strong wording; they cannot tell you whether a source is right."}


def bundle_status(quality: Mapping[str, Any], bundle: Mapping[str, Any]) -> str:
    """complete: nothing missing; partial: delivered but with gaps, unreadable sources or fewer sources than planned; insufficient: not usable as evidence."""
    if quality["verdict"] == "insufficient" or not bundle["evidence"]:
        return "insufficient"
    ids = {"sufficiency", "sources_accessible", "providers"}
    return "partial" if any(c["id"] in ids and c["status"] == "warn" for c in quality["checks"]) else "complete"
