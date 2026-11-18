"""Runs one modelling task (assess / train / predict / predict_batch / pdp) either in a resource-limited subprocess or, for tests and as a fallback, in-process.

Subprocess mode reuses Phase 4's sandbox: its own process group, no stdin, scrubbed environment (no application secrets), CPU/file/open-file limits,
a wall-clock timeout, the resident-memory watchdog, and the kernel address-space limit only where a probe proved the OS enforces it.
Added for long jobs: the parent polls the worker's progress file and a cancel flag, and kills the process group when cancellation is requested."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Mapping, Optional, Sequence, Union

from ..analytics import sandbox
from ..analytics.executor import BACKEND_ROOT, subprocess_available
from .constants import LIMITS
from .errors import Cancelled, MlError

PROGRESS_EVERY = 0.4
Source = Union[bytes, Callable[[], BinaryIO]]


class TaskOutput:
    def __init__(self, result: Dict[str, Any], files: Dict[str, bytes], isolation: Dict[str, Any], seconds: float) -> None:
        self.result, self.files, self.isolation, self.seconds = result, files, isolation, seconds


def limits_for(extended: bool) -> Dict[str, Any]:
    i = 1 if extended else 0
    return {"max_rows": LIMITS["max_rows"][i], "max_cells": LIMITS["max_cells"][i], "time_budget_s": LIMITS["time_budget_s"][i], "memory_mb": LIMITS["memory_mb"][i],
            "max_model_bytes": LIMITS["max_model_bytes"][i], "max_result_bytes": LIMITS["max_result_bytes"], "extended": extended}


def _stage(work: Path, name: str, src: Source) -> None:
    path = work / name
    if isinstance(src, (bytes, bytearray)):
        path.write_bytes(bytes(src))
    else:
        with src() as stream, open(path, "wb") as dst:
            shutil.copyfileobj(stream, dst, 1024 * 1024)
    os.chmod(path, 0o600)


def _read_progress(work: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads((work / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_task(task: str, job: Mapping[str, Any], sources: Mapping[str, Source], *, extended: bool, isolation: str, budget_s: float,
             on_progress: Optional[Callable[[Dict[str, Any]], None]] = None, is_cancelled: Optional[Callable[[], bool]] = None,
             heartbeat: Optional[Callable[[], None]] = None, collect: Sequence[str] = ()) -> TaskOutput:
    limits = limits_for(extended)
    if isolation == "subprocess" and not subprocess_available():
        isolation = "inprocess"
    work = Path(tempfile.mkdtemp(prefix="pai-ml-"))
    started = time.monotonic()
    try:
        os.chmod(work, 0o700)
        for name, src in sources.items():
            _stage(work, name, src)
        spec = {**dict(job), "task": task, "limits": limits, "extended": extended, "budget_s": budget_s}
        (work / "job.json").write_text(json.dumps(spec), encoding="utf-8")
        if isolation == "inprocess":
            from . import worker

            if is_cancelled and is_cancelled():
                raise Cancelled()
            worker.main(str(work), on_progress=on_progress or (lambda d: None), is_cancelled=is_cancelled)
            iso: Dict[str, Any] = {"mode": "inprocess", "limits_applied": ["row/cell caps", "cooperative time budget", "cooperative cancellation"], "note": "same process as the API"}
        else:
            iso = _run_subprocess(work, limits, budget_s, on_progress, is_cancelled, heartbeat)
        result_path = work / "result.json"
        if not result_path.exists():
            raise MlError("ML_ERROR", "The modelling process ended without a result.")
        if result_path.stat().st_size > limits["max_result_bytes"] * 2:
            raise MlError("RESULT_TOO_LARGE", "The result is too large to keep.")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if payload.get("cancelled"):
            raise Cancelled()
        if "fatal" in payload:
            raise MlError(payload["fatal"]["code"], payload["fatal"]["message"])
        files: Dict[str, bytes] = {}
        for name in collect:
            p = work / name
            if p.exists():
                if p.stat().st_size > limits["max_model_bytes"] * 2:
                    raise MlError("RESULT_TOO_LARGE", "An output file is larger than allowed.")
                files[name] = p.read_bytes()
        return TaskOutput(payload, files, iso, time.monotonic() - started)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_subprocess(work: Path, limits: Mapping[str, Any], budget_s: float, on_progress: Optional[Callable[[Dict[str, Any]], None]],
                    is_cancelled: Optional[Callable[[], bool]], heartbeat: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    env = {"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "LC_ALL": "C.UTF-8", "TMPDIR": str(work),
           "JOBLIB_MULTIPROCESSING": "0", "LOKY_MAX_CPU_COUNT": "1"}
    memory_bytes = int(limits["memory_mb"]) * 1024 * 1024
    hard = float(budget_s) + 30.0
    file_mb = int(limits["max_model_bytes"]) // (1024 * 1024) * 2 + 64
    argv = [sys.executable, "-I", str(Path(sandbox.__file__).with_name("_boot.py")), str(work), str(BACKEND_ROOT), str(int(hard) + 5), str(int(limits["memory_mb"])),
            "1" if sandbox.hard_memory_limit_supported() else "0", "predictive", str(file_mb)]
    state = {"last": 0.0, "seen": None}

    def poll() -> Optional[str]:
        now = time.monotonic()
        if now - state["last"] < PROGRESS_EVERY:
            return None
        state["last"] = now
        if heartbeat:
            heartbeat()
        if on_progress:
            p = _read_progress(work)
            if p is not None and p != state["seen"]:
                state["seen"] = p
                on_progress(p)
        if is_cancelled and is_cancelled():
            return "cancelled"
        return None

    proc = sandbox.run_limited(argv, cwd=str(work), env=env, timeout=hard, memory_bytes=memory_bytes, on_poll=poll)
    if on_progress:                                         # one last read so the final state is not lost between polls
        p = _read_progress(work)
        if p is not None and p != state["seen"] and proc.killed is None:
            on_progress(p)
    if proc.killed == "cancelled":
        raise Cancelled()
    if proc.killed == "time":
        raise MlError("TRAINING_TIMEOUT", "Training reached its time limit and was stopped. Use fewer rows or columns, choose fewer algorithms, or turn off tuning.")
    if proc.killed == "memory":
        raise MlError("ML_MEMORY", "This needed more memory than allowed. Exclude columns, use fewer rows, or choose lighter algorithms.")
    result = work / "result.json"
    if proc.returncode != 0 or not result.exists():
        if proc.returncode in (-9, -24, -11) or any(m in proc.stderr[-4000:] for m in (b"MemoryError", b"failed to map segment", b"Cannot allocate memory")):
            raise MlError("ML_MEMORY", "This needed more memory or time than allowed. Exclude columns, use fewer rows, or choose lighter algorithms.")
        raise MlError("ML_ERROR", "The modelling process stopped unexpectedly.")
    peak_file = work / "peak_rss.txt"
    peak = 0
    if peak_file.exists():
        try:
            peak = int(peak_file.read_text().strip())
        except ValueError:
            peak = 0
        if peak > memory_bytes:                              # a spike the watchdog could not catch: the result is not trusted
            raise MlError("ML_MEMORY", "This needed more memory than allowed. Exclude columns, use fewer rows, or choose lighter algorithms.")
    mem = sandbox.describe_memory(int(limits["memory_mb"]))
    applied = ["CPU seconds", "output file size", "open files", "wall-clock timeout", "scrubbed environment (no application secrets)", "private temporary work directory",
               "memory watchdog (resident memory)", "table-size preflight", "cancellation (process group killed)"]
    if mem["mode"] == "hard":
        applied.insert(1, "address space (kernel limit, verified)")
    return {"mode": "subprocess", "limits_applied": applied, "memory": mem, "pid_isolated": True, "peak_rss_mb": round(peak / (1024 * 1024), 1) if peak else None, "stderr_bytes": len(proc.stderr)}
