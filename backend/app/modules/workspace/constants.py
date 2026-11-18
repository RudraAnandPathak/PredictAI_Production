"""Workspace vocabulary: statuses, formats, retention classes, limits and audit event names."""
from __future__ import annotations

from typing import Dict, FrozenSet


class ProjectStatus:
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETING = "deleting"
    DELETED = "deleted"


class AssetKind:
    DATASET = "dataset"
    DOCUMENT = "document"


class AssetStatus:
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class VersionKind:
    ORIGINAL = "original"
    DERIVED = "derived"


class Retention:
    SOURCE = "source"        # the user's original upload: kept until the user deletes it
    DERIVED = "derived"      # cleaned versions
    EXTRACTED = "extracted"  # text / profile / canonical table generated from a source
    TEMPORARY = "temporary"  # upload chunks and unconfirmed previews: short-lived


# format id -> (kind, accepted extensions)
FORMATS: Dict[str, tuple] = {
    "csv": (AssetKind.DATASET, ("csv", "tsv")),
    "xlsx": (AssetKind.DATASET, ("xlsx",)),
    "xls": (AssetKind.DATASET, ("xls",)),
    "ods": (AssetKind.DATASET, ("ods",)),
    "json": (AssetKind.DATASET, ("json",)),
    "xml": (AssetKind.DATASET, ("xml",)),
    "pdf": (AssetKind.DOCUMENT, ("pdf",)),
    "docx": (AssetKind.DOCUMENT, ("docx",)),
    "rtf": (AssetKind.DOCUMENT, ("rtf",)),
    "txt": (AssetKind.DOCUMENT, ("txt",)),
    "md": (AssetKind.DOCUMENT, ("md", "markdown")),
}
EXTENSION_TO_FORMAT: Dict[str, str] = {ext: fmt for fmt, (_, exts) in FORMATS.items() for ext in exts}
SUPPORTED_EXTENSIONS: FrozenSet[str] = frozenset(EXTENSION_TO_FORMAT)

MAX_PROJECTS_PER_USER = 200
MAX_ASSETS_PER_PROJECT = 500
MAX_NAME = 120
MAX_DESCRIPTION = 2000
MAX_FILENAME = 180
MAX_VERSIONS_PER_ASSET = 50


class WEvent:
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_ARCHIVED = "project.archived"
    PROJECT_RESTORED = "project.restored"
    PROJECT_DELETED = "project.deleted"
    UPLOAD_STARTED = "upload.started"
    UPLOAD_CANCELLED = "upload.cancelled"
    UPLOAD_REJECTED = "upload.rejected"
    ASSET_CREATED = "asset.created"
    ASSET_DUPLICATE = "asset.duplicate"
    ASSET_FAILED = "asset.failed"
    ASSET_DELETED = "asset.deleted"
    ASSET_DOWNLOADED = "asset.downloaded"
    ASSET_PROFILED = "asset.profiled"
    ASSET_RETENTION_SET = "asset.retention_set"
    TRANSFORM_PREVIEWED = "transformation.previewed"
    TRANSFORM_APPLIED = "transformation.applied"
    TRANSFORM_DISCARDED = "transformation.discarded"
    ASSET_ROLLED_BACK = "asset.rolled_back"
    QUOTA_DENIED = "quota.denied"
    RETENTION_PURGED = "retention.purged"
    PURGE_FAILED = "purge.failed"

STALE_PROCESSING_MINUTES = 30        # an upload still 'processing' after this long was interrupted (crash/restart)
ORPHAN_MIN_AGE_SECONDS = 24 * 3600   # stored objects with no database row are only swept once this old
MAX_RETENTION_DAYS = 3650            # retention dates are capped at ten years
