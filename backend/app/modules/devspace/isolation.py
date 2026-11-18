"""Execution providers. An ordinary subprocess is NOT a multi-user sandbox, and this module never says it is.

local_trusted  - the job is a child process of the worker, run with kernel resource limits, a scrubbed environment and a private working directory, inside a NO-NETWORK
                 and private-PID namespace where the OS offers one (Linux: `unshare`; macOS: `sandbox-exec` with a deny-network profile). It cannot read other people's work
                 BY DESIGN of the engine (only the job's own directory is handed to it) but the process can still open any path its own user can read. Suitable for ONE trusted
                 person on their own machine. Refused when the server is hosted.
docker         - the job runs in a container with no network, a read-only root, dropped capabilities, a PID limit and only the job's own directories mounted. The Docker socket is
                 never mounted. The command is built and unit-tested here; running it needs a Docker daemon, which the build environment did not have, so it is UNVERIFIED.
off            - nothing runs."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

DOCKER_IMAGE_DEFAULT = "python:3.12-slim"
_probe_cache: Dict[str, Any] = {}


def _ok(argv: Sequence[str], timeout: float = 5.0) -> bool:
    try:
        return subprocess.run(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def probe(force: bool = False) -> Dict[str, Any]:
    if _probe_cache and not force:
        return _probe_cache
    system = platform.system()
    userns = None                      # prefer mapping the CURRENT user (no root inside the namespace, so file permissions still bind the job); fall back to -r on older util-linux
    if bool(shutil.which("unshare")) and system == "Linux":
        for flag in ("--map-current-user", "-r"):
            if _ok(["unshare", flag, "-n", "-p", "-f", "-m", "--mount-proc", "true"]):
                userns = flag
                break
    unshare = userns is not None
    sandbox_exec = system == "Darwin" and bool(shutil.which("sandbox-exec")) and _ok(["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", "true"])
    docker = bool(shutil.which("docker")) and _ok(["docker", "info"], 8.0)
    _probe_cache.clear()
    _probe_cache.update({"system": system, "python": platform.python_version(), "linux_namespaces": unshare, "unshare_user_flag": userns, "macos_sandbox_exec": sandbox_exec, "docker_daemon": docker})
    return _probe_cache


class Provider:
    name = "none"

    def network_isolation(self) -> Optional[str]:           # None when it cannot be enforced
        return None

    def wrap(self, argv: List[str], *, workdir: str, env: Dict[str, str], limits: Dict[str, Any], data_dir: Optional[str], out_dir: str, boot_dir: str) -> List[str]:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        raise NotImplementedError


class LocalProvider(Provider):
    name = "local_trusted"

    def __init__(self) -> None:
        self.p = probe()

    def network_isolation(self) -> Optional[str]:
        if self.p["linux_namespaces"]:
            return "linux-namespaces (no network, private PID and mount namespace)"
        if self.p["macos_sandbox_exec"]:
            return "macos-sandbox-exec (network denied)"
        return None

    def wrap(self, argv, *, workdir, env, limits, data_dir, out_dir, boot_dir):
        if self.p["linux_namespaces"]:
            # Defence in depth inside the mount namespace: every OTHER project's folder under the development root is covered by an empty tmpfs before the job starts, so a job cannot
            # read a sibling project's files through the host path. If any cover cannot be mounted the job does not start (fail closed). This does NOT make the provider multi-user safe:
            # the rest of the host filesystem stays readable by the job's user (see describe()).
            project_dir = os.path.dirname(os.path.dirname(os.path.dirname(boot_dir.rstrip("/"))))
            script = 'root="$1"; keep="$2"; shift 2; for d in "$root"/*/; do d="${d%/}"; [ "$d" = "$keep" ] || mount -t tmpfs -o size=4k,mode=0555 tmpfs "$d" || exit 97; done; exec env -u PWD -u OLDPWD "$@"'
            return ["unshare", self.p["unshare_user_flag"], "-n", "-p", "-f", "-m", "--mount-proc", "--", "sh", "-c", script, "sh", os.path.dirname(project_dir), project_dir] + argv
        if self.p["macos_sandbox_exec"]:
            return ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)"] + argv
        raise RuntimeError("no network isolation available")

    def describe(self) -> Dict[str, Any]:
        iso = self.network_isolation()
        return {"provider": self.name, "network": iso or "UNAVAILABLE on this host: jobs are refused rather than run with network access",
                "process_limits": "kernel rlimits (CPU, file size, open files); memory and process count are monitored and the whole process group is killed",
                "filesystem": "NOT isolated: other projects' folders under the development root are hidden on Linux, but the process can still read any other file its own user can read. Do not run other people's code with this provider.",
                "multi_user_safe": False, "suitable_for": "one trusted person on their own machine"}


class DockerProvider(Provider):
    name = "docker"

    def __init__(self, image: str = DOCKER_IMAGE_DEFAULT) -> None:
        self.image = image
        self.p = probe()

    def network_isolation(self) -> Optional[str]:
        return "docker --network none" if self.p["docker_daemon"] else None

    def wrap(self, argv, *, workdir, env, limits, data_dir, out_dir, boot_dir):
        cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", str(int(limits["max_procs"])),
               "--memory", f"{int(limits['memory_mb'])}m", "--cpus", "1", "--user", "65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-w", "/work",
               "-v", f"{workdir}:/work:rw", "-v", f"{out_dir}:/outputs:rw", "-v", f"{boot_dir}:/boot:ro", "-e", "PREDICTAI_OUTPUT_DIR=/outputs", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1"]
        if data_dir:
            cmd += ["-v", f"{data_dir}:/data:ro", "-e", "PREDICTAI_DATA_DIR=/data"]
        for k, v in env.items():
            if k.startswith("PREDICTAI_SECRET_"):
                continue
            if k in ("PREDICTAI_OUTPUT_DIR", "PREDICTAI_DATA_DIR", "HOME", "PATH", "TMPDIR", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE") or k.startswith("DOCKER_"):
                continue
            cmd += ["-e", k]          # name only: docker copies the value from its own environment, so secret values never appear in the command line (`ps`) or in `docker inspect` arguments
        return cmd + [self.image] + argv

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name, "network": self.network_isolation() or "UNAVAILABLE: no Docker daemon reachable", "process_limits": "container pids/memory/cpu limits",
                "filesystem": "read-only root; only the job's work, data (read-only) and output folders are mounted; the Docker socket is never mounted",
                "multi_user_safe": False, "suitable_for": "stronger isolation than local_trusted. NOT yet demonstrated: the build environment had no Docker daemon, so this path was unit-tested but never run"}


def provider_for(settings) -> Optional[Provider]:
    mode = settings.dev_execution
    if mode == "local_trusted":
        return LocalProvider()
    if mode == "docker":
        return DockerProvider(os.environ.get("PREDICTAI_DEV_DOCKER_IMAGE") or DOCKER_IMAGE_DEFAULT)
    return None


def describe(settings) -> Dict[str, Any]:
    prov = provider_for(settings)
    base = {"mode": settings.dev_execution, "host": probe()}
    if prov is None:
        return {**base, "enabled": False, "reason": "Code execution is switched off (PREDICTAI_DEV_EXECUTION=off). Files, environments' manifests, notebooks and Git can still be used; nothing runs.",
                "multi_user_safe": False}
    d = prov.describe()
    return {**base, "enabled": prov.network_isolation() is not None, **d,
            "statement": "Secure multi-user execution is NOT claimed by this release. Isolation has been demonstrated only as described above."}
