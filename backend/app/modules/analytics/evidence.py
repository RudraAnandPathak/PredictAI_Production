"""The evidence contract: one structure for every analytical result, covering where the data came from, exactly how the number was
calculated, the inputs needed to reproduce it, how far it can be trusted, and how to get back to the rows. A result that lacks any
required element is marked INCOMPLETE and must never be shown as a verified conclusion."""
from __future__ import annotations

import platform
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .constants import CALC_VERSION, EVIDENCE_SCHEMA, Evidence
from .plan import AnalysisPlan, OpSpec, canonical_json, sha256_json

REQUIRED_OP_KEYS = ("columns", "sample_size", "null_policy", "method", "formula_ref", "uncertainty", "assumptions", "limitations")


def library_versions() -> Dict[str, str]:
    import numpy
    import pandas
    import scipy

    return {"python": platform.python_version(), "numpy": numpy.__version__, "pandas": pandas.__version__, "scipy": scipy.__version__}


def op_missing(op_result: Mapping[str, Any], inferential: bool) -> List[str]:
    """Which required evidence elements a single operation result lacks (empty list = complete)."""
    ev = op_result.get("evidence")
    if not isinstance(ev, dict):
        return ["evidence"]
    missing = [k for k in REQUIRED_OP_KEYS if k not in ev or ev[k] is None or (k in ("method", "formula_ref", "null_policy") and not str(ev[k]).strip())]
    if "sample_size" in ev and not isinstance(ev["sample_size"], int):
        missing.append("sample_size")
    if "columns" in ev and not isinstance(ev["columns"], list):
        missing.append("columns")
    if "uncertainty" in ev and not isinstance(ev["uncertainty"], dict):
        missing.append("uncertainty")
    for k in ("assumptions", "limitations"):
        if k in ev and not isinstance(ev[k], list):
            missing.append(k)
    if inferential and isinstance(ev.get("limitations"), list) and not ev["limitations"]:
        missing.append("limitations")
    return sorted(set(missing))


def assemble(*, plan: AnalysisPlan, plan_hash: str, run_id: str, project_id: str, asset: Mapping[str, Any], version: Mapping[str, Any], lineage: Sequence[Dict[str, Any]],
             results: Sequence[Dict[str, Any]], specs: Mapping[str, OpSpec], rows_in_scope: int, rows_in_dataset: int, sampled: Optional[Dict[str, Any]], executed_at: str,
             isolation: Dict[str, Any], run_warnings: Sequence[str], saved: bool = False) -> Dict[str, Any]:
    per_op: List[Dict[str, Any]] = []
    operations: List[Dict[str, Any]] = []
    all_cols: List[str] = []
    missing: List[str] = []
    binning, tests, seeds = [], [], []
    for i, (op, res) in enumerate(zip(plan.operations, results)):
        ev = res.get("evidence") or {}
        spec = specs[op.op]
        operations.append({"index": i, "op": op.op, "title": spec.title, "status": res["status"], "method": ev.get("method"), "formula_ref": ev.get("formula_ref"), "parameters": op.params})
        for col in ev.get("columns", []) or []:
            if col not in all_cols:
                all_cols.append(col)
        if res["status"] == "failed":
            continue
        gaps = op_missing(res, spec.inferential) if res["status"] == "ok" else []
        res["evidence_status"] = Evidence.INCOMPLETE if gaps else Evidence.VERIFIED
        if gaps:
            missing += [f"operations[{i}].{g}" for g in gaps]
        per_op.append({"index": i, "op": op.op, "status": res["status"], "evidence_status": res.get("evidence_status", Evidence.VERIFIED if res["status"] != "ok" else None),
                       "sample_size": ev.get("sample_size"), "uncertainty": ev.get("uncertainty"), "assumptions": ev.get("assumptions", []), "limitations": ev.get("limitations", []),
                       "practical_relevance": ev.get("practical_relevance")})
        if ev.get("binning"):
            binning.append({"index": i, "binning": ev["binning"]})
        if ev.get("test_selection"):
            tests.append({"index": i, **ev["test_selection"]})
        if ev.get("seed") is not None:
            seeds.append(ev["seed"])
    ev_doc: Dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "source": {"project_id": project_id, "asset_id": asset["id"], "source_file": asset["name"], "version_id": version["id"], "version_number": version["version_number"],
                   "version_kind": version["kind"], "checksum_sha256": version["checksum_sha256"], "format": version["format"],
                   "rows_in_dataset": rows_in_dataset, "rows_in_scope": rows_in_scope,
                   "row_snapshot": {"reference": f"asset:{asset['id']}/version:{version['id']}/sha256:{version['checksum_sha256']}", "filters": [f.to_dict() for f in plan.filters],
                                    "filters_hash": sha256_json([f.to_dict() for f in plan.filters]), "sample": sampled,
                                    "how_to_retrieve": "POST /api/v1/analytics/runs/{run_id}/rows returns the rows behind a result"},
                   "authorised_scope": {"project_id": project_id, "access": "owner of the project", "cross_project": False}},
        "calculation": {"calc_version": CALC_VERSION, "plan_hash": plan_hash, "operations": operations, "libraries": library_versions(), "filters": [f.to_dict() for f in plan.filters],
                        "grouping": sorted({str(v) for o in plan.operations for k, v in o.params.items() if k in ("group_by", "group", "row", "column") and isinstance(v, str)}
                                           | {g for o in plan.operations for k, v in o.params.items() if k == "group_by" and isinstance(v, list) for g in v}),
                        "executed_at": executed_at, "isolation": isolation},
        "inputs": {"selected_columns": all_cols, "sample": sampled, "seed": plan.seed, "null_policy": plan.null_policy, "binning": binning, "test_selection": tests,
                   "transformation_lineage": list(lineage)},
        "interpretation": {"per_operation": per_op},
        "access": {"run_id": run_id, "rows_endpoint": f"/analytics/runs/{run_id}/rows", "export_endpoint": f"/analytics/runs/{run_id}/export", "saved_analysis": bool(saved),
                   "warnings": list(run_warnings) + [w for r in results for w in r.get("warnings", [])]},
    }
    for path, ok in [("source.project_id", project_id), ("source.asset_id", asset["id"]), ("source.version_id", version["id"]), ("source.checksum_sha256", version["checksum_sha256"]),
                     ("calculation.calc_version", CALC_VERSION), ("calculation.plan_hash", plan_hash), ("calculation.libraries", library_versions()),
                     ("calculation.executed_at", executed_at), ("inputs.null_policy", plan.null_policy), ("inputs.seed", plan.seed), ("access.run_id", run_id)]:
        if ok in (None, "", {}):
            missing.append(path)
    failed = [r for r in results if r["status"] == "failed"]
    material = [r for r in results if r["status"] == "ok"]
    if not material and not failed:
        missing.append("operations (no completed result)")
    ev_doc["missing"] = sorted(set(missing))
    ev_doc["status"] = Evidence.VERIFIED if not missing and not failed else Evidence.INCOMPLETE
    if failed:
        ev_doc["incomplete_reason"] = "One or more analyses failed; their results are not part of this evidence."
    return ev_doc


def result_hash(plan_hash: str, results: Sequence[Mapping[str, Any]], rows_in_scope: int) -> str:
    stable = [{"op": r["op"], "params": r["params"], "status": r["status"], "data": r.get("data"), "error": (r.get("error") or {}).get("code")} for r in results]
    return sha256_json({"plan": plan_hash, "rows_in_scope": rows_in_scope, "results": stable, "calc_version": CALC_VERSION})
