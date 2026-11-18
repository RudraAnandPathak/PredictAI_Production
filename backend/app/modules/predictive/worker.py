"""Entry point of the modelling subprocess (started by `_boot.py` after the kernel limits are applied). It reads job.json and the input files from its private
work directory, does ONE task, and writes result.json (+ output files). Nothing is read from the network, and no application secret is in its environment.

Tasks: assess | train | predict | predict_batch | pdp. Errors become coded messages that never contain data values."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

WORKING_SET_FACTOR = 8            # training copies the table for folds and models; refuse early rather than be killed late
MEMORY_MARKERS = ("failed to map segment", "annot allocate memory", "out of memory")


def is_memory_failure(exc: BaseException) -> bool:
    seen = 0
    while exc is not None and seen < 10:
        if isinstance(exc, MemoryError) or any(m in str(exc) for m in MEMORY_MARKERS):
            return True
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return False


def write_json_atomic(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _load_table(work: Path, job: Dict[str, Any]):
    from ..analytics.errors import AnalysisError
    from ..analytics.frame import load_snapshot

    limits = job["limits"]
    with open(work / "table.csv", "rb") as stream:
        snap = load_snapshot(stream, job["declared"], rows_total=int(job["rows_total"]), max_rows=int(limits["max_rows"]), max_cells=int(limits["max_cells"]))
    budget = int(limits.get("memory_mb", 0)) * 1024 * 1024
    if budget > 0 and int(snap.df.memory_usage(deep=True).sum()) * WORKING_SET_FACTOR > budget:
        raise AnalysisError("ML_MEMORY", "This table is too large for the memory limit. Exclude columns or use fewer rows.")
    return snap


def run(work: Path, job: Dict[str, Any], on_progress: Optional[Callable[[Dict[str, Any]], None]], is_cancelled: Optional[Callable[[], bool]]) -> Dict[str, Any]:
    task = job["task"]
    if task == "assess":
        from . import prep
        from .config import parse_config

        snap = _load_table(work, job)
        cfg = parse_config(job["config"], snap.types, extended=bool(job["extended"]))
        return {"assessment": prep.assess(snap.df, snap.types, cfg, extended=bool(job["extended"]), seed=cfg["seed"], snapshot_info=job.get("snapshot_info"))}
    if task == "train":
        from . import train
        from .config import parse_config

        snap = _load_table(work, job)
        cfg = parse_config(job["config"], snap.types, extended=bool(job["extended"]))
        rep = train.Reporter(on_progress, is_cancelled, time_budget_s=float(job.get("budget_s") or 120))
        res = train.run_automl(snap.df, snap.types, cfg, extended=bool(job["extended"]), rep=rep, dataset=job.get("dataset") or {})
        (work / "model.joblib").write_bytes(res.model_bytes)
        (work / "background.csv").write_bytes(res.background_csv)
        return {"report": res.report, "meta": res.meta}
    # the remaining tasks load a saved model; its bytes were verified by the parent against the hash it recorded when it wrote the file
    from . import predict
    from ..analytics.errors import AnalysisError  # noqa: F401

    bundle = predict.load_bundle((work / "model.joblib").read_bytes())
    background = None
    if (work / "background.csv").exists():
        import pandas as pd

        background = pd.read_csv(work / "background.csv", dtype=object, keep_default_na=False, na_values=[""])
    if task == "predict":
        return {"prediction": predict.predict_one(bundle, job["values"], background, explain=bool(job.get("explain", True)), seed=int(job.get("seed", 0)))}
    if task == "predict_batch":
        snap = _load_table(work, job)
        csv_bytes, summary = predict.predict_table(bundle, snap.df, source_rows=[int(i) for i in snap.df.index], max_rows=int(job["max_rows"]),
                                                   progress=(lambda d, n: on_progress({"phase": "predicting", "message": f"Predicted {d:,} of {n:,} rows", "fraction": round(d / n, 4)}) if on_progress else None),
                                                   cancel=(lambda: (_ for _ in ()).throw(_cancelled()) if is_cancelled and is_cancelled() else None))
        (work / "predictions.csv").write_bytes(csv_bytes)
        return {"summary": summary}
    if task == "pdp":
        from . import explain

        if background is None:
            from .errors import MlError

            raise MlError("NO_REFERENCE_ROWS", "No reference rows are stored with this model, so this chart cannot be drawn.")
        feature = job["feature"]
        spec = next((s for s in bundle["input_schema"] if s["name"] == feature), None)
        if spec is None:
            from .errors import MlError

            raise MlError("UNKNOWN_INPUT", "This column is not an input of the model.")
        names = [s["name"] for s in bundle["input_schema"]]
        coerced = bundle["pipeline"].named_steps["coerce"].transform(background[names])
        return {"partial_dependence": explain.partial_dependence(bundle["pipeline"], coerced, feature, spec["role"], bundle["problem"], bundle.get("positive_index"), bundle["classes"], seed=int(job.get("seed", 0)))}
    from .errors import MlError

    raise MlError("TASK_UNKNOWN", "Unknown task.")


def _cancelled() -> BaseException:
    from .errors import Cancelled

    return Cancelled()


def main(work_dir: str, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None, is_cancelled: Optional[Callable[[], bool]] = None) -> None:
    work = Path(work_dir)
    out = work / "result.json"
    progress_path = work / "progress.json"
    if on_progress is None:
        def on_progress(d: Dict[str, Any]) -> None:           # noqa: F811  subprocess mode: the parent reads this file
            try:
                write_json_atomic(progress_path, d)
            except OSError:
                pass
    try:
        from .errors import Cancelled, MlError

        job = json.loads((work / "job.json").read_text(encoding="utf-8"))
        payload = run(work, job, on_progress, is_cancelled)
        write_json_atomic(out, payload)
        from ..analytics.sandbox import peak_rss_bytes

        peak = peak_rss_bytes()
        if peak is not None:
            (work / "peak_rss.txt").write_text(str(peak), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        try:
            from .errors import Cancelled
        except Exception:  # noqa: BLE001
            Cancelled = ()  # type: ignore[assignment]
        if Cancelled and isinstance(exc, Cancelled):
            write_json_atomic(out, {"cancelled": True})
        elif is_memory_failure(exc):
            write_json_atomic(out, {"fatal": {"code": "ML_MEMORY", "message": "This needed more memory than allowed. Exclude columns, use fewer rows, or choose lighter algorithms.", "type": "MemoryError"}})
        else:
            code, message = getattr(exc, "code", None), getattr(exc, "message", None)
            if not code or not isinstance(code, str):
                code, message = "ML_ERROR", "The model task could not be completed."
            write_json_atomic(out, {"fatal": {"code": code, "message": message, "type": type(exc).__name__}})
