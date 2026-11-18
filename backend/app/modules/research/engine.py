"""The research run: from an approved plan to a sealed evidence bundle.

    context (your files)  ->  calculations on your datasets  ->  web collection (only approved queries)  ->  evidence  ->  findings  ->  conflicts  ->  conclusions
        ->  reliability  ->  structured report  ->  optional AI wording (checked)  ->  quality control  ->  bundle

Conclusions are always extractive: a conclusion is a sentence that exists in a source, with its location (or a calculated sentence rebuilt from a stored result). A language model is never needed. When one is available and the
person asks for it, it may add a short wording of the answer, which is shown separately, labelled as AI-written, and checked sentence by sentence against the evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..providers import build_messages, check_generated
from ..workspace import keys
from .bundles import ENGINE, assemble, seal, dumps
from .collector import Collector
from .consent import ConsentStore
from .context import PRIVATE_ORIGINS, ContextOrchestrator
from .datasets import DatasetResearcher
from .errors import Cancelled, ResearchFailed
from .evidence import apply_reliability, build_findings, conclude, detect_conflicts, extract_evidence
from .planner import plan_digest
from .quality import bundle_status, check_bundle
from .report import build_report
from .safety import sanitize_query, shares_private_text
from .sources import domain_class, source_label

logger = logging.getLogger("predictai.research")
MAX_TEXT = 100_000
MAX_REUSED = 12                                                                    # earlier sources carried into a follow-up
Progress = Callable[[str, float, str], None]


def approved_queries(web_plan: Dict[str, Any]) -> List[str]:
    """Every search phrase the plan lists, for every provider (the plan hash the person approved covers all of them)."""
    out: List[str] = []
    for q in web_plan.get("queries", []):
        out.append(q["query"])
        for variants in (q.get("by_provider") or {}).values():
            out += [v["query"] for v in variants]
    return list(dict.fromkeys(out))


def snapshot_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ResearchEngine:
    def __init__(self, *, ctx: Any, ws: Any, repo: Any, storage: Any, consent: ConsentStore, orchestrator: ContextOrchestrator, verifier: Any, web_factory: Callable[[str, str], Any],
                 router_factory: Callable[[], Any], new_id: Callable[[], str]) -> None:
        self.ctx, self.ws, self.repo, self.storage, self.consent = ctx, ws, repo, storage, consent
        self.orchestrator, self.verifier, self.web_factory, self.router_factory, self.new_id = orchestrator, verifier, web_factory, router_factory, new_id
        self.datasets = DatasetResearcher(ws)

    # ------------------------------------------------------------------ the run
    def run(self, principal: Any, session: Any, job_id: Optional[str], cancelled: Callable[[], bool], progress: Progress) -> Dict[str, Any]:
        clock = self.ctx.clock
        owner, pid, sid = session["owner_user_id"], session["project_id"], session["id"]
        opts = json.loads(session["options"] or "{}")
        plan = json.loads(session["plan"] or "null")
        if not plan or plan_digest(plan) != session["plan_hash"]:
            raise ResearchFailed("PLAN_INVALID", "The saved plan does not match its fingerprint, so the research was not run. Create the plan again.")

        def check() -> None:
            if cancelled():
                raise Cancelled()

        # ---- what is allowed right now (consent is re-read at run time: withdrawing it stops web access even for an already approved plan)
        eff = self.consent.effective(owner, pid)
        web_plan = plan["web"]
        web = {"requested": bool(web_plan["enabled"]), "used": False, "approved": session["approved_plan_hash"] == session["plan_hash"], "denied_reason": None,
               "approved_queries": approved_queries(web_plan), "queries_sent": [], "providers": []}
        use_web = False
        if web_plan["enabled"]:
            if not web["approved"]:
                web["denied_reason"] = "The plan with web searches was not approved."
            elif not eff["web_allowed"]:
                web["denied_reason"] = (eff["reasons"] or ["Web research is not allowed."])[0]
            else:
                use_web = True
                web["providers"] = [p for p in web_plan["providers"] if p in eff["providers"]]
                if not web["providers"]:
                    use_web, web["denied_reason"] = False, "None of the approved search providers is available now."

        # ---- 1. your own material
        progress("context", 0.05, "Reading your project's documents, data and earlier research")
        dataset_ids = list(plan["local"].get("datasets") or []) if any(st.get("scope") == "dataset" for st in plan["subtopics"]) else []
        gathered = self.orchestrator.gather(principal, pid, plan["question"], plan, {"documents": opts.get("documents") or None, "datasets": opts.get("datasets"),
                                                                                    "reuse_earlier": bool(opts.get("reuse_earlier")), "exclude_session": sid, "calculated_datasets": dataset_ids})
        check()
        candidates: List[Dict[str, Any]] = gathered["candidates"]
        manifest = gathered["manifest"]
        for c in candidates:
            c["source"].setdefault("status", "ok")

        # ---- 1b. calculations on the selected datasets (Phase 4 and Phase 5 services, under the owner's own principal; nothing here leaves the platform)
        calculations: List[Dict[str, Any]] = []
        dataset_notes: List[str] = []
        if dataset_ids:
            progress("calculating", 0.1, "Calculating on your dataset")
            ds = self.datasets.analyse(principal, pid, plan, plan["question"], dataset_ids, progress)
            check()
            candidates += ds["candidates"]
            calculations, dataset_notes = ds["calculations"], ds["notes"]
            for aid, name in {c["asset_id"]: c["dataset"] for c in calculations}.items():
                manifest["included"].append({"kind": "dataset", "asset_id": aid, "name": name, "calculated": sum(1 for c in ds["candidates"] if (c.get("citation") or {}).get("asset_id") == aid),
                                             "runs": sum(1 for c in calculations if c["asset_id"] == aid)})
            for c in ds["candidates"]:
                c["source"].setdefault("status", "ok")
            manifest["calculations"] = [{k: c.get(k) for k in ("asset_id", "dataset", "run_id", "kind", "operations", "rows_used", "result_hash")} for c in calculations]
            for o in manifest["counts"]:
                manifest["counts"][o] = sum(1 for c in candidates if c["origin"] == o)
            manifest["private_material"] = True

        # ---- 2. the web (only the approved queries; never any of your text)
        collection: Dict[str, Any] = {"searches": [], "failures": [], "dropped": [], "providers": []}
        if use_web:
            private_texts = [c["text"] for c in candidates if c["origin"] in PRIVATE_ORIGINS]
            allowed_queries, withheld = self._screen_queries(web_plan, web["providers"], private_texts)
            if withheld:
                collection["failures"] += [{"stage": "query", "message": f"A search was not sent: {w['reason']}.", "query": w["query"], "provider": w["provider"]} for w in withheld]
            if allowed_queries:
                run_plan = {**plan, "web": {**web_plan, "queries": allowed_queries}}
                got = Collector(self.web_factory(owner, pid), clock).run(run_plan, cancelled=cancelled, progress=progress)
                if got.get("cancelled"):
                    raise Cancelled()
                collection = {"searches": got["searches"], "failures": collection["failures"] + got["failures"], "dropped": got.get("dropped", []), "providers": got.get("providers", [])}
                web["provider_status"] = got.get("providers", [])
                web["used"] = True
                web["queries_sent"] = sorted({s["query"] for s in got["searches"]})
                for src in got["sources"]:
                    candidates.append(self._web_candidate(pid, sid, src))
        check()

        # ---- 2b. a follow-up starts from what the earlier question already read (stored snapshots, not new web requests)
        parent_sid = plan["local"].get("reuse_sources_of")
        if parent_sid:
            have = {c["source"]["key"] for c in candidates}
            reused = [c for c in self._reuse_sources(owner, pid, sid, parent_sid) if c["source"]["key"] not in have]
            if reused:
                candidates += reused
                manifest["included"].append({"kind": "earlier_sources", "session_id": parent_sid, "name": (plan.get("follow_up") or {}).get("question") or "the earlier question", "items": len(reused),
                                             "note": "Pages and articles the earlier question already read, reused from their saved copies."})
        check()

        # ---- 3. evidence
        progress("extracting", 0.75, "Choosing the passages that answer each part")
        # A follow-up is one narrow question asked of material already judged relevant, so a passage needs one of its words rather than two (its words are often "results", "report", "paper").
        # General web pages must still mention the question's own topic words, and every passage is still ranked against the whole question.
        res = extract_evidence(candidates, plan, min_match=1 if plan.get("follow_up") else 2)
        sources, evidence, removed = res["sources"], res["evidence"], res["removed"]
        if plan["rounds"] > 1:
            thin = [st for st in plan["subtopics"] if st.get("scope") != "dataset" and st["id"] not in {e["subtopic"] for e in evidence}]
            if thin:
                again = extract_evidence(candidates, {**plan, "subtopics": thin}, min_match=1, exclude=set(res["used"]))
                for i, e in enumerate(again["evidence"], start=len(evidence) + 1):
                    e["id"], e["pass"] = f"E{i}", 2
                    evidence.append(e)
        for e in evidence:
            e.setdefault("pass", 1)
        findings = build_findings(evidence)
        conflicts = detect_conflicts(evidence, findings)
        out = conclude(plan, findings, conflicts, evidence)
        conclusions, gaps = out["conclusions"], out["gaps"]
        apply_reliability(sources, findings, clock.now(), plan["web"].get("freshness_days") or eff.get("freshness_days"))
        for s in sources:
            s["origin_label"] = s["origin"]
            s["source_label"] = source_label(s)
        check()
        report = build_report(plan=plan, sources=sources, evidence=evidence, conclusions=conclusions, conflicts=conflicts, gaps=gaps, calculations=calculations, dataset_notes=dataset_notes,
                              collection=collection, web=web)

        # ---- 4. optional AI wording (checked)
        progress("wording", 0.85, "Checking the conclusions")
        ai = self._ai_summary(principal, session, plan, evidence, sources, eff, gathered["manifest"]["private_material"], opts)
        check()

        # ---- 5. bundle, quality, storage
        if web["requested"] and not web["used"] and web["denied_reason"]:
            collection["failures"].append({"stage": "web", "message": f"Web research was not run: {web['denied_reason']}"})
        now = clock.iso()
        bundle_id = self.new_id()
        number = self.repo.next_bundle_number(sid)
        content = assemble(bundle_id=bundle_id, session=session, number=number, job_id=job_id, plan=plan, manifest=manifest, sources=sources, evidence=evidence, findings=findings,
                           conflicts=conflicts, conclusions=conclusions, gaps=gaps, removed=removed, collection=collection, web=web, now=now, ai=ai,
                           extra={"report": report, "calculations": calculations})
        for s in content["sources"]:                                                      # the page snapshot each web source's citations are checked against
            s["text_key"] = next((c["source"].get("text_key") for c in candidates if c["source"]["key"] == s["key"]), None)
        self._write_snapshots(candidates)                                                 # before the check: citations are verified by reading these saved copies back
        quality = check_bundle(content, lambda e, s: self.verifier.verify(principal, e, s))
        if web["requested"] and not web["used"]:
            quality["warnings"].insert(0, f"Web research was requested but not run: {web['denied_reason']} Only your own material was used.")
            if quality["verdict"] == "good":
                quality["verdict"] = "caution"
        status = bundle_status(quality, content)
        if web["requested"] and not web["used"] and status == "complete":
            status = "partial"
        sealed = seal(content, quality, status)
        progress("saving", 0.95, "Saving the evidence bundle")
        return {"bundle": sealed, "candidates": candidates}

    # ------------------------------------------------------------------ helpers
    def _web_candidate(self, pid: str, sid: str, src: Dict[str, Any], *, reused_from: Optional[str] = None) -> Dict[str, Any]:
        text = (src.get("text") or "")[:MAX_TEXT]
        record = {"key": f"web:{src['url']}", "kind": "web", "origin": "external_research", "title": src.get("title") or src.get("domain") or src["url"], "url": src["url"], "domain": src.get("domain"),
                  "site": src.get("site"), "publisher": src.get("publisher"), "author": src.get("author"), "published": src.get("published"), "retrieved_at": src.get("retrieved_at"),
                  "provider": src.get("provider"), "domain_class": src.get("domain_class"), "source_type": src.get("source_type"), "status": src["status"], "status_code": src.get("status_code"),
                  "failure": src.get("failure"), "cache_hit": src.get("cache_hit", False), "page_hash": src.get("content_hash"), "hidden_blocks_removed": src.get("hidden_blocks_removed", 0), "text_key": None,
                  "tier": src.get("tier"), "via": src.get("via"), "modified": src.get("modified"), "license": src.get("license"), "final_url": src.get("final_url"), "sections": src.get("sections") or [],
                  "sections_used": src.get("sections_used") or [], "content_scope": src.get("content_scope"), "meta": src.get("meta") or {}, "relevance": src.get("relevance"),
                  "total_sections": src.get("total_sections"), "found_by": src.get("found_by") or [], "identity": src.get("identity")}
        if reused_from:
            record["reused_from_session"] = reused_from
        h = None
        if src["status"] in ("ok", "snippet_only") and text:
            h = snapshot_hash(text)
            record["text_key"] = keys.research_snapshot_key(pid, sid, h)
            record["content_hash"] = h
        record = {k: v for k, v in record.items() if v is not None}
        record.setdefault("text_key", None)
        return {"kind": "web", "origin": "external_research", "text": text, "score": 0.0, "citation": {"kind": "web", "url": src["url"], "content_hash": h}, "source": record}

    def _screen_queries(self, web_plan: Dict[str, Any], providers: List[str], private_texts: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Each search phrase is checked again at run time: it must be one the person approved (the plan hash guarantees that), contain nothing private-looking, and repeat nothing from
        their own material. A phrase that fails is not sent; the others still are. Returns (queries to run, what was withheld and why)."""
        allowed: List[Dict[str, Any]] = []
        withheld: List[Dict[str, Any]] = []
        for q in web_plan["queries"]:
            by_provider: Dict[str, List[Dict[str, str]]] = {}
            for provider in q["providers"]:
                if provider not in providers:
                    continue
                variants = (q.get("by_provider") or {}).get(provider) or [{"query": q["query"], "role": "primary"}]
                for v in variants:
                    clean, removed = sanitize_query(v["query"], max_len=400)
                    if removed or clean != v["query"]:
                        withheld.append({"query": v["query"], "provider": provider, "reason": "it contained private-looking text"})
                    elif shares_private_text(clean, private_texts):
                        withheld.append({"query": v["query"], "provider": provider, "reason": "it repeated text from your own material"})
                    else:
                        by_provider.setdefault(provider, []).append(v)
            if by_provider:
                allowed.append({**q, "providers": list(by_provider), "by_provider": by_provider})
        return allowed, withheld

    def _reuse_sources(self, owner: str, pid: str, sid: str, parent_sid: str) -> List[Dict[str, Any]]:
        """Candidates rebuilt from the saved page copies of the parent question's latest sealed bundle. Read through the owner's own rows: another person's research cannot be reached."""
        try:
            rows = [r for r in self.repo.bundles_of_session(owner, parent_sid) if r["project_id"] == pid and r["status"] != "insufficient"]
        except Exception:                                                                    # noqa: BLE001  a missing parent only means nothing is reused
            return []
        if not rows:
            return []
        row = max(rows, key=lambda r: r["number"])
        try:
            data = json.loads(self.storage.read_bytes(row["bundle_key"], 40_000_000).decode("utf-8"))
        except Exception:                                                                    # noqa: BLE001
            return []
        from .bundles import verify_integrity
        if not verify_integrity(data) or data.get("integrity", {}).get("sha256") != row["bundle_sha256"]:
            return []
        out: List[Dict[str, Any]] = []
        for src in data.get("sources", []):
            if src.get("kind") != "web" or src.get("status") not in ("ok", "snippet_only") or not src.get("text_key"):
                continue
            try:
                snap = json.loads(self.storage.read_bytes(src["text_key"], 8_000_000).decode("utf-8"))
            except Exception:                                                                # noqa: BLE001
                continue
            text = snap.get("text") or ""
            if not text or snapshot_hash(text) != src.get("content_hash"):
                continue
            rec = {k: v for k, v in src.items() if k not in ("id", "text_key", "reliability", "freshness", "injection_removed", "origin_label", "source_label")}
            rec["cache_hit"] = True
            out.append(self._web_candidate(pid, sid, {**rec, "text": text}, reused_from=parent_sid))
            if len(out) >= MAX_REUSED:
                break
        return out

    def _write_snapshots(self, candidates: List[Dict[str, Any]]) -> None:
        for c in candidates:
            s = c["source"]
            if s.get("text_key") and c["text"]:
                payload = json.dumps({"url": s["url"], "retrieved_at": s.get("retrieved_at"), "page_hash": s.get("page_hash"), "text": c["text"]}, ensure_ascii=False).encode("utf-8")
                self.storage.put(s["text_key"], [payload], overwrite=True)

    def persist(self, bundle: Dict[str, Any]) -> None:
        """The sealed bundle is stored before its database row: a row never points at a file that is not there. (Page snapshots were written before the quality check.)"""
        self.storage.put(keys.bundle_key(bundle["project_id"], bundle["session_id"], bundle["id"]), [dumps(bundle)], overwrite=True)

    def _ai_summary(self, principal: Any, session: Any, plan: Dict[str, Any], evidence: List[Dict[str, Any]], sources: List[Dict[str, Any]], eff: Dict[str, Any], private: bool, opts: Dict[str, Any]) -> Dict[str, Any]:
        if not opts.get("ai_summary"):
            return {"requested": False, "status": "not_requested", "note": "Conclusions are quotations; no model wrote any of them."}
        if not evidence:
            return {"requested": True, "status": "unavailable", "reason": "There is no evidence to word."}
        router = self.router_factory()
        data_class = "private" if (private or not eff.get("external_ai")) else "public"
        per: Dict[str, List[str]] = {}
        for e in evidence:
            per.setdefault(e["source"], []).append(e["quote"])
        pairs = [(sid_, " ".join(qs)[:1800]) for sid_, qs in per.items()][:10]
        messages = build_messages("Answer the question in 3 to 5 plain sentences using only the sources. End every sentence with the id of the source it uses, like [S1].", pairs, plan["question"])
        outcome = router.chat(session["owner_user_id"], session["project_id"], "synthesize", messages, data_class=data_class, max_tokens=400)
        if not outcome.ok or not outcome.text:
            return {"requested": True, "status": "unavailable", "provider": outcome.provider, "reason": "No language model could be used for this material; the conclusions above are unaffected.", "routing": outcome.summary()}
        checked = check_generated(outcome.text, {sid_: text for sid_, text in pairs})
        kept = checked["kept"]
        return {"requested": True, "status": "available" if kept else "rejected", "label": "ai_phrased", "provider": outcome.provider, "model": outcome.model, "data_class": data_class,
                "text": " ".join(k["text"] + (" " + " ".join(f"[{c}]" for c in k["sources"]) if k["sources"] else "") for k in kept), "kept": kept, "dropped": checked["dropped"],
                "note": "Written by a language model and checked sentence by sentence against the evidence. Sentences that failed the check were removed.", "routing": outcome.summary()}
