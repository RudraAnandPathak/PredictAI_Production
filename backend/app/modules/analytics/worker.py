"""Subprocess entry point: read job.json + table.csv from the private work directory, run the plan, write result.json.
Prints nothing about the data. Any failure becomes a coded message without values."""
from __future__ import annotations

import json
import sys
from pathlib import Path


MEMORY_MARKERS = ("failed to map segment", "annot allocate memory", "out of memory")


def is_memory_failure(exc: BaseException) -> bool:
    """True if the exception, or anything in its cause chain, is a MemoryError or the loader's 'could not map memory' failure (what a
    kernel address-space limit looks like when it hits while a library is being imported)."""
    seen = 0
    while exc is not None and seen < 10:
        if isinstance(exc, MemoryError) or any(m in str(exc) for m in MEMORY_MARKERS):
            return True
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return False


def main(work_dir: str) -> None:
    work = Path(work_dir)
    out = work / "result.json"
    try:
        from .errors import AnalysisError
        from .executor import plan_from_dict, run_plan

        job = json.loads((work / "job.json").read_text(encoding="utf-8"))
        types = {d["name"]: d.get("type") or "text" for d in job["declared"]}
        plan = plan_from_dict(job["plan"], types)
        with open(work / "table.csv", "rb") as stream:
            payload = run_plan(plan, stream, job["declared"], int(job["rows_total"]), job["limits"])
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        from .sandbox import peak_rss_bytes          # reported beside (not inside) the result so it can never change a result fingerprint

        peak = peak_rss_bytes()
        if peak is not None:
            (work / "peak_rss.txt").write_text(str(peak), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        if not is_memory_failure(exc):
            _generic(out, exc)
            return
        out.write_text(json.dumps({"fatal": {"code": "ANALYSIS_MEMORY", "message": "The analysis needed more memory than allowed. Narrow the columns or filter the data.", "type": "MemoryError"}}), encoding="utf-8")


def _generic(out: Path, exc: BaseException) -> None:
    code, message = getattr(exc, "code", None), getattr(exc, "message", None)
    if not code:
        code, message = "ANALYSIS_ERROR", "The analysis could not be completed."
    out.write_text(json.dumps({"fatal": {"code": code, "message": message, "type": type(exc).__name__}}), encoding="utf-8")
