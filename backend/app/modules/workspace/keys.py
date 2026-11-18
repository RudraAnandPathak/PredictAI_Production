"""Opaque storage key layout. Only server-generated hex identifiers appear in keys; user file names never do."""
from __future__ import annotations


def project_prefix(project_id: str) -> str:
    return f"p/{project_id}"


def asset_prefix(project_id: str, asset_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}"


def original_key(project_id: str, asset_id: str, version_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/v/{version_id}/original"


def table_key(project_id: str, asset_id: str, version_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/v/{version_id}/table.csv"


def text_key(project_id: str, asset_id: str, extraction_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/x/{extraction_id}.json"


def profile_key(project_id: str, asset_id: str, profile_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/f/{profile_id}.json"


def upload_prefix(upload_id: str) -> str:
    return f"t/{upload_id}"


def chunk_key(upload_id: str, index: int) -> str:
    return f"t/{upload_id}/{index:06d}"


def preview_key(project_id: str, asset_id: str, transformation_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/pv/{transformation_id}.json"


def analysis_key(project_id: str, asset_id: str, run_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/n/{run_id}.json"


def model_prefix(project_id: str, asset_id: str, model_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/m/{model_id}"


def model_key(project_id: str, asset_id: str, model_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/m/{model_id}/model.joblib"


def model_report_key(project_id: str, asset_id: str, model_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/m/{model_id}/report.json"


def model_background_key(project_id: str, asset_id: str, model_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/m/{model_id}/background.csv"


def prediction_key(project_id: str, asset_id: str, model_id: str, prediction_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/m/{model_id}/pred/{prediction_id}.csv"


# ---------------------------------------------------------------- Phase 6 (all under the project prefix, so deleting a project deletes them)
def doc_index_key(project_id: str, asset_id: str, version_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/i/{version_id}/index.json"


def doc_embedding_key(project_id: str, asset_id: str, version_id: str) -> str:
    return f"p/{project_id}/a/{asset_id}/i/{version_id}/dense.json"


def bundle_key(project_id: str, session_id: str, bundle_id: str) -> str:
    return f"p/{project_id}/r/{session_id}/b/{bundle_id}.json"


def research_prefix(project_id: str, session_id: str) -> str:
    return f"p/{project_id}/r/{session_id}"


def research_snapshot_key(project_id: str, session_id: str, content_hash: str) -> str:
    """A page as it was read, content-addressed inside its session: older bundles keep verifying after the same page changes, and deleting the session removes it."""
    return f"p/{project_id}/r/{session_id}/t/{content_hash}.json"


def web_cache_key(project_id: str, cache_id: str) -> str:
    return f"p/{project_id}/w/{cache_id}.json"


def web_cache_prefix(project_id: str) -> str:
    return f"p/{project_id}/w"


def news_text_key(project_id: str, article_id: str) -> str:
    return f"p/{project_id}/n/{article_id}.json"


def influence_key(project_id: str, analysis_id: str) -> str:
    return f"p/{project_id}/q/{analysis_id}.json"
