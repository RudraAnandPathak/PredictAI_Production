"""Local-disk storage. Writes are atomic (temp file + rename); originals are written once and made read-only;
every resolved path is checked to stay inside the storage root (no traversal, no symlink escape)."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO, Iterable, List, Optional

from .base import StorageError, StoredObject, valid_key


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()  # directories are created on first write, so merely importing the app touches nothing
        self._tmp = self.root / ".tmp"

    # ------------------------------------------------------------------ helpers
    def _path(self, key: str) -> Path:
        if not valid_key(key):
            raise StorageError("invalid storage key")
        path = (self.root / key).resolve()
        if self.root != path and self.root not in path.parents:
            raise StorageError("storage key escapes the storage root")
        return path

    def local_path(self, key: str) -> Path:
        return self._path(key)

    # ------------------------------------------------------------------ operations
    def put(self, key: str, chunks: Iterable[bytes], *, overwrite: bool = False, max_bytes: Optional[int] = None) -> StoredObject:
        target = self._path(key)
        if target.exists() and not overwrite:
            raise StorageError("object already exists")
        self._tmp.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        fd, temp_name = tempfile.mkstemp(dir=self._tmp)
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in chunks:
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise StorageError("object exceeds the allowed size")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists() and overwrite:
                os.chmod(target, 0o600)
            os.replace(temp_name, target)
            os.chmod(target, 0o440 if not overwrite else 0o640)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return StoredObject(key, size, digest.hexdigest())

    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        try:
            return open(path, "rb")
        except OSError as error:
            raise StorageError("object not found") from error

    def read_bytes(self, key: str, limit: Optional[int] = None) -> bytes:
        with self.open(key) as handle:
            return handle.read() if limit is None else handle.read(limit)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        try:
            return self._path(key).stat().st_size
        except OSError as error:
            raise StorageError("object not found") from error

    def modified_at(self, key: str) -> float:
        """Seconds since the epoch when the object was last written (used to sweep abandoned objects safely)."""
        try:
            return self._path(key).stat().st_mtime
        except OSError as error:
            raise StorageError("object not found") from error

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        os.chmod(path, 0o600)
        path.unlink()
        self._prune(path.parent)
        return True

    def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix.rstrip("/"))
        if not base.exists():
            return 0
        count = sum(1 for p in base.rglob("*") if p.is_file())
        for p in base.rglob("*"):
            try:
                os.chmod(p, 0o700 if p.is_dir() else 0o600)
            except OSError:
                pass
        shutil.rmtree(base, ignore_errors=False)
        self._prune(base.parent)
        return count

    def list_prefix(self, prefix: str) -> List[str]:
        base = self._path(prefix.rstrip("/"))
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace(os.sep, "/") for p in base.rglob("*") if p.is_file())

    def _prune(self, directory: Path) -> None:
        while directory != self.root and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            directory = directory.parent
