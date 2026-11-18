"""Private object storage behind a small interface. Only a local-disk backend exists; a cloud backend
can implement the same protocol later without touching callers (no paid cloud dependency today)."""
from .base import StorageBackend, StorageError, StoredObject, valid_key
from .local import LocalStorage

__all__ = ["StorageBackend", "StorageError", "StoredObject", "LocalStorage", "valid_key"]
