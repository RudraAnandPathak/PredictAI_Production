"""Analytics service: authorises, plans, executes, records evidence and serves results. Framework-free.

Every entry point goes through `_open`, which reuses the workspace's single ownership check (`AssetService.get_owned`) and then
the version check, so a missing, foreign, deleted or other-project resource is an identical 404. Numbers are computed only by the
engine in `ops/`; nothing here (and nothing in the browser or an AI explainer) recalculates them."""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ...core.errors import AppError
from ..identity.authorization import Principal
from ..identity.constants import Perm
from ..identity.context import Context
from ..identity.tokens import new_id
from ..explanation import attach_to_run
from ..storage import StorageBackend, StorageError
from ..workspace import keys
from ..workspace.assets import AssetService
from ..workspace.constants import AssetKind, AssetStatus
from ..workspace.extraction import DataViews
from ..workspace.projects import ProjectService
from ..workspace.repository import WorkspaceRepository
from .constants import (AEvent, CALC_VERSION, Evidence, MAX_DRILL_ROWS, MAX_EXPORT_ROWS, RESULT_SCHEMA, RunKind)
from .drill import select_to_filters
from .csvsafe import FORMULA_CHARS, csv_safe  # noqa: F401  (re-exported: other modules and tests import it from here)
from .errors import AnalysisError, failed, not_found, plan_invalid, too_large, unavailable
from .evidence import assemble, result_hash
from .executor import execute, limits_for, plan_from_dict
from .frame import apply_filters, load_snapshot
from .ops import REGISTRY
from .plan import AnalysisPlan, build_plan, plan_hash
from .repository import AnalyticsRepository

TEST_NOTE_WINDOW_DAYS = 30


def _json(row_value: Optional[str], default: Any) -> Any:
    try:
        return json.loads(row_value) if row_value else default
    except ValueError:
        return default


class AnalyticsService:
    def __init__(self, ctx: Context, wrepo: WorkspaceRepository, repo: AnalyticsRepository, storage: StorageBackend, assets: AssetService, data: DataViews,
                 projects: ProjectService) -> None:
        self.ctx, self.wrepo, self.repo, self.storage, self.assets, self.data, self.projects = ctx, wrepo, repo, storage, assets, data, projects

    # ------------------------------------------------------------------ authorisation and schema
    def _open(self, principal: Principal, asset_id: str, version_id: Optional[str] = None) -> Tuple[Mapping[str, Any], Mapping[str, Any], List[Dict[str, Any]], Dict[str, str]]:
        asset = self.assets.get_owned(principal, asset_id)
        if asset["kind"] != AssetKind.DATASET:
            raise not_found("dataset")
        if asset["status"] != AssetStatus.READY:
            raise AppError("ASSET_NOT_READY", "This file could not be read, so it cannot be analysed.", 409)
        version = self.data._version(principal, asset, version_id)
        extraction = self.wrepo.extraction_for_version(principal.user_id, version["id"])
        if extraction is None or not version["canonical_key"]:
            raise not_found("data")
        declared = list(_json(extraction["summary"], {}).get("columns", []))
        if not declared:
            raise failed("DATASET_EMPTY", "This dataset has no columns to analyse.")
        return asset, version, declared, {c["name"]: c["type"] for c in declared}

    def open_dataset(self, principal: Principal, asset_id: str, version_id: Optional[str] = None):
        """For other modules that need a dataset's current table: (asset, version, declared columns, {name: type}). Same ownership rules as every analysis."""
        return self._open(principal, asset_id, version_id)

    def _lineage(self, principal: Principal, asset_id: str, version: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Which cleaning steps produced this version (ids and operation names only)."""
        out: List[Dict[str, Any]] = []
        by_id = {v["id"]: v for v in self.wrepo.versions(asset_id)}
        cur: Optional[Mapping[str, Any]] = version
        guard = 0
        while cur is not None and guard < 50:
            guard += 1
            entry: Dict[str, Any] = {"version_id": cur["id"], "version_number": cur["version_number"], "kind": cur["kind"], "checksum_sha256": cur["checksum_sha256"]}
            if cur["transformation_id"]:
                t = self.wrepo.transformation(principal.user_id, cur["transformation_id"])
                if t is not None:
                    ops = _json(t["operations"], [])
                    entry["transformation"] = {"id": t["id"], "kind": t["kind"], "operations": [o.get("op") for o in ops if isinstance(o, dict)][:20]}
            out.append(entry)
            cur = by_id.get(cur["parent_version_id"]) if cur["parent_version_id"] else None
        return list(reversed(out))

    def schema(self, principal: Principal, asset_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        asset, version, declared, types = self._open(principal, asset_id, version_id)
        from .plan import role_of
        cols = [{"name": c["name"], "type": c["type"], "role": role_of(c["type"]), "missing": c.get("missing", 0), "invalid": c.get("invalid", 0)} for c in declared]
        return {"asset_id": asset["id"], "name": asset["name"], "version_id": version["id"], "version_number": version["version_number"], "rows": version["row_count"],
                "columns": cols, "operations": [{"name": s.name, "title": s.title, "inferential": s.inferential,
                                                 "params": [{"name": n, "kind": p.kind, "required": p.required, "role": p.role, "choices": list(p.choices), "default": p.default} for n, p in s.params.items()]} for s in REGISTRY.values()],
                "limits": limits_for(principal.can(Perm.COMPUTE_EXTENDED)), "calc_version": CALC_VERSION}

    # ------------------------------------------------------------------ presets (so the browser never needs the operation vocabulary for the common views)
    def preset(self, principal: Principal, asset_id: str, name: str, *, filters: Any = None, version_id: Optional[str] = None) -> Dict[str, Any]:
        sch = self.schema(principal, asset_id, version_id)
        numeric = [c["name"] for c in sch["columns"] if c["role"] == "numeric"]
        if name == "dashboard":
            ops = [{"op": "dataset_summary", "params": {}}, {"op": "data_quality", "params": {}}, {"op": "missing_values", "params": {}}]
            ops.append({"op": "column_stats", "params": {"columns": numeric[:12]}} if numeric else {"op": "frequency", "params": {"column": sch["columns"][0]["name"]}})
            kind = RunKind.DASHBOARD
        elif name == "relationships":
            ops = [{"op": "discover", "params": {"top_k": 25}}]
            if len(numeric) >= 2:
                ops.append({"op": "correlation", "params": {"columns": numeric[:15], "top": 50}})
            kind = RunKind.RELATIONSHIPS
        else:
            raise plan_invalid("preset", "Choose 'dashboard' or 'relationships'.")
        return self.run(principal, asset_id, kind=kind, operations=ops, filters=filters, version_id=version_id)

    # ------------------------------------------------------------------ running analyses
    def plan(self, principal: Principal, asset_id: str, *, kind: str, operations: Any, filters: Any = None, null_policy: Any = None, seed: Any = None,
             sample_rows: Any = None, version_id: Optional[str] = None) -> Tuple[AnalysisPlan, Mapping[str, Any], Mapping[str, Any], List[Dict[str, Any]]]:
        asset, version, declared, types = self._open(principal, asset_id, version_id)
        if kind not in RunKind.ALL:
            raise plan_invalid("kind", "This kind of analysis is not available.")
        plan = build_plan(kind=kind, asset_id=asset["id"], version_id=version["id"], operations=operations, filters=filters, null_policy=null_policy, seed=seed,
                          sample_rows=sample_rows, registry=REGISTRY, types=types)
        return plan, asset, version, declared

    def run(self, principal: Principal, asset_id: str, *, kind: str, operations: Any, filters: Any = None, null_policy: Any = None, seed: Any = None,
            sample_rows: Any = None, version_id: Optional[str] = None, parent_run_id: Optional[str] = None, label: Optional[str] = None,
            event: str = AEvent.RUN, extra_notes: Sequence[str] = ()) -> Dict[str, Any]:
        self.ctx.limiter.hit("analytics:user", principal.user_id)
        plan, asset, version, declared = self.plan(principal, asset_id, kind=kind, operations=operations, filters=filters, null_policy=null_policy, seed=seed,
                                                   sample_rows=sample_rows, version_id=version_id)
        return self._execute_and_store(principal, plan, asset, version, declared, parent_run_id=parent_run_id, label=label, event=event, extra_notes=extra_notes)

    def _execute_and_store(self, principal: Principal, plan: AnalysisPlan, asset: Mapping[str, Any], version: Mapping[str, Any], declared: List[Dict[str, Any]], *,
                           parent_run_id: Optional[str], label: Optional[str], event: str, extra_notes: Sequence[str] = ()) -> Dict[str, Any]:
        limits = limits_for(principal.can(Perm.COMPUTE_EXTENDED))
        digest = plan_hash(plan)
        run_id = new_id()
        notes = list(extra_notes)
        if any(REGISTRY[o.op].inferential for o in plan.operations):
            since = self._days_ago(TEST_NOTE_WINDOW_DAYS)
            earlier = self.repo.count_tests(principal.user_id, version["id"], since)
            if earlier:
                notes.append(f"{earlier} earlier statistical test run(s) on this dataset version in the last {TEST_NOTE_WINDOW_DAYS} days. Running many tests raises the chance of a "
                             "false positive; treat a single small p-value from a search through many tests with caution.")
        rows_total = int(version["row_count"] or 0)
        opener = lambda: self.storage.open(version["canonical_key"])  # noqa: E731
        now = self.ctx.clock.iso()
        try:
            out = execute(plan, opener, declared, rows_total, limits, isolation=self.ctx.settings.analytics_isolation)
        except AnalysisError as error:
            self._record_failure(principal, plan, asset, version, run_id, digest, error, parent_run_id, label, now)
            if error.code.endswith("TOO_LARGE"):
                raise too_large(error.message) from None
            raise failed(error.code, error.message) from None
        except StorageError:
            raise not_found("data") from None
        results = out["results"]
        ev = assemble(plan=plan, plan_hash=digest, run_id=run_id, project_id=asset["project_id"], asset=asset, version=version, lineage=self._lineage(principal, asset["id"], version),
                      results=results, specs=REGISTRY, rows_in_scope=out["rows_in_scope"], rows_in_dataset=out["rows_in_dataset"], sampled=_sample(out), executed_at=now,
                      isolation=out["isolation"], run_warnings=out.get("snapshot_warnings", []) + notes)
        failed_ops = [r for r in results if r["status"] == "failed"]
        status = "failed" if results and len(failed_ops) == len(results) else "completed"   # a run with some failed operations is completed; each operation carries its own status
        digest_result = result_hash(digest, results, out["rows_in_scope"])
        doc = {"schema": RESULT_SCHEMA, "run_id": run_id, "calc_version": CALC_VERSION, "plan": plan.to_dict(), "plan_hash": digest, "result_hash": digest_result,
               "created_at": now, "results": results, "evidence": ev, "notes": notes,
               "execution": {"isolation": out["isolation"], "duration_ms": out["duration_ms"], "stdout": out["stdout"], "library_warnings": out["library_warnings"],
                             "errors": out["errors"], "rows_loaded": out["rows_loaded"], "types": out["types"]}}
        key = keys.analysis_key(asset["project_id"], asset["id"], run_id)
        self.storage.put(key, [json.dumps(doc, ensure_ascii=False, allow_nan=False).encode("utf-8")], overwrite=True)
        summary = self._summary(plan, results, ev)
        self.repo.create_run(id=run_id, project_id=asset["project_id"], asset_id=asset["id"], version_id=version["id"], owner=principal.user_id, kind=plan.kind, status=status,
                             evidence_status=ev["status"], plan=json.dumps(plan.to_dict()), plan_hash=digest, result_hash=digest_result, result_key=key,
                             summary=json.dumps(summary), error_code=None, calc_version=CALC_VERSION, rows_used=out["rows_in_scope"], duration_ms=out["duration_ms"],
                             parent_run_id=parent_run_id, label=(label or None) and str(label)[:120], now=now)
        self._audit(principal, event, asset, run_id, {"kind": plan.kind, "ops": [o.op for o in plan.operations][:40], "rows": out["rows_in_scope"], "evidence": ev["status"], "status": status})
        return self.get_run(principal, run_id)

    def _record_failure(self, principal, plan, asset, version, run_id, digest, error: AnalysisError, parent, label, now) -> None:
        self.repo.create_run(id=run_id, project_id=asset["project_id"], asset_id=asset["id"], version_id=version["id"], owner=principal.user_id, kind=plan.kind, status="failed",
                             evidence_status=Evidence.INCOMPLETE, plan=json.dumps(plan.to_dict()), plan_hash=digest, result_hash=None, result_key=None,
                             summary=json.dumps({"error": {"code": error.code, "message": error.message}}), error_code=error.code, calc_version=CALC_VERSION, rows_used=None,
                             duration_ms=0, parent_run_id=parent, label=(label or None) and str(label)[:120], now=now)
        self._audit(principal, AEvent.REJECTED, asset, run_id, {"code": error.code, "kind": plan.kind}, outcome="failure")

    @staticmethod
    def _summary(plan: AnalysisPlan, results: Sequence[Mapping[str, Any]], ev: Mapping[str, Any]) -> Dict[str, Any]:
        return {"operations": [{"op": r["op"], "status": r["status"], "evidence_status": r.get("evidence_status"), "headline": (r.get("data") or {}).get("headline") if isinstance(r.get("data"), dict) else None}
                               for r in results],
                "missing": ev.get("missing", [])[:20], "columns": ev["inputs"]["selected_columns"][:30]}

    def _days_ago(self, days: int) -> str:
        from datetime import datetime, timedelta
        now = datetime.fromisoformat(self.ctx.clock.iso().replace("Z", "+00:00"))
        return (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")

    def _audit(self, principal: Principal, event: str, asset: Mapping[str, Any], run_id: str, meta: Dict[str, Any], outcome: str = "success") -> None:
        """IDs, counts and operation names only: never a data value, column value or question text."""
        self.ctx.audit.record(event, outcome, actor=principal.user_id, target=run_id, metadata={"asset_id": asset["id"], "project_id": asset["project_id"], **meta})
        try:
            self.projects.record(asset["project_id"], principal, event, run_id, {k: v for k, v in meta.items() if k in ("kind", "rows", "evidence", "status")})
        except Exception:  # the project timeline is best-effort; the audit log above is the record of truth
            pass

    # ------------------------------------------------------------------ reading runs
    def _run_row(self, principal: Principal, run_id: Any):
        row = self.repo.run(principal.user_id, run_id) if isinstance(run_id, str) and 0 < len(run_id) <= 64 else None
        if row is None:
            raise not_found("analysis")
        self.assets.get_owned(principal, row["asset_id"])          # a deleted dataset/project hides its runs too
        return row

    def present(self, row) -> Dict[str, Any]:
        return {"id": row["id"], "kind": row["kind"], "status": row["status"], "evidence_status": row["evidence_status"], "asset_id": row["asset_id"], "version_id": row["version_id"],
                "plan_hash": row["plan_hash"], "result_hash": row["result_hash"], "calc_version": row["calc_version"], "rows_used": row["rows_used"], "duration_ms": row["duration_ms"],
                "parent_run_id": row["parent_run_id"], "label": row["label"], "saved": bool(row["saved"]), "created_at": row["created_at"], "error_code": row["error_code"],
                "summary": _json(row["summary"], {}), "plan": _json(row["plan"], {})}

    def get_run(self, principal: Principal, run_id: str) -> Dict[str, Any]:
        row = self._run_row(principal, run_id)
        out = self.present(row)
        if row["result_key"]:
            try:
                out["result"] = json.loads(self.storage.read_bytes(row["result_key"]).decode("utf-8"))
            except (StorageError, ValueError):
                raise not_found("analysis") from None
            attach_to_run(out["result"])           # "Explanation" beside each result; computed on read from the stored numbers, so runs made before Phase 5 get it too
        return out

    def list_runs(self, principal: Principal, asset_id: str, *, kind: Optional[str] = None, saved_only: bool = False, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        if kind is not None and kind not in RunKind.ALL:
            raise plan_invalid("kind", "This kind of analysis is not available.")
        limit = max(1, min(int(limit), 100))
        rows, total = self.repo.runs_for_asset(principal.user_id, asset["id"], kind=kind, saved_only=saved_only, limit=limit, offset=max(0, int(offset)))
        return {"items": [self.present(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def save_run(self, principal: Principal, run_id: str, *, saved: bool, label: Optional[str] = None) -> Dict[str, Any]:
        row = self._run_row(principal, run_id)
        fields: Dict[str, Any] = {"saved": 1 if saved else 0}
        if label is not None:
            fields["label"] = str(label).strip()[:120] or None
        self.repo.update_run(row["id"], **fields)
        return self.present(self.repo.run(principal.user_id, run_id))

    def rerun(self, principal: Principal, run_id: str) -> Dict[str, Any]:
        """Re-executes the stored plan against the same dataset version and reports whether the numbers are identical (reproducibility)."""
        row = self._run_row(principal, run_id)
        self.ctx.limiter.hit("analytics:user", principal.user_id)
        asset, version, declared, types = self._open(principal, row["asset_id"], row["version_id"])
        if version["checksum_sha256"] is None:
            raise not_found("data")
        plan = plan_from_dict(_json(row["plan"], {}), types)
        fresh = self._execute_and_store(principal, plan, asset, version, declared, parent_run_id=row["id"], label=row["label"], event=AEvent.RERUN)
        same = fresh["result_hash"] is not None and fresh["result_hash"] == row["result_hash"]
        fresh["reproduction"] = {"original_run_id": row["id"], "reproduced": bool(same), "original_result_hash": row["result_hash"], "new_result_hash": fresh["result_hash"],
                                 "calc_version_same": row["calc_version"] == CALC_VERSION,
                                 "explanation": ("Identical inputs, plan, seed and calculation version produced identical results." if same else
                                                 "The results differ. Compare the calculation version and library versions in the evidence; the dataset version itself is unchanged.")}
        return fresh

    # ------------------------------------------------------------------ underlying rows
    def _selected(self, principal: Principal, run_id: str, select: Any) -> Tuple[Mapping[str, Any], Mapping[str, Any], Any, List[str], Any, int]:
        row = self._run_row(principal, run_id)
        asset, version, declared, types = self._open(principal, row["asset_id"], row["version_id"])
        plan = plan_from_dict(_json(row["plan"], {}), types)
        chosen = select_to_filters(select or {"type": "all"}, types)
        limits = limits_for(principal.can(Perm.COMPUTE_EXTENDED))
        with self.storage.open(version["canonical_key"]) as stream:
            snap = load_snapshot(stream, declared, rows_total=int(version["row_count"] or 0), max_rows=int(limits["max_rows"]), max_cells=int(limits["max_cells"]),
                                 sample_rows=plan.sample_rows, seed=plan.seed)
        df = apply_filters(snap.df, snap.types, plan.filters)          # exactly the rows the analysis saw
        df = apply_filters(df, snap.types, chosen["filters"])
        if chosen["rows"] is not None:
            df = df[df.index.isin(chosen["rows"])]
        return asset, version, df, [c["name"] for c in declared], plan, int(len(df))

    def rows(self, principal: Principal, run_id: str, select: Any, *, offset: int = 0, limit: int = 50, columns: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        self.ctx.limiter.hit("analytics:user", principal.user_id)
        asset, version, df, names, plan, total = self._selected(principal, run_id, select)
        limit = max(1, min(int(limit), MAX_DRILL_ROWS))
        offset = max(0, int(offset))
        if columns is not None:
            if not isinstance(columns, (list, tuple)) or any(c not in names for c in columns):
                raise plan_invalid("columns", "Choose columns that exist in this dataset.")
            names = list(columns)
        page = df.iloc[offset:offset + limit]
        items = [{"row": int(idx), "values": [_cell(page.at[idx, c]) for c in names]} for idx in page.index]
        self._audit(principal, AEvent.DRILLED, asset, run_id, {"rows": total, "select": str((select or {}).get("type", "all"))[:20]})
        return {"run_id": run_id, "columns": names, "items": items, "total": total, "offset": offset, "limit": limit, "version_id": version["id"],
                "filters_applied": [f.to_dict() for f in plan.filters], "note": "Row numbers refer to the position in the source table (1 = first data row)."}

    def export(self, principal: Principal, run_id: str, *, what: str, select: Any = None, index: Optional[int] = None) -> Tuple[str, str, bytes]:
        """What leaves the system is audited and rate limited: 'result' (JSON with evidence), 'rows' (CSV of the selected rows) or 'table' (CSV of one chart/result table)."""
        self.ctx.limiter.hit("analytics_export:user", principal.user_id)
        row = self._run_row(principal, run_id)
        if what == "result":
            doc = self.get_run(principal, run_id)
            body = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
            name, ctype = f"analysis-{run_id[:8]}.json", "application/json"
            meta = {"what": "result"}
        elif what == "rows":
            asset, version, df, names, plan, total = self._selected(principal, run_id, select)
            if total > MAX_EXPORT_ROWS:
                raise too_large(f"This selection has {total:,} rows; exports are limited to {MAX_EXPORT_ROWS:,}. Add a filter or select a smaller part.")
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["row"] + [csv_safe(c) for c in names])
            for idx, rec in zip(df.index, df[names].itertuples(index=False, name=None)):
                w.writerow([int(idx)] + [csv_safe(_cell(v)) for v in rec])
            body, name, ctype = buf.getvalue().encode("utf-8"), f"rows-{run_id[:8]}.csv", "text/csv; charset=utf-8"
            meta = {"what": "rows", "rows": total}
        elif what == "table":
            doc = self.get_run(principal, run_id).get("result") or {}
            if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < len(doc.get("results", []))):
                raise plan_invalid("index", "Choose one of the analyses in this run.")
            table = _find_table(doc["results"][index].get("data"))
            if table is None:
                raise failed("NO_TABLE", "This analysis has no table to export.")
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([csv_safe(str(c)) for c in table["columns"]])
            for rec in table["rows"][:MAX_EXPORT_ROWS]:
                w.writerow([csv_safe(_cell(v)) for v in rec])
            body, name, ctype = buf.getvalue().encode("utf-8"), f"table-{run_id[:8]}-{index}.csv", "text/csv; charset=utf-8"
            meta = {"what": "table"}
        else:
            raise plan_invalid("what", "Export 'result', 'rows' or 'table'.")
        asset = self.assets.get_owned(principal, row["asset_id"])
        self._audit(principal, AEvent.EXPORTED, asset, run_id, {**meta, "bytes": len(body)})
        return name, ctype, body


def _sample(out: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    return out.get("sampled") if isinstance(out.get("sampled"), dict) else None


def _cell(value: Any) -> Any:
    try:
        import pandas as pd
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        pass
    return value


def _find_table(data: Any) -> Optional[Dict[str, Any]]:
    """Finds a {columns, rows} table inside a result (chart results expose `data_table`)."""
    if isinstance(data, dict):
        t = data.get("data_table")
        if isinstance(t, dict) and "columns" in t and "rows" in t:
            return t
        for v in data.values():
            found = _find_table(v)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find_table(v)
            if found is not None:
                return found
    return None
