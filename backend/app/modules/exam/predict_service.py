"""Exam prediction: from a subject's past papers and syllabus (and, if the person allows it, exam-focused web articles) to a ranked estimate and a predicted draft paper.

Order of work, and why it is in this order:
  1. The ranking is computed from the person's papers alone, with no AI and no network. It is tested on the person's own papers (see prediction.backtest) and the result is shown with it.
  2. Web articles are optional. They need the person's web consent and a general web-search provider; they add a small capped bonus and are shown with their addresses. They never remove a chapter.
  3. The predicted paper is a normal Exam Intelligence draft built from the ranking. Questions come from the person's own past papers and notes first; when the person allows it, the Brain (Gemini)
     writes new questions for the slots, checked for shape and labelled "written by an AI model". A draft is never published until a person approves it.
Failure is quiet and honest: no web consent, no search provider, no key, a spent daily cap, a page that cannot be read, an answer that is not valid JSON: each is reported in the result and the rest still works."""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit
from typing import Any, Callable, Dict, List, Optional

from ..collab.access import AccessResolver
from ..identity.authorization import Principal
from ..p7 import E, Service7, clean, invalid, jdump, jload, new_id, not_found
from ..webaccess.fetcher import FetchError
from ..webaccess.urlguard import registrable_domain
from . import engine as X
from . import prediction as P
from .service import ExamService

WEB_QUERIES = ("{base} important topics expected questions", "{base} syllabus changes", "{base} examiner report common mistakes")
MAX_SLOTS = 60


class PredictionService(Service7):
    def __init__(self, ctx: Any, ws: Any, exam: ExamService, brain: Any = None, consent: Any = None, web_factory: Optional[Callable[[str, str], Any]] = None) -> None:
        super().__init__(ctx, ws)
        self.exam, self.brain, self.consent, self.web_factory = exam, brain, consent, web_factory

    # ------------------------------------------------------------------ what is possible for this person right now
    def capabilities(self, principal: Principal, subject_id: str) -> Dict[str, Any]:
        s = self.exam._subject(principal, subject_id)
        web = self._web_state(principal, s["project_id"])
        ai: Dict[str, Any] = {"available": self.brain is not None, "ready": False, "note": "No AI provider is set up. Predicted papers are built from your own past papers and notes."}
        if self.brain is not None:
            st = self.brain.status(principal)
            ai = {"available": True, "ready": bool(st["ready"]) and bool(st["for_you"]["external_ai"]), "gemini_ready": st["ready"], "you_allow_external_ai": st["for_you"]["external_ai"],
                  "blocked_by": st["blocked_by"], "usage": st.get("usage"), "note": st["data_notice"]}
            if st["ready"] and not st["for_you"]["external_ai"]:
                ai["note"] = "Gemini is ready, but your “allow external AI” setting is off, so your papers are not sent to it. Questions come from your own material, or from a local model if one is set up."
        return {"ranking": {"available": True, "needs": "at least one past paper and a syllabus"}, "web": web, "ai": ai}

    def _web_state(self, principal: Principal, project_id: str) -> Dict[str, Any]:
        if self.consent is None or self.web_factory is None:
            return {"allowed": False, "provider": None, "reasons": ["Web research is not available in this installation."]}
        eff = self.consent.effective(principal.user_id, project_id)
        reasons = list(eff["reasons"])
        provider = "searxng" if "searxng" in eff["providers"] else None
        if eff["web_allowed"] and provider is None:
            reasons.append("Exam articles need a general web-search provider (SearXNG). The providers you selected only search papers and encyclopedias.")
        return {"allowed": bool(eff["web_allowed"] and provider), "provider": provider, "reasons": reasons}

    # ------------------------------------------------------------------ prediction
    def predict(self, principal: Principal, subject_id: str, params: Any = None) -> Dict[str, Any]:
        self.limit("p7_exam_predict:user", principal)
        s = self.exam._subject(principal, subject_id, writable=True)
        params = params if isinstance(params, dict) else {}
        for k in ("year_from", "year_to"):
            if params.get(k) is not None and (isinstance(params[k], bool) or not isinstance(params[k], int) or not 1900 <= params[k] <= 2100):
                raise invalid(k, "Use a four-digit year.")
        use_web = params.get("use_web", False)
        if not isinstance(use_web, bool):
            raise invalid("use_web", "Use true or false.")
        max_articles = params.get("max_articles", 5)
        if isinstance(max_articles, bool) or not isinstance(max_articles, int) or not 1 <= max_articles <= 8:
            raise invalid("max_articles", "Read between 1 and 8 articles.")
        exam_name = clean(params.get("exam_name"), "exam_name", maximum=120, required=False)
        papers = [{"id": d["id"], "year": d["exam_year"], "title": d["title"]} for d in self.conn.execute("SELECT * FROM exam_documents WHERE subject_id = ? AND kind = 'past_paper' ORDER BY exam_year, title", (subject_id,))
                  if (params.get("year_from") is None or (d["exam_year"] or 0) >= params["year_from"]) and (params.get("year_to") is None or (d["exam_year"] or 9999) <= params["year_to"])]
        if not papers:
            raise invalid("subject", "Add at least one past paper before predicting.")
        curriculum = self.exam._curriculum_rows(subject_id)
        if not curriculum:
            raise invalid("subject", "Add a syllabus or curriculum first: a prediction is made chapter by chapter.")
        ids = {p["id"] for p in papers}
        yr = {p["id"]: p["year"] for p in papers}
        qs: List[Dict[str, Any]] = []
        for q in self.conn.execute("SELECT * FROM exam_questions WHERE subject_id = ? AND owner_user_id = ?", (subject_id, principal.user_id)).fetchall():
            if q["document_id"] not in ids:
                continue
            cur = self.exam._current(q["id"])
            qs.append({"id": q["id"], "document_id": q["document_id"], "year": yr[q["document_id"]], "marks": q["marks"], "chapter": cur["chapter"] if cur else None, "topic": cur["topic"] if cur else None,
                       "qtype": q["qtype"], "text": q["text"]})
        chapters = list(dict.fromkeys(r["chapter"] for r in curriculum))
        analysis = X.analyse(qs, chapters, papers)
        test = P.backtest(qs, chapters, papers)
        rec = P.recurring_questions(qs)
        mix = P.type_mix(qs, papers)
        web = self._web(principal, s, curriculum, use_web, max_articles, exam_name or " ".join(x for x in (s["name"], s["level"]) if x))
        rows = P.combine(analysis, web.get("signals"), web_used=web["used"], recurring=rec, papers=papers)
        limits = [P.NOTICE]
        if len(papers) < 3:
            limits.append(f"Only {len(papers)} paper(s) were used; patterns from so few papers are weak.")
        if analysis["unmapped_questions"]:
            limits.append(f"{analysis['unmapped_questions']} question(s) could not be matched to a chapter and are not counted in any chapter.")
        if web["used"]:
            limits.append("Web articles may describe a different exam, board or year than yours. They add at most "
                          f"{P.WEB_BONUS_MAX:g} points to a chapter and never lower or remove one; read the sources before relying on them.")
        if any(f["code"] == "POSSIBLY_REMOVED" for r in rows for f in r["flags"]):
            limits.append("At least one article mentions a syllabus change. Check your own syllabus for those chapters.")
        sources = web.get("sources", [])
        result = {"version": P.PREDICTION_VERSION, "notice": P.NOTICE, "papers": len(papers), "years": analysis["years"], "questions": analysis["questions"], "chapters": rows,
                  "never_asked": analysis["never_asked"], "backtest": test, "recurring": rec, "type_mix": mix,
                  "web": {k: web[k] for k in ("requested", "used", "reason", "provider", "queries", "failures") if k in web} | {"sources": sources,
                                                                                                                            "signals": [{**i, "url": sources[i["source"]]["url"] if i["source"] < len(sources) else None} for i in web.get("signals", {}).get("items", [])][:60]},
                  "limitations": limits, "weights": {"history": "heuristic score of the past papers (0-100)", "web_bonus_max": P.WEB_BONUS_MAX},
                  "reproducibility": {"version": P.PREDICTION_VERSION, "history_heuristic": X.HEURISTIC_VERSION,
                                      "input_hash": hashlib.sha256(jdump(sorted((q["id"], q["chapter"], q["topic"], q["marks"], q["year"]) for q in qs) + sorted(x.get("content_hash") or "" for x in sources)).encode()).hexdigest()}}
        pid = new_id()
        with self.conn:
            self.conn.execute("INSERT INTO exam_predictions (id, subject_id, project_id, owner_user_id, params, result, created_at) VALUES (?,?,?,?,?,?,?)",
                              (pid, subject_id, s["project_id"], principal.user_id, jdump({k: params.get(k) for k in ("year_from", "year_to", "use_web", "max_articles", "exam_name") if params.get(k) is not None}), jdump(result), self.now()))
        self.audit(E.EXAM_ANALYSED, principal, pid, kind="prediction", papers=len(papers), web_used=web["used"], web_sources=len(sources))
        return self.get(principal, pid)

    def _web(self, principal: Principal, subject: Any, curriculum: List[Dict[str, Any]], use_web: bool, max_articles: int, base: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"requested": use_web, "used": False, "reason": None, "provider": None, "queries": [], "sources": [], "failures": []}
        if not use_web:
            out["reason"] = "Web articles were not requested."
            return out
        state = self._web_state(principal, subject["project_id"])
        if not state["allowed"]:
            out["reason"] = " ".join(state["reasons"]) or "Web research is not allowed."
            return out
        web = self.web_factory(principal.user_id, subject["project_id"])
        provider = state["provider"]
        out["provider"] = provider
        hits: List[Dict[str, Any]] = []
        for tmpl in WEB_QUERIES:
            query = tmpl.format(base=base)[:200]
            out["queries"].append(query)
            self.ctx.limiter.hit("research_web:user", principal.user_id)
            try:
                res = web.search(provider, query, limit=5)
            except FetchError as e:
                out["failures"].append({"stage": "search", "query": query, "code": e.code, "message": e.message})
                if e.code in ("PROVIDER_NOT_CONFIGURED", "PROVIDER_JSON_DISABLED", "PROVIDER_LIMITER", "CONNECT_FAILED"):
                    break
                continue
            hits += res["hits"]
        picked, domains = [], set()
        for h in hits:
            try:
                dom = registrable_domain(urlsplit(h["url"]).hostname or "")
            except Exception:                                                                  # noqa: BLE001
                continue
            if dom in domains:
                continue
            domains.add(dom)
            picked.append(h)
            if len(picked) >= max_articles:
                break
        pages, sources = [], []
        for h in picked:
            self.ctx.limiter.hit("research_web:user", principal.user_id)
            idx = len(sources)
            try:
                page = web.page(h["url"])
            except FetchError as e:
                sources.append({"index": idx, "url": h["url"], "title": h.get("title"), "domain": None, "status": "failed", "error": e.code})
                out["failures"].append({"stage": "read", "url": h["url"], "code": e.code, "message": e.message})
                continue
            sources.append({"index": idx, "url": page["final_url"] if page.get("final_url") else h["url"], "title": page.get("title") or h.get("title"), "domain": page["domain"], "status": "read",
                            "retrieved_at": page["retrieved_at"], "cache_hit": bool(page.get("cache_hit")), "content_hash": page.get("content_hash"), "chars": len(page["text"])})
            pages.append({"idx": idx, "domain": page["domain"], "text": page["text"][:30000]})
        out["sources"] = sources
        for sidx, src in enumerate(sources):
            src["index"] = sidx
        if not pages:
            out["reason"] = "No article could be read, so the ranking uses your past papers only."
            return out
        out["signals"] = P.web_signals(pages, P.chapter_terms(curriculum, exclude=set(X.tokens(" ".join(x for x in (subject["name"], subject["level"], base) if x)))))
        out["used"] = True
        if not out["signals"]["items"]:
            out["reason"] = f"{len(pages)} article(s) were read but none said anything exam-specific about your chapters, so they changed nothing."
        return out

    # ------------------------------------------------------------------ reading predictions
    def get(self, principal: Principal, prediction_id: str) -> Dict[str, Any]:
        r = self.conn.execute("SELECT * FROM exam_predictions WHERE id = ? AND owner_user_id = ?", (prediction_id, principal.user_id)).fetchone() if isinstance(prediction_id, str) and len(prediction_id) <= 64 else None
        if r is None:
            raise not_found("prediction")
        return {"id": r["id"], "subject_id": r["subject_id"], "project_id": r["project_id"], "params": jload(r["params"], {}), "result": jload(r["result"], {}), "created_at": r["created_at"]}

    def list(self, principal: Principal, subject_id: str) -> Dict[str, Any]:
        self.exam._subject(principal, subject_id)
        rows = self.conn.execute("SELECT id, created_at, params, result FROM exam_predictions WHERE subject_id = ? AND owner_user_id = ? ORDER BY created_at DESC", (subject_id, principal.user_id)).fetchall()
        return {"items": [{"id": r["id"], "created_at": r["created_at"], "params": jload(r["params"], {}), "verdict": (jload(r["result"], {}).get("backtest") or {}).get("verdict"),
                           "web_used": bool((jload(r["result"], {}).get("web") or {}).get("used"))} for r in rows]}

    # ------------------------------------------------------------------ the predicted paper
    def build_paper(self, principal: Principal, prediction_id: str, spec: Any = None) -> Dict[str, Any]:
        pred = self.get(principal, prediction_id)
        spec = dict(spec) if isinstance(spec, dict) else {}
        mode = spec.pop("ai", "all")
        if mode not in ("off", "gaps", "all"):
            raise invalid("ai", "Choose off, gaps or all.")
        seed, title = spec.pop("seed", None), spec.pop("title", None)
        mix = pred["result"].get("type_mix") or {}
        if not spec.get("type_counts") and mix.get("type_counts"):
            spec["type_counts"] = mix["type_counts"]
        if not spec.get("marks_per_type") and mix.get("marks_per_type"):
            spec["marks_per_type"] = mix["marks_per_type"]
        counts = spec.get("type_counts") or {"mcq": 10, "short": 5, "long": 2}
        if isinstance(counts, dict) and sum(v for v in counts.values() if isinstance(v, int) and not isinstance(v, bool)) > MAX_SLOTS:
            raise invalid("type_counts", f"A predicted paper may have at most {MAX_SLOTS} questions.")
        for k in ("emphasis", "prediction_id", "analysis_id"):
            spec.pop(k, None)
        bp = self.exam.blueprint(principal, pred["subject_id"], {**spec, "emphasis": "prediction", "prediction_id": prediction_id})
        hook = self._hook(principal, pred, mode)
        paper = self.exam.generate(principal, bp["id"], seed=seed, title=clean(title, "title", maximum=160, required=False) or "Predicted paper (estimate)", rows_hook=hook, prediction_id=prediction_id)
        return {"assessment": paper, "ai": (paper.get("validation") or {}).get("ai"), "prediction_id": prediction_id}

    def _past(self, principal: Principal, subject_id: str) -> List[Dict[str, Any]]:
        out = []
        years = {d["id"]: d["exam_year"] for d in self.conn.execute("SELECT id, exam_year FROM exam_documents WHERE subject_id = ? AND kind = 'past_paper'", (subject_id,))}
        for q in self.conn.execute("SELECT * FROM exam_questions WHERE subject_id = ? AND owner_user_id = ?", (subject_id, principal.user_id)).fetchall():
            if q["document_id"] not in years:
                continue
            cur = self.exam._current(q["id"])
            out.append({"text": q["text"], "qtype": q["qtype"], "chapter": cur["chapter"] if cur else None, "year": years[q["document_id"]]})
        return out

    def _hook(self, principal: Principal, pred: Dict[str, Any], mode: str) -> Callable[..., Any]:
        def hook(plan: Dict[str, Any], spec: Dict[str, Any], by_slot: Dict[int, Dict[str, Any]], unfilled: List[Dict[str, Any]]):
            summary: Dict[str, Any] = {"mode": mode, "used_ai": False, "requests": 0, "slots_asked": 0, "accepted": 0, "rejected": [], "reasons": [], "provider": None, "model": None, "tokens_in": 0, "tokens_out": 0}
            slots = plan["slots"]
            targets = [i for i in range(len(slots)) if mode == "all" or (mode == "gaps" and i not in by_slot)]
            if mode == "off":
                summary["reasons"].append("AI writing was switched off for this paper.")
            elif self.brain is None:
                summary["reasons"].append("No AI provider is available in this installation.")
            elif not targets:
                summary["reasons"].append("Every slot was filled from your own material.")
            else:
                self._ask_batches(principal, pred, plan, by_slot, targets, summary)
            rest = [{"position": i + 1, "chapter": slots[i]["chapter"], "qtype": slots[i]["qtype"], "reason": "No past question, note sentence or AI-written question fits this slot."} for i in range(len(slots)) if i not in by_slot]
            return by_slot, rest, {"ai": summary}
        return hook

    def _ask_batches(self, principal: Principal, pred: Dict[str, Any], plan: Dict[str, Any], by_slot: Dict[int, Dict[str, Any]], targets: List[int], summary: Dict[str, Any]) -> None:
        curriculum = self.exam._curriculum_rows(pred["subject_id"])
        topics_by: Dict[str, List[str]] = {}
        for r in curriculum:
            if r["topic"]:
                topics_by.setdefault(r["chapter"], [])
                if r["topic"] not in topics_by[r["chapter"]]:
                    topics_by[r["chapter"]].append(r["topic"])
        past = self._past(principal, pred["subject_id"])
        web_items = [{"chapter": i["chapter"], "text": i["text"]} for i in (pred["result"].get("web") or {}).get("signals", []) if i.get("kind") == "emphasis"]
        batches = [targets[i:i + P.AI_BATCH] for i in range(0, len(targets), P.AI_BATCH)][:P.AI_MAX_BATCHES]
        if len(targets) > P.AI_BATCH * P.AI_MAX_BATCHES:
            summary["reasons"].append(f"Only the first {P.AI_BATCH * P.AI_MAX_BATCHES} slots were sent to the AI; the rest use your own material.")
        for batch in batches:
            numbered = {i + 1: {"slot": i + 1, **plan["slots"][i]} for i in batch}
            summary["slots_asked"] += len(batch)
            ans = self.brain.ask(principal, pred["project_id"], "synthesize", P.ai_instruction(list(numbered.values())), P.ai_sources(list(numbered.values()), curriculum, past, web_items),
                                 system=P.QUESTION_SYSTEM, data_class="private", max_tokens=8192, temperature=0.4)
            summary["requests"] += sum(1 for t in ans.trace if t.get("outcome") in ("ok", "error", "timeout"))
            if not ans.ok:
                summary["reasons"] += [r for r in ans.reasons if r not in summary["reasons"]]
                break                                                                           # one failed batch means the provider is unavailable; do not spend more requests on it
            summary.update(used_ai=True, provider=ans.provider, model=ans.model)
            summary["tokens_in"] += ans.units_in
            summary["tokens_out"] += ans.units_out
            accepted, rejected = P.parse_ai_questions(ans.text or "", numbered, topics_by, [p["text"] for p in past], f"{ans.provider}:{ans.model}")
            for n, q in accepted.items():
                sl = plan["slots"][n - 1]
                by_slot[n - 1] = {**q, "difficulty": sl["difficulty"], "chapter": sl["chapter"]}
            summary["accepted"] += len(accepted)
            summary["rejected"] += rejected[:20]
        if summary["used_ai"] and not summary["accepted"]:
            summary["reasons"].append("The AI answered, but none of its questions passed the checks, so your own material was used.")
