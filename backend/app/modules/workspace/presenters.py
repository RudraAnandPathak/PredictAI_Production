"""Shapes workspace rows into API payloads. Only fields meant for clients are listed; storage keys never leave the server."""
from __future__ import annotations

from typing import Any, Dict


def present_asset(row, versions: int = 0) -> Dict[str, Any]:
    return {
        "id": row["id"], "project_id": row["project_id"], "kind": row["kind"], "name": row["name"], "format": row["detected_format"],
        "status": row["status"], "status_code": row["status_code"], "status_detail": row["status_detail"],
        "size_bytes": row["size_bytes"], "checksum_sha256": row["source_checksum"],
        "page_count": row["page_count"], "page_count_estimated": bool(row["page_count_estimated"]),
        "row_count": row["row_count"], "column_count": row["column_count"],
        "current_version_id": row["current_version_id"], "versions": versions,
        "retention_class": row["retention_class"], "retain_until": row["retain_until"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def present_upload(row, received) -> Dict[str, Any]:
    return {
        "id": row["id"], "project_id": row["project_id"], "filename": row["filename"], "size_bytes": row["declared_size"],
        "chunk_size": row["chunk_size"], "total_chunks": row["total_chunks"], "received_chunks": sorted(received),
        "received_bytes": row["received_bytes"], "status": row["status"], "error_code": row["error_code"],
        "asset_id": row["asset_id"], "expires_at": row["expires_at"],
    }
