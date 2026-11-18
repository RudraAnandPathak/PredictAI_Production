"""Entry point of the analysis subprocess: apply the kernel resource limits the platform allows, THEN import anything heavy.
Standard library only. Usage: python -I _boot.py <work_dir> <backend_root> <cpu_seconds> <memory_mb> <kernel_address_limit 1|0> [worker] [max_file_mb]
`worker` names one entry of the fixed list below (default "analytics"); it is never a path or a module name taken from a request, and each worker is a
plain static import placed after the limits.
Each limit is applied independently; a refusal (for example RLIMIT_AS on macOS) is ignored here because the parent's watchdog
and `describe_memory()` already account for it truthfully."""
import sys

WORKERS = ("analytics", "predictive")


def _limit(name: str, value: int) -> None:
    try:
        import resource

        resource.setrlimit(getattr(resource, name), (value, value))
    except Exception:  # noqa: BLE001  (ImportError on non-POSIX, ValueError/OSError when the platform refuses)
        pass


def main(argv: list) -> None:
    work, root, cpu_s, memory_mb, kernel_as = argv[1], argv[2], int(argv[3]), int(argv[4]), argv[5] == "1"
    which = argv[6] if len(argv) > 6 else "analytics"
    file_mb = int(argv[7]) if len(argv) > 7 else 64
    if which not in WORKERS:
        sys.exit(2)
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 2))
    except Exception:  # noqa: BLE001
        pass
    if kernel_as:                       # only where the parent's probe proved the kernel really refuses allocations (never assumed)
        _limit("RLIMIT_AS", memory_mb * 1024 * 1024)
    _limit("RLIMIT_FSIZE", file_mb * 1024 * 1024)
    _limit("RLIMIT_NOFILE", 128)
    _limit("RLIMIT_CORE", 0)
    sys.path.insert(0, root)
    if which == "predictive":
        from app.modules.predictive import worker
    else:
        from app.modules.analytics import worker

    worker.main(work)


if __name__ == "__main__":
    main(sys.argv)
