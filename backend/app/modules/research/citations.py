"""Citation verification: given a piece of evidence, go back to where it came from and check it still says that.

  document          re-read the stored extraction: the page and character span must exist and be exactly the quoted text
  web / news        re-read the page snapshot saved with the research: its hash must match, and the span must be exactly the quoted text
  calculation       a statement the dataset researcher calculated: the stored result it came from (run, operation, result hash) is read again through the owner's principal and the statement
                    is rebuilt from it by the same fixed template. The rebuilt sentence must equal the quote, so a changed result, a deleted run or an edited quote all fail
  dataset/analysis/ older style: the check is that the object it came from still exists and the quote still matches the recorded hash
  model/evidence
Nothing the caller supplies is trusted: every check reads storage and the database through the owner's own principal."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from ...core.errors import AppError
from ..identity.authorization import Principal
from ..storage import StorageBackend, StorageError
from .datasets import RegenerateError, regenerate


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class CitationVerifier:
    def __init__(self, ws: Any, retrieval: Any, storage: StorageBackend, repo: Any) -> None:
        self.ws, self.retrieval, self.storage, self.repo = ws, retrieval, storage, repo

    def verify(self, principal: Principal, evidence: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        cit = evidence.get("citation") or {}
        quote = evidence.get("quote", "")
        if sha16(quote) != cit.get("quote_sha256"):
            return {"verified": False, "reason": "The quoted text no longer matches the fingerprint recorded with it."}
        kind = source.get("kind")
        if kind == "document":
            res = self.retrieval.verify_citation(principal, cit)
            if not res.get("verified"):
                return {"verified": False, "reason": res.get("reason")}
            return {"verified": True, "reason": None, "ocr": res.get("ocr"), "ocr_confidence": res.get("ocr_confidence")} if res.get("text") == quote else \
                   {"verified": False, "reason": "The document text at the cited position is different from what was quoted."}
        if kind in ("web", "news"):
            key = source.get("text_key")
            if not key:
                return {"verified": False, "reason": "No saved copy of this page exists."}
            try:
                snap = json.loads(self.storage.read_bytes(key, 8_000_000).decode("utf-8"))
            except (StorageError, OSError, ValueError):
                return {"verified": False, "reason": "The saved copy of this page could not be read."}
            text = snap.get("text", "")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != cit.get("content_hash"):
                return {"verified": False, "reason": "The saved copy of the page has changed since it was cited."}
            s, e = cit.get("start"), cit.get("end")
            if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text)) or text[s:e] != quote:
                return {"verified": False, "reason": "The cited position in the saved page does not contain the quoted text."}
            return {"verified": True, "reason": None}
        calc = cit.get("calc")
        if calc and cit.get("kind") in ("calculation", "model"):
            return self._verify_calculation(principal, cit, calc, quote)
        asset_id = source.get("asset_id") or cit.get("asset_id")
        if kind in ("dataset", "analysis", "model") and asset_id:
            try:
                self.ws.assets.get_owned(principal, asset_id)
            except AppError:
                return {"verified": False, "reason": "The dataset this was calculated from is no longer available."}
            return {"verified": True, "reason": None, "note": "Calculated from your data; the check is that the data still exists."}
        if kind == "evidence":
            row = self.repo.bundle(principal.user_id, cit.get("bundle_id") or source.get("ref_id") or "")
            return {"verified": True, "reason": None, "note": "Earlier research that still exists."} if row is not None else {"verified": False, "reason": "The earlier research this came from has been deleted."}
        return {"verified": False, "reason": "This kind of source cannot be checked."}

    def _verify_calculation(self, principal: Principal, cit: Dict[str, Any], calc: Dict[str, Any], quote: str) -> Dict[str, Any]:
        """Rebuild the statement from the stored result and compare. Every read goes through the owner's principal, so a result the person can no longer open cannot verify."""
        def load_run(run_id: str) -> Dict[str, Any]:
            run = self.ws.analytics.get_run(principal, run_id)
            if cit.get("asset_id") and run.get("asset_id") != cit["asset_id"]:
                raise RegenerateError("The stored result belongs to a different dataset than the one cited.")
            return run

        def load_model(model_id: str):
            model = self.ws.predictive.get_model(principal, model_id)
            if cit.get("asset_id") and model.get("asset_id") != cit["asset_id"]:
                raise RegenerateError("The model belongs to a different dataset than the one cited.")
            try:
                report = self.ws.predictive.report(principal, model_id)
            except AppError:
                report = None
            return model, report

        try:
            text = regenerate(calc, load_run, load_model)
        except RegenerateError as e:
            return {"verified": False, "reason": str(e)}
        except AppError:
            return {"verified": False, "reason": "The stored result this was calculated from is no longer available."}
        except (KeyError, TypeError, ValueError):
            return {"verified": False, "reason": "The record of this calculation is incomplete."}
        if text != quote:
            return {"verified": False, "reason": "Recalculating from the stored result gives a different statement than the one quoted."}
        return {"verified": True, "reason": None, "note": "Rebuilt from the stored result of your own calculation; the statement and the numbers in it match."}

    def snapshot(self, key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        try:
            return json.loads(self.storage.read_bytes(key, 8_000_000).decode("utf-8"))
        except (StorageError, OSError, ValueError):
            return None
