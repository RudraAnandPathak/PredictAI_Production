"""Runs an analysis plan against a dataset snapshot with resource limits. Two isolation modes:
  subprocess - a separate Python process with CPU, memory, file-size and open-file limits, a wall-clock timeout and a scrubbed environment
               (no application secrets), reading only a private temporary copy of the table; killed if it overruns;
  inprocess  - the same code in the API process (used by tests and as a fallback when subprocesses are unavailable).
Neither mode evaluates code: a plan is a closed-vocabulary data structure that was validated before it got here."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Mapping, Optional, Sequence

from . import sandbox
from .constants import LIMITS
from .errors import AnalysisError, AnalysisTimeout, failed as failed_error, too_large
from .frame import apply_filters, load_snapshot
from .ops import REGISTRY
from .ops.common import OpContext, py
from .plan import AnalysisPlan, Filter, Operation, build_plan, role_of

BACKEND_ROOT = Path(__file__).resolve().parents[3]
MAX_STREAM = 4096


def _truncate(text: str, limit: int = MAX_STREAM) -> str:
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more characters not kept]"


def limits_for(extended: bool) -> Dict[str, Any]:
    i = 1 if extended else 0
    return {"max_rows": LIMITS["max_rows"][i], "max_cells": LIMITS["max_cells"][i], "time_budget_s": LIMITS["time_budget_s"][i], "memory_mb": LIMITS["memory_mb"][i],
            "max_result_bytes": LIMITS["max_result_bytes"], "extended": extended}


def plan_from_dict(raw: Mapping[str, Any], types: Mapping[str, str]) -> AnalysisPlan:
    """Re-validates a serialised plan (used in the worker and for re-runs) — never trusts its input."""
    return build_plan(kind=raw["kind"], asset_id=raw["dataset"]["asset_id"], version_id=raw["dataset"]["version_id"], operations=raw["operations"], filters=raw.get("filters"),
                      null_policy=raw.get("null_policy"), seed=raw.get("seed"), sample_rows=raw.get("sample_rows"), registry=REGISTRY, types=types)


def run_plan(plan: AnalysisPlan, stream: BinaryIO, declared: Sequence[Mapping[str, Any]], rows_total: int, limits: Mapping[str, Any]) -> Dict[str, Any]:
    """The computation proper. Returns a JSON-safe dict; never raises for per-operation failures (they are recorded)."""
    started = time.monotonic()
    deadline = started + float(limits["time_budget_s"])
    out_buf, warn_log, errors = io.StringIO(), [], []
    snap = load_snapshot(stream, declared, rows_total=rows_total, max_rows=int(limits["max_rows"]), max_cells=int(limits["max_cells"]), sample_rows=plan.sample_rows, seed=plan.seed)
    df = apply_filters(snap.df, snap.types, plan.filters)
    _preflight_memory(snap.df, limits)
    results: List[Dict[str, Any]] = []
    with contextlib.redirect_stdout(out_buf), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for op in plan.operations:
            spec = REGISTRY[op.op]
            ctx = OpContext(df=df, types=snap.types, invalid=snap.invalid, invalid_rows=snap.invalid_rows, seed=plan.seed, null_policy=plan.null_policy, rows_total=snap.rows_total, deadline=deadline)
            t0 = time.monotonic()
            try:
                res = spec.fn(ctx, **op.params)
            except AnalysisTimeout as exc:
                res = {"status": "failed", "data": None, "evidence": {}, "warnings": [], "error": {"code": exc.code, "message": exc.message}}
            except AnalysisError as exc:
                res = {"status": "failed", "data": None, "evidence": {}, "warnings": [], "error": {"code": exc.code, "message": exc.message}}
            except MemoryError:
                res = {"status": "failed", "data": None, "evidence": {}, "warnings": [], "error": {"code": "ANALYSIS_MEMORY", "message": "The analysis needed more memory than allowed. Narrow the columns or filter the data."}}
            except Exception as exc:  # a bug or a data shape we did not anticipate: record type and place, never values
                tb = exc.__traceback__
                while tb is not None and tb.tb_next is not None:
                    tb = tb.tb_next
                place = f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}" if tb is not None else "?"
                errors.append({"op": op.op, "type": type(exc).__name__, "where": place})
                res = {"status": "failed", "data": None, "evidence": {}, "warnings": [], "error": {"code": "ANALYSIS_ERROR", "message": "This calculation could not be completed for the selected data."}}
            res = {"op": op.op, "params": op.params, **res, "duration_ms": int((time.monotonic() - t0) * 1000)}
            results.append(py(res))
            if time.monotonic() > deadline:
                for rest in plan.operations[len(results):]:
                    results.append({"op": rest.op, "params": rest.params, "status": "failed", "data": None, "evidence": {}, "warnings": [],
                                    "error": {"code": "ANALYSIS_TIMEOUT", "message": "Skipped: the time limit was reached."}, "duration_ms": 0})
                break
    for w in caught:
        text = _truncate(f"{w.category.__name__}: {w.message}", 300)
        if text not in warn_log:
            warn_log.append(text)
    return {"results": results, "rows_in_scope": int(len(df)), "rows_in_dataset": int(snap.rows_total), "rows_loaded": int(snap.rows_loaded), "sampled": snap.sampled,
            "snapshot_warnings": snap.warnings, "types": snap.types, "stdout": _truncate(out_buf.getvalue()), "library_warnings": warn_log[:20], "errors": errors,
            "duration_ms": int((time.monotonic() - started) * 1000)}


WORKING_SET_FACTOR = 3      # analyses copy, sort and rank columns, so a table needs a few times its own size in working memory


def _preflight_memory(df: Any, limits: Mapping[str, Any]) -> None:
    """Platform-independent guard: refuse before computing if the loaded table cannot plausibly fit in the memory limit."""
    budget = int(limits.get("memory_mb", 0)) * 1024 * 1024
    if budget <= 0:
        return
    needed = int(df.memory_usage(deep=True).sum()) * WORKING_SET_FACTOR
    if needed > budget:
        raise AnalysisError("ANALYSIS_MEMORY", "This table is too large for the memory limit. Narrow the columns or filter the data.")


# ------------------------------------------------------------------------------------------------ isolation
def subprocess_available() -> bool:
    return os.name == "posix" and bool(sys.executable)


def execute(plan: AnalysisPlan, open_table: Callable[[], BinaryIO], declared: Sequence[Mapping[str, Any]], rows_total: int, limits: Mapping[str, Any], *, isolation: str) -> Dict[str, Any]:
    """Run the plan and report how it was isolated. `isolation` is 'subprocess' or 'inprocess'."""
    if isolation == "subprocess" and not subprocess_available():
        isolation = "inprocess"
    if isolation == "inprocess":
        with open_table() as stream:
            out = run_plan(plan, stream, declared, rows_total, limits)
        out["isolation"] = {"mode": "inprocess", "limits_applied": ["row/cell caps", "cooperative time budget"], "note": "same process as the API"}
        return out
    work = Path(tempfile.mkdtemp(prefix="pai-run-"))
    try:
        os.chmod(work, 0o700)
        with open_table() as src, open(work / "table.csv", "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.chmod(work / "table.csv", 0o600)
        job = {"plan": plan.to_dict(), "declared": list(declared), "rows_total": rows_total, "limits": dict(limits)}
        (work / "job.json").write_text(json.dumps(job), encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "LC_ALL": "C.UTF-8", "TMPDIR": str(work)}
        memory_bytes = int(limits["memory_mb"]) * 1024 * 1024
        argv = [sys.executable, "-I", str(Path(__file__).with_name("_boot.py")), str(work), str(BACKEND_ROOT), str(int(limits["time_budget_s"]) + 5), str(int(limits["memory_mb"])), "1" if sandbox.hard_memory_limit_supported() else "0"]
        proc = sandbox.run_limited(argv, cwd=str(work), env=env, timeout=float(limits["time_budget_s"]) + 10, memory_bytes=memory_bytes)
        if proc.killed == "time":
            raise AnalysisTimeout()
        if proc.killed == "memory":
            raise AnalysisError("ANALYSIS_MEMORY", "The analysis needed more memory than allowed. Narrow the columns or filter the data.")
        result_path = work / "result.json"
        if proc.returncode != 0 or not result_path.exists():
            if proc.returncode in (-9, -24, -11) or any(m in proc.stderr[-4000:] for m in (b"MemoryError", b"failed to map segment", b"Cannot allocate memory")):
                raise AnalysisError("ANALYSIS_MEMORY", "The analysis needed more memory or time than allowed. Narrow the columns or filter the data.")
            raise AnalysisError("ANALYSIS_ERROR", "The analysis process stopped unexpectedly.")
        if result_path.stat().st_size > int(limits["max_result_bytes"]) * 2:
            raise AnalysisError("RESULT_TOO_LARGE", "The result is too large. Choose fewer columns or a smaller selection.")
        peak_file = work / "peak_rss.txt"
        if peak_file.exists():
            try:
                peak = int(peak_file.read_text().strip())
            except ValueError:
                peak = 0
            if peak > memory_bytes:                    # a short spike the watchdog could not catch: the result is discarded, not trusted
                raise AnalysisError("ANALYSIS_MEMORY", "The analysis needed more memory than allowed. Narrow the columns or filter the data.")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if "fatal" in payload:
            raise AnalysisError(payload["fatal"]["code"], payload["fatal"]["message"])
        mem = sandbox.describe_memory(int(limits["memory_mb"]))
        applied = ["CPU seconds", "output file size", "open files", "wall-clock timeout", "scrubbed environment", "private temporary copy of the table",
                   "memory watchdog (resident memory)", "table-size preflight"]
        if mem["mode"] == "hard":
            applied.insert(1, "address space (kernel limit, verified)")
        payload["isolation"] = {"mode": "subprocess", "limits_applied": applied, "memory": mem, "pid_isolated": True, "stderr_bytes": len(proc.stderr)}
        return payload
    finally:
        shutil.rmtree(work, ignore_errors=True)
