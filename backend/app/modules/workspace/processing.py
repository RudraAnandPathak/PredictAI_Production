"""The hook between the upload pipeline and content extraction. The pipeline only needs this narrow contract,
so extraction (Stage 5) can change without touching uploads."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Protocol


class Processor(Protocol):
    def pages_for_quota(self, path: Path, fmt: str) -> Optional[int]:
        """Cheap page count for a document (before anything is stored), or None when pages do not apply."""

    def process(self, principal: Any, asset: Any, version: Any, path: Path) -> Dict[str, Any]:
        """Extract and index the stored original. Returns the asset fields to record. Raises AppError for unreadable files."""


class NullProcessor:
    """Used when no extractor is configured: the file is stored and marked ready, with nothing extracted."""

    def pages_for_quota(self, path: Path, fmt: str) -> Optional[int]:
        return None

    def process(self, principal: Any, asset: Any, version: Any, path: Path) -> Dict[str, Any]:
        return {}
