"""The Report Evidence Bundle: every statement a report makes points at evidence items kept here. Adapters turn the output of each intelligence module (research bundles, analysis runs, model
versions, exam analyses, scenario runs) into the same item shape, so the report engine, the editor and the exporters never need to know where a fact came from."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ..p7 import jdump

PROVIDER_TYPE = {"wikipedia": "wikipedia", "arxiv": "arxiv", "searxng": "web"}
ITEM_FIELDS = ("ref", "source_id", "source_type", "location", "citation", "anchor", "passage", "calculation_ref", "dataset_version", "model_version", "source_date", "retrieved_at",
               "verification", "uncertainty", "limitation", "bundle_ref")


class EvidenceSet:
    """Collects evidence items for one report and hands out E1, E2... references. `key` de-duplicates the same underlying passage."""

    def __init__(self, start: int = 1) -> None:
        self.items: List[Dict[str, Any]] = []
        self._by_key: Dict[str, str] = {}
        self._n = start - 1

    def add(self, *, key: str, source_type: str, citation: Mapping[str, Any], passage: Optional[str] = None, **extra: Any) -> str:
        if key in self._by_key:
            return self._by_key[key]
        self._n += 1
        ref = f"E{self._n}"
        item = {"ref": ref, "source_id": extra.pop("source_id", None), "source_type": source_type, "location": extra.pop("location", None), "citation": dict(citation), "anchor": extra.pop("anchor", None),
                "passage": (passage or None) and passage[:1200], "calculation_ref": extra.pop("calculation_ref", None), "dataset_version": extra.pop("dataset_version", None),
                "model_version": extra.pop("model_version", None), "source_date": extra.pop("source_date", None), "retrieved_at": extra.pop("retrieved_at", None),
                "verification": extra.pop("verification", "unverified"), "uncertainty": extra.pop("uncertainty", None), "limitation": extra.pop("limitation", None), "bundle_ref": extra.pop("bundle_ref", None)}
        self.items.append(item)
        self._by_key[key] = ref
        return ref


def from_research_bundle(bundle: Mapping[str, Any], es: EvidenceSet) -> Dict[str, str]:
    """Add every passage of a saved research result. Returns {bundle evidence id -> report reference}."""
    bundle_ref = bundle["id"]
    sources = {s["id"]: s for s in bundle.get("sources", [])}
    checks = {c["id"]: c["status"] for c in (bundle.get("quality") or {}).get("checks", [])}
    verified = "verified" if checks.get("citations_exist") == "pass" and checks.get("claims_supported") == "pass" else "checks_incomplete"
    mapping: Dict[str, str] = {}
    for e in bundle.get("evidence", []):
        s = sources.get(e["source"], {})
        origin = s.get("origin")
        stype = "document" if origin == "user_provided" else PROVIDER_TYPE.get(s.get("provider") or "", "web")
        loc = e.get("location") or {}
        meta = s.get("meta") or {}
        citation = {"kind": stype, "title": s.get("title"), "authors": meta.get("authors") or [], "date": s.get("published") or meta.get("published"), "publisher": s.get("publisher"),
                    "site": s.get("site") or s.get("domain"), "url": loc.get("deep_url") or s.get("final_url") or s.get("url"), "doi": meta.get("doi"), "page": loc.get("page"),
                    "retrieved": s.get("retrieved_at")}
        where = " › ".join(x for x in [(f"p. {loc['page']}" if loc.get("page") else None), loc.get("section")] if x) or None
        limitation = None
        if s.get("provider") == "arxiv":
            limitation = "arXiv papers are preprints and may not be peer reviewed."
        elif stype == "wikipedia":
            limitation = "Wikipedia is an encyclopedia written by volunteers: good for background, not a primary source."
        unc = (s.get("reliability") or {}).get("note") if isinstance(s.get("reliability"), dict) else None
        ref = es.add(key=f"{bundle_ref}:{e['id']}", source_type=stype, citation=citation, passage=e.get("quote"), source_id=f"{bundle_ref}:{e['source']}", location=where,
                     anchor=loc.get("anchor") or (f"page {loc['page']}" if loc.get("page") else None), source_date=s.get("published"), retrieved_at=s.get("retrieved_at"),
                     verification=verified, uncertainty=unc, limitation=limitation, bundle_ref=bundle_ref)
        mapping[e["id"]] = ref
    return mapping


def calculation(es: EvidenceSet, *, key: str, title: str, passage: str, calculation_ref: str, dataset_version: Optional[str] = None, model_version: Optional[str] = None,
                when: Optional[str] = None, source_type: str = "dataset_calculation", limitation: Optional[str] = None, uncertainty: Optional[str] = None, source_id: Optional[str] = None) -> str:
    return es.add(key=key, source_type=source_type, citation={"kind": "calculation", "title": title, "authors": [], "date": when, "site": "PredictAI", "publisher": "PredictAI"}, passage=passage,
                  calculation_ref=calculation_ref, dataset_version=dataset_version, model_version=model_version, source_date=when, retrieved_at=when, verification="recomputable",
                  limitation=limitation, uncertainty=uncertainty, source_id=source_id)
