"""Context orchestration: collect what the project already holds that bears on a question, from documents, datasets, saved analyses, trained models and earlier research,
and label every item with where it came from, so a calculation, a prediction and a quotation are never mixed up.

Origin classes (the only ones):
  user_provided      the person's own words/files (document passages)
  calculated         numbers PredictAI computed from the person's data (profiles, analyses, insights)
  model_prediction   output of a trained model: an estimate, never a fact about the world
  external_research  text taken from the web or from earlier web-based research
(AI-written wording is a separate label on findings, `ai_phrased`; it is never a source.)

Everything is read through the owning services (AssetService.get_owned etc.), so one person's or project's material can never appear in another's context."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..identity.authorization import Principal
from ..textkit import sentence_spans, stem, tokenize
from . import relevance as rel
from .understanding import mentions, numbers_named

ORIGINS = ("user_provided", "calculated", "model_prediction", "external_research")
MAX_TEXT = 2500                                                                           # a passage is never cut shorter than the chunker's own limit, and never mid-sentence (see _whole)
PRIVATE_ORIGINS = ("user_provided", "calculated", "model_prediction")


def _terms(text: str) -> set:
    return set(tokenize(text))


class ContextOrchestrator:
    def __init__(self, ws: Any, retrieval: Any, bundles_reader: Any = None) -> None:
        self.ws, self.retrieval, self.bundles_reader = ws, retrieval, bundles_reader

    def gather(self, principal: Principal, project_id: str, question: str, plan: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
        project = self.ws.projects.get_owned(principal, project_id)
        qterms = _terms(question) | {t for st in plan["subtopics"] for t in _terms(st["query"])}
        candidates: List[Dict[str, Any]] = []
        manifest: Dict[str, Any] = {"project_id": project["id"], "included": [], "excluded": [], "counts": {o: 0 for o in ORIGINS}}
        self._documents(principal, project["id"], question, plan, options, candidates, manifest)
        datasets = [a for a in self.ws.assets.list(principal, project["id"], kind="dataset", limit=200)["items"] if a.get("status", "ready") == "ready"]
        chosen = set(options.get("datasets") or [])
        handled = set(options.get("calculated_datasets") or [])
        for a in datasets:
            explicit = a["id"] in chosen
            if options.get("datasets") is not None and not explicit:
                manifest["excluded"].append({"kind": "dataset", "name": a["name"], "reason": "not selected for this research"})
                continue
            if a["id"] in handled:
                continue                                                                      # the dataset researcher calculates on it and reports it separately
            self._dataset(principal, a, qterms, explicit, candidates, manifest)
        if options.get("reuse_earlier"):
            self._prior_research(principal, project["id"], qterms, candidates, manifest, options.get("exclude_session"))
        for o in ORIGINS:
            manifest["counts"][o] = sum(1 for c in candidates if c["origin"] == o)
        manifest["private_material"] = any(c["origin"] in PRIVATE_ORIGINS for c in candidates)
        return {"candidates": candidates, "manifest": manifest}

    # ------------------------------------------------------------------ documents
    @staticmethod
    def _whole(text: str, limit: int = MAX_TEXT) -> str:
        """Cut a passage at the end of the last whole sentence that fits, never in the middle of one."""
        if len(text) <= limit:
            return text
        ends = [b for _, b in sentence_spans(text) if b <= limit]
        return text[: ends[-1]] if ends else text[:limit]

    @staticmethod
    def _entity_boost(frame, text: str) -> float:
        """Rank a passage by WHO it is about: passages that name the subject rise, passages that name only another member of the same family (Louis XVIII for Louis XVI) fall."""
        ids = frame.identities if frame is not None else []
        if not ids:
            return 1.0
        if any(mentions(text, e) for e in ids):
            return 1.4
        if any(numbers_named(text, e.family) for e in ids):
            return 0.3
        return 1.0

    def _documents(self, principal, project_id, question, plan, options, out, manifest) -> None:
        selected = options.get("documents")
        queries, seen_q = [], set()
        for q in [question] + [st["query"] for st in plan["subtopics"]]:
            if q and q.lower() not in seen_q:
                seen_q.add(q.lower())
                queries.append(q)
        seen: Dict[tuple, Dict[str, Any]] = {}
        docs_searched: Dict[str, str] = {}
        per_q = max(3, plan["local"]["passages_per_subtopic"])
        frame = rel.frame_from_plan(plan) if plan.get("understanding") else None
        if frame is not None and frame.identities:
            per_q *= 2                                                                    # look wider, then rank by who each passage is about
        for q in queries:
            res = self.retrieval.search(principal, project_id, q, asset_ids=selected, mode="hybrid", k=per_q, consent=options.get("consent"))
            for d in res.get("documents", []):
                docs_searched[d["asset_id"]] = d["name"]
            for s in res.get("skipped", []):
                if not any(e.get("asset_id") == s["asset_id"] for e in manifest["excluded"]):
                    manifest["excluded"].append({"kind": "document", "asset_id": s["asset_id"], "reason": s["reason"]})
            for h in res["hits"]:
                key = (h["asset_id"], h["chunk_id"])
                if key not in seen:
                    seen[key] = {"kind": "document", "origin": "user_provided", "text": self._whole(h["text"]), "score": h["score"] * self._entity_boost(frame, h["text"]), "citation": h["citation"],
                                 "source": {"key": f"document:{h['asset_id']}:{h['version_id']}", "kind": "document", "origin": "user_provided", "title": h["document"], "asset_id": h["asset_id"],
                                            "ref_id": h["version_id"], "page_estimated": h["page_estimated"]}}
        out.extend(sorted(seen.values(), key=lambda c: -c["score"]))
        for aid, name in docs_searched.items():
            n = sum(1 for c in seen.values() if c["source"]["asset_id"] == aid)
            manifest["included"].append({"kind": "document", "asset_id": aid, "name": name, "passages": n})

    # ------------------------------------------------------------------ datasets, analyses, models
    def _dataset(self, principal, asset, qterms, explicit, out, manifest) -> None:
        profile = None
        try:
            got = self.ws.profiles.get(principal, asset["id"])
            profile = got.get("profile")
        except Exception:                                                                    # noqa: BLE001  a dataset without a profile is simply described less
            profile = None
        cols = [c for c in (profile or {}).get("columns", []) if isinstance(c, dict)]
        words = _terms(asset["name"]) | {t for c in cols for t in _terms(str(c.get("name", "")))}
        relevant = explicit or bool(words & qterms)
        models = []
        try:
            models = self.ws.predictive.list_models(principal, asset["id"], limit=5)["items"]
        except Exception:                                                                    # noqa: BLE001
            models = []
        insights = []
        try:
            insights = self.ws.insights.list(principal, asset["id"], limit=8)["items"]
        except Exception:                                                                    # noqa: BLE001
            insights = []
        words |= {t for m in models for t in _terms(f"{m.get('target', '')} {m.get('name', '')}")}
        words |= {t for i in insights for t in _terms(str(i.get("statement", "")))}
        if not (explicit or words & qterms):
            manifest["excluded"].append({"kind": "dataset", "asset_id": asset["id"], "name": asset["name"], "reason": "nothing in it relates to the question"})
            return
        src = {"key": f"dataset:{asset['id']}:{asset.get('current_version_id') or ''}", "kind": "dataset", "origin": "calculated", "title": asset["name"], "asset_id": asset["id"]}
        n = 0
        if profile:
            rows = profile.get("rows") or profile.get("row_count")
            desc = ", ".join(f"{c.get('name')} ({(c.get('type') or {}).get('value') if isinstance(c.get('type'), dict) else c.get('type')})" for c in cols[:25])
            text = f"Dataset {asset['name']} has {rows} rows and {len(cols)} columns: {desc}." if rows is not None else f"Dataset {asset['name']} has {len(cols)} columns: {desc}."
            out.append({"kind": "dataset", "origin": "calculated", "text": text[:MAX_TEXT], "score": 1.0, "citation": {"asset_id": asset["id"], "document": asset["name"], "kind": "dataset_profile"}, "source": src})
            n += 1
        for i in insights[:6]:
            if i.get("statement"):
                out.append({"kind": "analysis", "origin": "calculated", "text": f"{i['statement']} (evidence status: {i.get('evidence_status', 'unknown')})"[:MAX_TEXT], "score": 0.8,
                            "citation": {"asset_id": asset["id"], "document": asset["name"], "kind": "insight", "insight_id": i.get("id")}, "source": {**src, "key": f"analysis:{asset['id']}", "kind": "analysis", "title": f"Findings from {asset['name']}"}})
                n += 1
        for m in models[:3]:
            out.append({"kind": "model", "origin": "model_prediction", "score": 0.7,
                        "text": (f"A {m.get('algorithm_label', m.get('algorithm'))} model named '{m.get('name')}' was trained to predict '{m.get('target')}' from {asset['name']}; on held-out data its "
                                 f"{m.get('primary_label')} was {m.get('test_score')}. This describes an estimate learned from past data, not an observed fact.")[:MAX_TEXT],
                        "citation": {"asset_id": asset["id"], "document": asset["name"], "kind": "model", "model_id": m.get("id")}, "source": {"key": f"model:{m.get('id')}", "kind": "model", "origin": "model_prediction",
                                                                                                                                           "title": f"Model '{m.get('name')}'", "asset_id": asset["id"], "ref_id": m.get("id")}})
            n += 1
        manifest["included"].append({"kind": "dataset", "asset_id": asset["id"], "name": asset["name"], "items": n, "models": len(models), "insights": len(insights)})

    # ------------------------------------------------------------------ earlier research in this project
    def _prior_research(self, principal, project_id, qterms, out, manifest, exclude_session=None) -> None:
        if self.bundles_reader is None:
            return
        for b in self.bundles_reader(principal, project_id, exclude_session):
            terms = _terms(b["question"]) | {t for c in b.get("conclusions", []) for t in _terms(c["text"])}
            if not terms & qterms:
                continue
            used = 0
            for c in b.get("conclusions", [])[:4]:
                origin = "external_research" if b.get("used_web") else "user_provided"
                out.append({"kind": "evidence", "origin": origin, "text": c["text"][:MAX_TEXT], "score": 0.5, "citation": {"bundle_id": b["id"], "conclusion": c["id"], "kind": "earlier_research"},
                            "source": {"key": f"evidence:{b['id']}", "kind": "evidence", "origin": origin, "title": f"Earlier research: {b['question'][:80]}", "ref_id": b["id"],
                                                                                                                                       "inherited_asset_ids": list(b.get("asset_ids", []))}})
                used += 1
            if used:
                manifest["included"].append({"kind": "earlier_research", "bundle_id": b["id"], "name": b["question"][:80], "items": used})
