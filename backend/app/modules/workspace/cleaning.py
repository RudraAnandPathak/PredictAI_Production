"""Previewable, reversible cleaning of datasets.

Flow: preview (nothing is saved except a short-lived preview) -> explicit confirm -> a NEW derived version (original and earlier
versions are never modified) -> history records who did it and what changed -> rollback moves the 'current' pointer back to any
earlier version without deleting anything. Data values live only in storage; the database and audit log hold counts and step names."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import tempfile
from datetime import timedelta
from typing import Any, Dict, List, Optional

from ...core.clock import from_iso, to_iso
from ...db import transaction
from ..identity.authorization import Principal
from ..identity.context import Context
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from .safelog import get_logger
from . import keys
from .assets import AssetService
from .cleaning_ops import Plan
from .constants import MAX_VERSIONS_PER_ASSET, AssetKind, AssetStatus, VersionKind, WEvent
from .errors import bad_request, conflict, invalid, not_found
from .extraction import PARSER_VERSION
from .parsing import canonical
from .parsing.canonical import ParsedTable
from .projects import ProjectService
from .repository import WorkspaceRepository

logger = get_logger()
SAMPLE = 10
CHANGED_SAMPLE = 12
MAX_SAMPLE_COLUMNS = 40
CELL = 200
BLOCK = 1024 * 1024
csv.field_size_limit(16 * 1024 * 1024)


def _cut(row: List[str], limit: int = MAX_SAMPLE_COLUMNS) -> List[str]:
    return [c[:CELL] for c in row[:limit]]


class CleaningService:
    def __init__(self, ctx: Context, repo: WorkspaceRepository, storage: StorageBackend, assets: AssetService, projects: ProjectService) -> None:
        self.ctx, self.repo, self.storage, self.assets, self.projects = ctx, repo, storage, assets, projects

    # ------------------------------------------------------------------ helpers
    def _dataset(self, principal: Principal, asset_id: str):
        asset = self.assets.get_owned(principal, asset_id, writable=True)
        if asset["kind"] != AssetKind.DATASET:
            raise invalid("asset", "Cleaning applies to datasets. Documents are kept as uploaded.")
        if asset["status"] != AssetStatus.READY:
            raise conflict("ASSET_NOT_READY", "This file could not be read, so it cannot be cleaned.")
        version = self.repo.version(principal.user_id, asset["current_version_id"])
        if version is None or not version["canonical_key"]:
            raise not_found("data")
        return asset, version

    def _rows(self, key: str):
        """Header + a generator over data rows; the stream is closed when the generator finishes."""
        try:
            raw = self.storage.open(key)
        except StorageError as error:
            raise not_found("data") from error
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        reader = csv.reader(text)
        header = next(reader, [])

        def rows():
            try:
                yield from reader
            finally:
                text.detach()
                raw.close()

        return header, rows

    def _header(self, key: str) -> List[str]:
        try:
            with self.storage.open(key) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                try:
                    return next(csv.reader(text), [])
                finally:
                    text.detach()
        except StorageError as error:
            raise not_found("data") from error

    def _who(self, user_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None
        row = self.ctx.conn.execute("SELECT display_name, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return {"user_id": user_id, "name": None}
        return {"user_id": user_id, "name": row["display_name"] or row["email"]}

    # ------------------------------------------------------------------ preview
    def preview(self, principal: Principal, asset_id: str, operations: Any) -> Dict[str, Any]:
        asset, version = self._dataset(principal, asset_id)
        self.ctx.limiter.hit("workspace_write:user", principal.user_id)
        key = version["canonical_key"]
        plan = Plan(operations, self._header(key))
        stats = self._stats(plan, key)
        removed_sample: List[Dict[str, Any]] = []

        def on_removed(number: int, source: List[str], why: str) -> None:
            if len(removed_sample) < SAMPLE:
                removed_sample.append({"row": number, "cells": _cut(source), "reason": why})

        header, rows = self._rows(key)
        kept, effects = plan.run(rows(), stats, on_removed)
        before_rows: List[List[str]] = []
        after_rows: List[List[str]] = []
        changed_sample: List[Dict[str, Any]] = []
        kept_count = 0
        mapping = plan.source_index
        for number, source, result in kept:
            kept_count += 1
            if len(after_rows) < SAMPLE:
                after_rows.append(_cut(result))
            if len(changed_sample) < CHANGED_SAMPLE:
                cols = [j for j, s in enumerate(mapping) if s is not None and (s >= len(source) or result[j] != source[s])]
                if cols:
                    changed_sample.append({"row": number, "before": _cut([source[s] if s is not None and s < len(source) else "" for s in mapping]),
                                           "after": _cut(result), "changed_columns": [c for c in cols if c < MAX_SAMPLE_COLUMNS]})
        total = version["row_count"] or 0  # the stored count of the source version
        _, again = self._rows(key)
        first_rows = again()
        try:
            for i, row in enumerate(first_rows):
                if i >= SAMPLE:
                    break
                before_rows.append(_cut(row))
        finally:
            first_rows.close()
        removed_total = total - kept_count
        cells_changed = sum(e["cells_changed"] for e in effects)
        warnings: List[Dict[str, str]] = []
        can_apply, blocked = True, None
        if kept_count == 0:
            can_apply, blocked = False, "These steps would remove every row. Change the steps so some rows remain."
        elif removed_total == 0 and cells_changed == 0 and not plan.schema_changed:
            can_apply, blocked = False, "These steps would not change anything in this data."
        elif removed_total and total and removed_total / total >= 0.5:
            warnings.append({"code": "MANY_ROWS_REMOVED", "message": f"This removes {removed_total:,} of {total:,} rows ({round(100 * removed_total / total)}%). Check the removed-row examples before applying."})
        for i, e in enumerate(effects):
            if e["unconvertible"]:
                what = "left empty" if plan.ops[i].get("on_invalid") == "empty" else "kept unchanged"
                warnings.append({"code": "UNCONVERTIBLE", "message": f"Step {i + 1}: {e['unconvertible']:,} value(s) could not be converted and will be {what}."})
        steps = []
        for i, spec in enumerate(plan.ops):
            e = effects[i]
            steps.append({"number": i + 1, "op": spec["op"], "explanation": plan.explanations[i], "rows_removed": e["rows_removed"],
                          "cells_changed": e["cells_changed"], "unconvertible": e["unconvertible"], **({"fill_value": spec["fill_value"]} if "fill_value" in spec else {})})
        if plan.blank_effect["rows_removed"]:
            warnings.append({"code": "EMPTY_ROWS_DROPPED", "message": f"{plan.blank_effect['rows_removed']:,} row(s) would end up completely empty and are dropped."})
        summary = {"rows_before": total, "rows_after": kept_count, "rows_removed": removed_total, "columns_before": len(header), "columns_after": len(plan.names),
                   "cells_changed": cells_changed, "steps": steps, "can_apply": can_apply}
        now = self.ctx.clock.now()
        expires = to_iso(now + timedelta(minutes=self.ctx.settings.preview_ttl_minutes))
        tid = new_id()
        doc = {"id": tid, "asset_id": asset["id"], "source_version_id": version["id"], "source_version_number": version["version_number"],
               "operations": plan.public_operations(), "explanation": plan.explanations, "summary": summary, "warnings": warnings, "blocked_reason": blocked,
               "before": {"columns": header[:MAX_SAMPLE_COLUMNS], "rows": before_rows}, "after": {"columns": plan.names[:MAX_SAMPLE_COLUMNS], "rows": after_rows},
               "changed_rows": changed_sample, "removed_rows": removed_sample, "columns_truncated": len(header) > MAX_SAMPLE_COLUMNS or len(plan.names) > MAX_SAMPLE_COLUMNS,
               "expires_at": expires}
        self.storage.put(keys.preview_key(asset["project_id"], asset["id"], tid), [json.dumps(doc, ensure_ascii=False).encode("utf-8")], overwrite=True)
        with transaction(self.ctx.conn):
            self._discard_open_previews(asset, tid_keep=None)
            self.repo.create_transformation(id=tid, asset_id=asset["id"], project_id=asset["project_id"], owner=principal.user_id, kind="clean", status="previewed",
                                            source_version_id=version["id"], operations=json.dumps(plan.public_operations(), ensure_ascii=False),
                                            explanation=json.dumps(plan.explanations, ensure_ascii=False), preview=None,
                                            created_by=principal.user_id, now=to_iso(now), expires_at=expires, summary=json.dumps({k: v for k, v in summary.items()}))
        self.projects.record(asset["project_id"], principal, WEvent.TRANSFORM_PREVIEWED, asset["id"],
                             {"transformation_id": tid, "steps": [s["op"] for s in plan.ops], "rows_removed": removed_total})
        return doc

    def _stats(self, plan: Plan, key: str):
        if not plan.stat_requests:
            return {}
        _, rows = self._rows(key)
        return plan.compute_stats(rows())

    def _discard_open_previews(self, asset, tid_keep: Optional[str]) -> None:
        for row in self.repo.transformations_for_asset(asset["owner_user_id"], asset["id"]):
            if row["status"] == "previewed" and row["id"] != tid_keep:
                self.repo.update_transformation(row["id"], status="discarded")
                self._drop_preview_file(asset, row["id"])

    def _drop_preview_file(self, asset, tid: str) -> None:
        try:
            self.storage.delete(keys.preview_key(asset["project_id"], asset["id"], tid))
        except (StorageError, OSError):
            logger.warning("Could not remove a preview file", exc_info=True)

    def _open_transformation(self, principal: Principal, tid: Any):
        row = self.repo.transformation(principal.user_id, tid) if isinstance(tid, str) and 0 < len(tid) <= 64 else None
        if row is None:
            raise not_found("cleaning preview")
        asset = self.assets.get_owned(principal, row["asset_id"], writable=True)
        return row, asset

    def get_preview(self, principal: Principal, tid: str) -> Dict[str, Any]:
        row, asset = self._open_transformation(principal, tid)
        if row["status"] != "previewed" or (row["expires_at"] and row["expires_at"] <= self.ctx.clock.iso()):
            raise conflict("PREVIEW_EXPIRED", "This preview is no longer available. Run the preview again.")
        try:
            return json.loads(self.storage.read_bytes(keys.preview_key(asset["project_id"], asset["id"], tid)).decode("utf-8"))
        except (StorageError, ValueError) as error:
            raise conflict("PREVIEW_EXPIRED", "This preview is no longer available. Run the preview again.") from error

    # ------------------------------------------------------------------ discard / confirm
    def discard(self, principal: Principal, tid: str) -> Dict[str, Any]:
        row, asset = self._open_transformation(principal, tid)
        if row["status"] != "previewed":
            return {"discarded": False, "status": row["status"]}
        self.repo.update_transformation(tid, status="discarded")
        self._drop_preview_file(asset, tid)
        self.projects.record(asset["project_id"], principal, WEvent.TRANSFORM_DISCARDED, asset["id"], {"transformation_id": tid})
        return {"discarded": True, "status": "discarded"}

    def confirm(self, principal: Principal, tid: str, confirmed: bool = False) -> Dict[str, Any]:
        row, asset = self._open_transformation(principal, tid)
        ctx = self.ctx
        if confirmed is not True:
            raise invalid("confirm", "Confirm that you want to apply these changes.", "required")
        if row["status"] != "previewed":
            raise conflict("PREVIEW_ALREADY_USED", "This preview was already applied or discarded. Run a new preview.")
        if row["expires_at"] and row["expires_at"] <= ctx.clock.iso():
            raise conflict("PREVIEW_EXPIRED", "This preview expired. Run the preview again, then confirm.")
        if asset["status"] != AssetStatus.READY:
            raise conflict("ASSET_NOT_READY", "This file is not ready to be cleaned.")
        if asset["current_version_id"] != row["source_version_id"]:
            raise conflict("DATA_CHANGED", "The data changed after this preview was made (for example by a rollback). Run the preview again.")
        if len(self.repo.versions(asset["id"])) >= MAX_VERSIONS_PER_ASSET:
            raise conflict("VERSION_LIMIT", f"This file already has {MAX_VERSIONS_PER_ASSET} versions. Delete the file and upload a fresh copy, or roll back and continue from there.")
        source = self.repo.version(principal.user_id, row["source_version_id"])
        expected = json.loads(row["summary"] or "{}")
        if not expected.get("can_apply"):
            raise conflict("NOT_APPLICABLE", "This preview cannot be applied. Change the steps and preview again.")
        if not self.repo.claim_transformation(tid):
            raise conflict("PREVIEW_ALREADY_USED", "This preview was already applied or discarded. Run a new preview.")
        version_id = new_id()
        table_key = keys.table_key(asset["project_id"], asset["id"], version_id)
        try:
            plan = Plan(json.loads(row["operations"]), self._header(source["canonical_key"]))
            stats = self._stats(plan, source["canonical_key"])
            table = ParsedTable(header=list(plan.names), rows=lambda: (cells for _n, _s, cells in plan.run(self._rows(source["canonical_key"])[1](), stats)[0]))
            sha = hashlib.sha256()
            with tempfile.TemporaryFile() as spool:
                def sink(data: bytes) -> None:
                    sha.update(data)
                    spool.write(data)

                summary = canonical.write_canonical(table, sink, max_rows=ctx.settings.parse_max_rows, max_columns=ctx.settings.parse_max_columns)
                size = spool.tell()
                spool.seek(0)
                self.storage.put(table_key, iter(lambda: spool.read(BLOCK), b""), overwrite=True, max_bytes=max(ctx.settings.upload_max_bytes * 6, 64 * 1024 * 1024))
            if summary["rows"] != expected.get("rows_after") or len(summary["columns"]) != expected.get("columns_after"):
                raise conflict("DATA_CHANGED", "The result differs from the preview, so nothing was saved. Run the preview again.")
            now = ctx.clock.iso()
            with transaction(ctx.conn):
                number = self.repo.next_version_number(asset["id"])
                self.repo.create_version(id=version_id, asset_id=asset["id"], project_id=asset["project_id"], owner=principal.user_id, number=number, kind=VersionKind.DERIVED,
                                         parent=source["id"], transformation_id=tid, storage_key=table_key, canonical_key=table_key, checksum=sha.hexdigest(), size=size,
                                         fmt="csv", rows=summary["rows"], cols=len(summary["columns"]), created_by=principal.user_id, now=now)
                self.repo.create_extraction(id=new_id(), asset_id=asset["id"], version_id=version_id, project_id=asset["project_id"], owner=principal.user_id, status="ready",
                                            parser="cleaning/derived", parser_version=PARSER_VERSION,
                                            summary=json.dumps({**summary, "source": {"derived_from_version": source["version_number"], "transformation_id": tid},
                                                                "facts_vs_estimates": {"counts": "exact", "types": "heuristic (90% of non-missing values must fit)"}}),
                                            warnings="[]", text_key=None, now=now)
                self.repo.update_asset(asset["id"], current_version_id=version_id, row_count=summary["rows"], column_count=len(summary["columns"]), updated_at=now)
                done = {**expected, "rows_after": summary["rows"], "result_version_number": number, "result_checksum": sha.hexdigest()}
                self.repo.update_transformation(tid, result_version_id=version_id, summary=json.dumps(done), applied_by=principal.user_id, applied_at=now)
            self._drop_preview_file(asset, tid)
        except Exception:
            try:
                self.repo.update_transformation(tid, status="failed")
                self.storage.delete(table_key)
            except Exception:
                logger.warning("Cleanup after a failed cleaning run did not finish", exc_info=True)
            raise
        self.projects.record(asset["project_id"], principal, WEvent.TRANSFORM_APPLIED, asset["id"],
                             {"transformation_id": tid, "version_id": version_id, "version_number": number, "steps": [s["op"] for s in plan.ops], "rows_after": summary["rows"]})
        return {"applied": True, "asset": self.assets.get(principal, asset["id"]), "version_id": version_id, "version_number": number}

    # ------------------------------------------------------------------ rollback
    def rollback(self, principal: Principal, asset_id: str, version_id: Any, confirmed: bool = False) -> Dict[str, Any]:
        asset, current = self._dataset(principal, asset_id)
        if confirmed is not True:
            raise invalid("confirm", "Confirm that you want to switch to this earlier version.", "required")
        target = self.repo.version(principal.user_id, version_id) if isinstance(version_id, str) and 0 < len(version_id) <= 64 else None
        if target is None or target["asset_id"] != asset["id"]:
            raise not_found("version")
        if target["id"] == current["id"]:
            raise bad_request("ALREADY_CURRENT", "That version is already the current one.")
        if not target["canonical_key"] or not self.storage.exists(target["canonical_key"]):
            raise conflict("VERSION_UNAVAILABLE", "That version's data is no longer available.")
        now, tid = self.ctx.clock.iso(), new_id()
        ext = self.repo.extraction_for_version(principal.user_id, target["id"])
        with transaction(self.ctx.conn):
            self.repo.create_transformation(id=tid, asset_id=asset["id"], project_id=asset["project_id"], owner=principal.user_id, kind="rollback", status="applied",
                                            source_version_id=current["id"], result_version_id=target["id"],
                                            operations=json.dumps([{"op": "rollback", "to_version_number": target["version_number"]}]),
                                            explanation=json.dumps([f"Make version {target['version_number']} the current version again. Nothing is deleted."]), preview=None,
                                            created_by=principal.user_id, now=now, expires_at=None,
                                            summary=json.dumps({"from_version_number": current["version_number"], "to_version_number": target["version_number"]}))
            self.repo.update_transformation(tid, applied_by=principal.user_id, applied_at=now)
            self.repo.update_asset(asset["id"], current_version_id=target["id"], row_count=target["row_count"], column_count=target["column_count"], updated_at=now)
            self._discard_open_previews(asset, tid_keep=None)
        self.projects.record(asset["project_id"], principal, WEvent.ASSET_ROLLED_BACK, asset["id"],
                             {"transformation_id": tid, "from_version": current["version_number"], "to_version": target["version_number"]})
        return {"rolled_back": True, "asset": self.assets.get(principal, asset["id"])}

    # ------------------------------------------------------------------ history
    def history(self, principal: Principal, asset_id: str) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        versions = {v["id"]: v for v in self.repo.versions(asset["id"])}
        items = []
        for t in self.repo.transformations_for_asset(principal.user_id, asset["id"]):
            if t["status"] in ("discarded", "expired", "previewed"):
                continue
            src, res = versions.get(t["source_version_id"]), versions.get(t["result_version_id"]) if t["result_version_id"] else None
            items.append({"id": t["id"], "kind": t["kind"], "status": t["status"], "performed_by": self._who(t["applied_by"] or t["created_by"]),
                          "at": t["applied_at"] or t["created_at"], "from_version": src["version_number"] if src else None, "to_version": res["version_number"] if res else None,
                          "operations": json.loads(t["operations"] or "[]"), "explanation": json.loads(t["explanation"] or "[]"), "summary": json.loads(t["summary"] or "{}")})
        items.sort(key=lambda i: i["at"])
        return {"items": items, "current_version_id": asset["current_version_id"],
                "versions": [{"id": v["id"], "number": v["version_number"], "kind": v["kind"], "parent_version_id": v["parent_version_id"], "rows": v["row_count"],
                              "columns": v["column_count"], "checksum_sha256": v["checksum_sha256"], "created_at": v["created_at"], "created_by": self._who(v["created_by"]),
                              "current": v["id"] == asset["current_version_id"]} for v in versions.values()]}

    # ------------------------------------------------------------------ suggestions (heuristic, never applied automatically)
    def suggestions(self, principal: Principal, asset_id: str) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        if asset["kind"] != AssetKind.DATASET or asset["status"] != AssetStatus.READY:
            return {"items": [], "basis": "heuristic"}
        version = self.repo.version(principal.user_id, asset["current_version_id"])
        ext = self.repo.extraction_for_version(principal.user_id, version["id"])
        columns = json.loads(ext["summary"] or "{}").get("columns", []) if ext else []
        profile = None
        prof = self.repo.latest_profile(principal.user_id, version["id"])
        if prof is not None:
            try:
                profile = json.loads(self.storage.read_bytes(prof["result_key"]).decode("utf-8"))
            except (StorageError, ValueError):
                profile = None
        out: List[Dict[str, Any]] = []
        rows = version["row_count"] or 0
        for c in columns:
            name = c["name"]
            if c["missing"] and c["type"] in ("integer", "float"):
                out.append({"id": f"fill-{c['index']}", "title": f"Fill empty values in “{name}”", "reason": f"{c['missing']:,} of {rows:,} cells are empty. The median is a common, outlier-resistant fill for numbers.",
                            "operations": [{"op": "fill_missing", "column": name, "strategy": "median"}]})
            elif c["missing"] and c["type"] != "empty":
                out.append({"id": f"dropmissing-{c['index']}", "title": f"Remove rows with an empty “{name}”", "reason": f"{c['missing']:,} of {rows:,} cells are empty.",
                            "operations": [{"op": "drop_missing", "columns": [name], "how": "any"}]})
            if c["invalid"] and c["type"] in ("integer", "float"):
                out.append({"id": f"convert-{c['index']}", "title": f"Fix values in “{name}” that are not numbers", "reason": f"{c['invalid']:,} value(s) do not look like numbers. They will be left empty if they cannot be converted.",
                            "operations": [{"op": "convert_type", "column": name, "to": c["type"], "on_invalid": "empty"}]})
            if c["name"].startswith("column_") and c["name"][7:].isdigit():
                out.append({"id": f"rename-{c['index']}", "title": f"Give “{name}” a meaningful name", "reason": "This column has no header, so it was named automatically.", "operations": []})
        if profile and profile["kind"] == "dataset":
            if profile["dataset"]["duplicate_rows"]:
                out.append({"id": "dedupe", "title": "Remove duplicate rows", "reason": f"{profile['dataset']['duplicate_rows']:,} row(s) repeat an earlier row exactly.", "operations": [{"op": "drop_duplicates"}]})
            for pc in profile["columns"]:
                codes = {f["code"] for f in pc["flags"]}
                if "EXTRA_SPACES" in codes:
                    out.append({"id": f"trim-{pc['name']}", "title": f"Trim spaces in “{pc['name']}”", "reason": "Some values start or end with spaces, which can make equal values look different.", "operations": [{"op": "trim_whitespace", "columns": [pc["name"]]}]})
                if "CONSTANT" in codes:
                    out.append({"id": f"const-{pc['name']}", "title": f"Remove “{pc['name']}” (all values are the same)", "reason": "A column with a single repeated value adds no information.", "operations": [{"op": "drop_columns", "columns": [pc["name"]]}]})
        return {"items": out[:30], "basis": "heuristic", "needs_profile": profile is None,
                "note": "Suggestions are rules of thumb. Nothing is applied until you preview and confirm."}
