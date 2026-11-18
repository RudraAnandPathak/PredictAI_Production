#!/usr/bin/env python3
"""Build a clean release archive of the project. Local configuration and runtime data are never included, and nothing is deleted or
modified in the working tree (your .env stays exactly where it is).

    python scripts/package_release.py [OUTPUT.zip]        # default: ../PredictAI_release.zip

Excluded: .env and .env.* (except the *.example templates), data/, databases, email files, keys and certificates, virtual
environments, node_modules, build output and caches, version-control metadata."""
from __future__ import annotations

import fnmatch
import sys
import zipfile
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", "data", "dist", ".venv", "venv", ".vite", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode"}
EXCLUDED_FILES = (".env", ".env.*", "*.db", "*.db-journal", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3", "*.eml", "*.pyc", "*.pyo", "*.pem", "*.key", "*.p12", "*.pfx",
                  "dev-secret-key", ".DS_Store", "Thumbs.db", "*.log", "*.zip", "*.egg-info")
ALLOWED_TEMPLATES = (".env.example", "env.example", "env.production.example")


def is_excluded(relative: Path) -> bool:
    parts = relative.parts
    if any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in ALLOWED_TEMPLATES or name.endswith(".example"):
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_FILES)


def release_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and not is_excluded(path.relative_to(root)):
            yield path


def build_archive(root: Path, output: Path, *, top_level: str = "PredictAI") -> int:
    """Write the archive (output must be outside `root` or is skipped automatically). Returns the number of files written."""
    root, output = Path(root).resolve(), Path(output).resolve()
    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in release_files(root):
            if path.resolve() == output:
                continue
            archive.write(path, f"{top_level}/{path.relative_to(root).as_posix()}")
            count += 1
    return count


def main(argv: Iterable[str]) -> int:
    args = list(argv)
    output = Path(args[0]) if args else ROOT.parent / "PredictAI_release.zip"
    count = build_archive(ROOT, output)
    print(f"Wrote {output} ({count} files). Local .env and runtime data were not included and were not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
