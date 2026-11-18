"""Extraction: turns a stored original into (datasets) a canonical UTF-8 table with column facts, or (documents) page-addressed text
with headings, tables and OCR provenance. The original is only read. Results are stored under opaque keys; the database holds
counts and a summary, never document text."""
from __future__ import annotations

import csv
import io
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ...core.errors import AppError
from ..identity.authorization import Principal
from ..identity.context import Context
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from .safelog import get_logger
from . import keys
from .assets import AssetService
from .constants import AssetKind
from .errors import invalid, not_found
from .parsing import canonical, datasets, documents
from .repository import WorkspaceRepository

logger = get_logger()
PARSER_VERSION = "1"
BLOCK = 1024 * 1024
csv.field_size_limit(16 * 1024 * 1024)


class Extractor:
    """Implements the `Processor` contract used by the upload pipeline."""

    def __init__(self, ctx: Context, repo: WorkspaceRepository, storage: StorageBackend) -> None:
        self.ctx, self.repo, self.storage = ctx, repo, storage

    # -- quota pre-check (cheap, before anything is stored)
    def pages_for_quota(self, path: Path, fmt: str) -> Optional[int]:
        return documents.estimate_pages(path, fmt)

    # -- full extraction
    def process(self, principal: Any, asset: Any, version: Any, path: Path) -> Dict[str, Any]:
        if asset["kind"] == AssetKind.DATASET:
            return self._dataset(principal, asset, version, path)
        return self._document(principal, asset, version, path)

    def _record(self, asset, version, *, status: str, parser: str, summary: Dict[str, Any], warnings: List[Dict[str, str]], text_key: Optional[str], extraction_id: str) -> None:
        self.repo.create_extraction(id=extraction_id, asset_id=asset["id"], version_id=version["id"], project_id=asset["project_id"], owner=asset["owner_user_id"],
                                    status=status, parser=parser, parser_version=PARSER_VERSION, summary=json.dumps(summary, ensure_ascii=False),
                                    warnings=json.dumps(warnings, ensure_ascii=False), text_key=text_key, now=self.ctx.clock.iso())

    def _dataset(self, principal, asset, version, path: Path) -> Dict[str, Any]:
        settings = self.ctx.settings
        table = datasets.read_dataset(path, asset["detected_format"])
        key = keys.table_key(asset["project_id"], asset["id"], version["id"])
        with tempfile.TemporaryFile() as spool:
            summary = canonical.write_canonical(table, spool.write, max_rows=settings.parse_max_rows, max_columns=settings.parse_max_columns)
            spool.seek(0)
            try:
                self.storage.put(key, iter(lambda: spool.read(BLOCK), b""), overwrite=True, max_bytes=max(settings.upload_max_bytes * 6, 64 * 1024 * 1024))
            except StorageError as error:
                raise AppError("TABLE_TOO_LARGE", "The data is too large to store in readable form. Split the file into smaller parts.", 413) from error
        columns = summary["columns"]
        self.repo.update_version_canonical(version["id"], key, summary["rows"], len(columns))
        summary["source"] = table.meta
        summary["facts_vs_estimates"] = {"counts": "exact", "types": "heuristic (90% of non-missing values must fit)"}
        self._record(asset, version, status="ready", parser=f"dataset/{asset['detected_format']}", summary=summary, warnings=table.warnings, text_key=None, extraction_id=new_id())
        return {"row_count": summary["rows"], "column_count": len(columns)}

    def _document(self, principal, asset, version, path: Path) -> Dict[str, Any]:
        s = self.ctx.settings
        data = documents.read_document(path, asset["detected_format"], ocr_enabled=s.ocr_enabled, ocr_languages=s.ocr_languages, ocr_max_pages=s.ocr_max_pages) \
            if asset["detected_format"] == "pdf" else documents.read_document(path, asset["detected_format"])
        extraction_id = new_id()
        key = keys.text_key(asset["project_id"], asset["id"], extraction_id)
        payload = json.dumps({"version_id": version["id"], "source_checksum": version["checksum_sha256"], **data}, ensure_ascii=False).encode("utf-8")
        self.storage.put(key, [payload], overwrite=True)
        pages = data["pages"]
        confidences = [p["ocr_confidence"] for p in pages if p.get("ocr_confidence") is not None]
        summary = {
            "pages": data["page_count"], "page_count_estimated": data["page_count_estimated"],
            "characters": sum(len(p["text"]) for p in pages), "headings": len(data["headings"]), "tables": len(data["tables"]),
            "pages_by_method": {m: sum(1 for p in pages if p["method"] == m) for m in ("text", "ocr", "none")},
            "ocr_mean_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
            "meta": data["meta"], "facts_vs_estimates": {"text": "extracted" if data["format"] != "pdf" else "extracted (OCR pages are recognised, not exact)",
                                                         "page_numbers": "exact" if not data["page_count_estimated"] else "estimated (format has no fixed pages)"},
        }
        self._record(asset, version, status="partial" if data.get("partial") else "ready", parser=f"document/{asset['detected_format']}", summary=summary,
                     warnings=data["warnings"], text_key=key, extraction_id=extraction_id)
        return {"page_count": data["page_count"], "page_count_estimated": 1 if data["page_count_estimated"] else 0}


class DataViews:
    """Read-only views over stored extraction results (always scoped through AssetService.get_owned)."""

    MAX_PREVIEW = 200
    MAX_PAGES = 20

    def __init__(self, ctx: Context, repo: WorkspaceRepository, storage: StorageBackend, assets: AssetService) -> None:
        self.ctx, self.repo, self.storage, self.assets = ctx, repo, storage, assets

    def _version(self, principal: Principal, asset, version_id: Optional[str]):
        vid = version_id or asset["current_version_id"]
        version = self.repo.version(principal.user_id, vid) if isinstance(vid, str) and len(vid) <= 64 else None
        if version is None or version["asset_id"] != asset["id"]:
            raise not_found("version")
        return version

    def _extraction(self, principal: Principal, asset, version):
        row = self.repo.extraction_for_version(principal.user_id, version["id"])
        if row is None:
            raise not_found("extraction")
        return row

    def extraction(self, principal: Principal, asset_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        version = self._version(principal, asset, version_id)
        row = self._extraction(principal, asset, version)
        out = {"id": row["id"], "version_id": version["id"], "status": row["status"], "parser": row["parser"], "parser_version": row["parser_version"],
               "summary": json.loads(row["summary"] or "{}"), "warnings": json.loads(row["warnings"] or "[]"), "created_at": row["created_at"],
               "source_checksum": version["checksum_sha256"], "kind": asset["kind"]}
        if asset["kind"] == AssetKind.DOCUMENT and row["text_key"]:
            doc = self._document_json(row)
            out["pages"] = [{"number": p["number"], "method": p["method"], "ocr_confidence": p.get("ocr_confidence"), "characters": len(p["text"]),
                             "warning": p.get("warning"), "virtual": bool(p.get("virtual"))} for p in doc["pages"]]
        return out

    def _document_json(self, row) -> Dict[str, Any]:
        try:
            return json.loads(self.storage.read_bytes(row["text_key"]).decode("utf-8"))
        except (StorageError, ValueError) as error:
            raise not_found("extraction") from error

    def document_json(self, principal: Principal, asset_id: str, version_id: Optional[str] = None):
        """(asset, version, extraction row, extracted text document) for a document the person owns. Used by retrieval and research; authorisation is AssetService.get_owned."""
        asset = self.assets.get_owned(principal, asset_id)
        if asset["kind"] != AssetKind.DOCUMENT:
            raise invalid("asset", "Only documents have text that can be searched. Datasets have a table preview.")
        version = self._version(principal, asset, version_id)
        row = self._extraction(principal, asset, version)
        if row["status"] not in ("ready", "partial") or not row["text_key"]:
            raise not_found("extraction")
        return asset, version, row, self._document_json(row)

    def preview(self, principal: Principal, asset_id: str, *, offset: int = 0, limit: int = 50, version_id: Optional[str] = None) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        if asset["kind"] != AssetKind.DATASET:
            raise invalid("asset", "Only datasets have a table preview. Documents have a text view.")
        version = self._version(principal, asset, version_id)
        if not version["canonical_key"]:
            raise not_found("preview")
        offset, limit = max(0, int(offset)), max(1, min(int(limit), self.MAX_PREVIEW))
        rows: List[List[str]] = []
        header: List[str] = []
        try:
            with self.storage.open(version["canonical_key"]) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                try:
                    reader = csv.reader(text)
                    header = next(reader, [])
                    for i, row in enumerate(reader):
                        if i < offset:
                            continue
                        if len(rows) >= limit:
                            break
                        rows.append(row)
                finally:
                    text.detach()
        except StorageError as error:
            raise not_found("preview") from error
        ext = self._extraction(principal, asset, version)
        summary = json.loads(ext["summary"] or "{}")
        return {"version_id": version["id"], "columns": summary.get("columns", [{"name": h} for h in header]), "rows": rows,
                "offset": offset, "limit": limit, "total_rows": version["row_count"]}

    def pages(self, principal: Principal, asset_id: str, *, start: int = 1, limit: int = 5, version_id: Optional[str] = None) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        if asset["kind"] != AssetKind.DOCUMENT:
            raise invalid("asset", "Only documents have a text view. Datasets have a table preview.")
        version = self._version(principal, asset, version_id)
        row = self._extraction(principal, asset, version)
        doc = self._document_json(row)
        start, limit = max(1, int(start)), max(1, min(int(limit), self.MAX_PAGES))
        selected = [p for p in doc["pages"] if start <= p["number"] < start + limit]
        return {"version_id": version["id"], "page_count": doc["page_count"], "page_count_estimated": doc["page_count_estimated"], "start": start, "pages": selected}

    def structure(self, principal: Principal, asset_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        if asset["kind"] != AssetKind.DOCUMENT:
            raise invalid("asset", "Only documents have headings and tables.")
        version = self._version(principal, asset, version_id)
        doc = self._document_json(self._extraction(principal, asset, version))
        tables = [{"page": t["page"], "rows": t["rows"][:50], "row_count": len(t["rows"]), "truncated": len(t["rows"]) > 50} for t in doc["tables"][:50]]
        return {"headings": doc["headings"][:500], "tables": tables, "page_count_estimated": doc["page_count_estimated"]}
