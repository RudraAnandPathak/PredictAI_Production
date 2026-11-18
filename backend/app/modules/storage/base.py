"""Storage interface. Keys are opaque, server-generated strings: never a user-supplied file name."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import BinaryIO, Iterable, Protocol

_KEY = re.compile(r"^[a-z0-9][a-z0-9_.\-]*(/[a-z0-9][a-z0-9_.\-]*)*$")
MAX_KEY = 240


class StorageError(Exception):
    """A storage operation failed or was refused. Messages are for logs, not for users."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    sha256: str


def valid_key(key: str) -> bool:
    return isinstance(key, str) and len(key) <= MAX_KEY and ".." not in key and bool(_KEY.match(key))


class StorageBackend(Protocol):
    def put(self, key: str, chunks: Iterable[bytes], *, overwrite: bool = False, max_bytes: int | None = None) -> StoredObject: ...
    def open(self, key: str) -> BinaryIO: ...
    def read_bytes(self, key: str, limit: int | None = None) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def delete(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def list_prefix(self, prefix: str) -> list: ...
    def modified_at(self, key: str) -> float: ...
    def local_path(self, key: str): ...
