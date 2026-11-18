"""Process control for the analysis subprocess: what the operating system really enforces, a memory watchdog that works everywhere,
and honest reporting of both. Standard library only; nothing here sees data.

Why this exists: RLIMIT_AS (address-space limit) is a hard kernel limit on Linux, but macOS does not enforce it (the call may succeed
or fail, and allocations are not refused). Rather than assume, `hard_memory_limit_supported()` PROBES the running platform, and
`run_limited()` adds a parent-side watchdog that samples the child's resident memory (RSS) and kills its process group when it
exceeds the limit. The watchdog is the cross-platform control; RLIMIT_AS is an extra hard backstop only where the probe proves it works.
Watchdog limits: it polls (default every 20 ms), so a process that allocates faster than it is sampled can overshoot briefly before it is
killed. That is why this is reported as "monitored", never as a hard guarantee, on platforms without a working kernel limit."""
from __future__ import annotations

import functools
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

POLL_SECONDS = 0.02
PROBE_LIMIT_MB = 512


# ------------------------------------------------------------------------------------------------ measuring memory
def _rss_proc(pid: int) -> Optional[int]:
    try:
        fields = Path(f"/proc/{pid}/statm").read_text().split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def _rss_ps(pid: int) -> Optional[int]:
    """`ps -o rss=` reports kilobytes on macOS, Linux and the BSDs."""
    try:
        done = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=2, check=False)
        text = done.stdout.decode("ascii", "ignore").strip()
        return int(text) * 1024 if text else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def rss_bytes(pid: int) -> Optional[int]:
    """Resident memory of a process right now, or None if it cannot be read (it may already have exited)."""
    if sys.platform.startswith("linux"):
        value = _rss_proc(pid)
        if value is not None:
            return value
    return _rss_ps(pid)


def peak_rss_bytes() -> Optional[int]:
    """Peak resident memory of THIS process (ru_maxrss is bytes on macOS and kilobytes on Linux)."""
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, ValueError):
        return None
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


# ------------------------------------------------------------------------------------------------ what the OS enforces
_PROBE = (
    "import resource,sys\n"
    f"lim={PROBE_LIMIT_MB}*1024*1024\n"
    "try:\n"
    "    resource.setrlimit(resource.RLIMIT_AS,(lim,lim))\n"
    "except Exception:\n"
    "    sys.exit(3)\n"
    "try:\n"
    "    b=bytearray(2*lim)\n"
    "except MemoryError:\n"
    "    sys.exit(0)\n"
    "sys.exit(4)\n"
)


@functools.lru_cache(maxsize=1)
def hard_memory_limit_supported() -> bool:
    """True only if a child that lowers RLIMIT_AS is actually refused a large allocation on this machine (checked once, then cached)."""
    if os.name != "posix":
        return False
    try:
        done = subprocess.run([sys.executable, "-I", "-c", _PROBE], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
                              env={"PATH": "/usr/bin:/bin"}, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def describe_memory(memory_mb: int) -> Dict[str, Any]:
    """The truthful statement of how the memory limit is enforced here, for the evidence record and the UI."""
    hard = hard_memory_limit_supported()
    base = {"limit_mb": int(memory_mb), "watchdog": f"resident memory sampled every {int(POLL_SECONDS * 1000)} ms; process group killed above the limit",
            "preflight": "table size is estimated after loading and refused if it cannot fit", "after_run_check": "peak resident memory reported by the worker is compared with the limit"}
    if hard:
        return {**base, "mode": "hard", "kernel_limit": "RLIMIT_AS (verified by a probe on this machine)",
                "note": "Address space is capped by the kernel; the watchdog and checks are additional safeguards."}
    return {**base, "mode": "monitored", "kernel_limit": "unavailable (a probe showed this operating system does not refuse allocations under RLIMIT_AS)",
            "note": "Best effort: memory is monitored and the run is killed when it exceeds the limit, but a very fast allocation can overshoot briefly before it is stopped."}


# ------------------------------------------------------------------------------------------------ running a limited child
class Outcome:
    __slots__ = ("returncode", "stdout", "stderr", "killed", "peak_rss", "seconds")

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes, killed: Optional[str], peak_rss: int, seconds: float) -> None:
        self.returncode, self.stdout, self.stderr, self.killed, self.peak_rss, self.seconds = returncode, stdout, stderr, killed, peak_rss, seconds


def _kill_group(proc: "subprocess.Popen[bytes]") -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def run_limited(argv: Sequence[str], *, cwd: str, env: Mapping[str, str], timeout: float, memory_bytes: int,
                sampler: Callable[[int], Optional[int]] = rss_bytes, poll: float = POLL_SECONDS, on_poll: Optional[Callable[[], Optional[str]]] = None) -> Outcome:
    """Run `argv` in its own session with no stdin, killing the whole process group if it exceeds the wall-clock `timeout` or `memory_bytes` of resident memory.
    `killed` is None, "memory" or "time". Output is collected without deadlocking on full pipes.
    `on_poll` (optional, used by long-running jobs) is called on every poll; if it returns a word such as "cancelled" the process group is killed and that word becomes
    `killed`. It is how a person's cancel request, and progress reporting, reach a running job. Left unset, behaviour is exactly as before."""
    started = time.monotonic()
    proc = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
    peak, killed = 0, None
    out: bytes = b""
    err: bytes = b""
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=poll)
                break
            except subprocess.TimeoutExpired:
                pass
            used = sampler(proc.pid)
            if used is not None:
                peak = max(peak, used)
                if used > memory_bytes:
                    killed = "memory"
            if killed is None and time.monotonic() - started > timeout:
                killed = "time"
            if killed is None and on_poll is not None:
                try:
                    killed = on_poll() or None
                except Exception:  # noqa: BLE001  a faulty callback must never stop the safety limits above from working
                    killed = None
            if killed:
                _kill_group(proc)
                out, err = proc.communicate()
                break
    finally:
        if proc.poll() is None:
            _kill_group(proc)
            proc.wait()
    return Outcome(proc.returncode, out, err, killed, peak, time.monotonic() - started)
