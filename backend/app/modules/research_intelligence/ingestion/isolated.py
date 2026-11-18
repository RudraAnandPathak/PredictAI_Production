"""Run extraction in a separate, resource-limited process.

A parser handed a hostile file can hang, allocate without bound or crash. Here it can only do that to a throw-away child: address-space, CPU-time, file-size and descriptor limits are set
before it starts, it has a wall-clock timeout, a scrubbed environment (no secrets, no network configuration), a private temporary directory, and its output is bounded. The child imports
only extract.py: it has no database handle and no application code. This is NOT a security sandbox against a determined attacker (it runs as the worker's user); it contains accidents and
resource attacks. Network and filesystem confinement for untrusted multi-user hosting are listed under Known limitations."""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
from typing import Any, Dict

from .extract import ExtractError

MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extract_main.py")
MAX_OUTPUT_BYTES = 60 * 1024 * 1024


def run_extract(fmt: str, data: bytes, *, max_pages: int, timeout_s: int, memory_mb: int, base_url: str = "") -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ri-extract-") as tmp:
        src, dst = os.path.join(tmp, "input.bin"), os.path.join(tmp, "out.json")
        with open(src, "wb") as f:
            f.write(data)

        def limits() -> None:
            os.setsid()
            mem = memory_mb * 1024 * 1024
            for res, val in ((resource.RLIMIT_AS, mem), (resource.RLIMIT_CPU, timeout_s + 5), (resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES), (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_CORE, 0)):
                try:
                    resource.setrlimit(res, (val, val))
                except (ValueError, OSError):
                    pass

        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": tmp, "HOME": tmp, "RI_BASE_URL": base_url, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run([sys.executable, "-I", MAIN, fmt, src, dst, str(max_pages)], cwd=tmp, env=env, preexec_fn=limits, capture_output=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired:
            raise ExtractError("EXTRACT_TIMEOUT", "Reading this file took too long, so it was stopped.") from None
        if proc.returncode != 0 or not os.path.exists(dst):
            low = (proc.stderr or b"").lower()
            if b"memoryerror" in low or proc.returncode in (-9, -11):
                raise ExtractError("EXTRACT_RESOURCE_LIMIT", "This file needs more memory than the import limit allows.")
            raise ExtractError("EXTRACT_FAILED", "This file could not be read.")
        try:
            with open(dst, "r", encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError):
            raise ExtractError("EXTRACT_FAILED", "This file could not be read.") from None
    if "error" in result:
        raise ExtractError(str(result["error"].get("code", "EXTRACT_FAILED")), str(result["error"].get("message", "This file could not be read.")))
    return result["ok"]
