"""Profiling: descriptive facts about one version of a dataset or document. No predictive modelling.

Every number is labelled as a FACT (computed exactly from the stored data), a HEURISTIC (a rule of thumb, with the rule named)
or an ESTIMATE. A profile records the source version, its checksum, the profiler version and the time it was made. The
`content_hash` covers everything except the timestamp, so re-profiling unchanged data must give the same hash (reproducible)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from ..explanation import explain
from ..identity.authorization import Principal
from ..identity.context import Context
from ..identity.tokens import new_id
from ..storage import StorageBackend, StorageError
from . import keys
from .assets import AssetService
from .constants import AssetKind, AssetStatus, WEvent
from .errors import conflict, invalid, not_found
from .extraction import DataViews
from .parsing.coltypes import classify, is_missing, parse_date, to_float
from .projects import ProjectService
from .repository import WorkspaceRepository

PROFILER_VERSION = "1"
SCHEMA = "predictai.profile/1"
DISTINCT_CAP = 100_000
TOP_VALUES = 5
BINS = 10
csv.field_size_limit(16 * 1024 * 1024)

BASIS = {
    "fact": "Computed exactly from every stored value.",
    "heuristic": "A rule of thumb; the rule is named next to the result. It can be wrong for your data.",
    "estimate": "An approximation, not an exact count.",
}


def _round(x: Optional[float], places: int = 6) -> Optional[float]:
    if x is None or not math.isfinite(x):
        return None
    return round(x, places)


def _percentile(sorted_values: List[float], q: float) -> Optional[float]:
    """Linear interpolation between closest ranks (the common 'type 7' definition)."""
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


class _Column:
    def __init__(self, name: str, declared_type: str) -> None:
        self.name, self.declared_type = name, declared_type
        self.count = self.missing = 0
        self.distinct: Counter = Counter()
        self.distinct_capped = False
        self.numbers: List[float] = []
        self.dates: List[Any] = []
        self.min_len: Optional[int] = None
        self.max_len = 0
        self.total_len = 0
        self.padded = 0
        self.kinds: Counter = Counter()

    def add(self, value: str) -> None:
        self.count += 1
        if is_missing(value):
            self.missing += 1
            return
        kind = classify(value)
        self.kinds[kind] += 1
        n = len(value)
        self.min_len = n if self.min_len is None else min(self.min_len, n)
        self.max_len = max(self.max_len, n)
        self.total_len += n
        if value != value.strip():
            self.padded += 1
        if value in self.distinct or len(self.distinct) < DISTINCT_CAP:
            self.distinct[value] += 1
        else:
            self.distinct_capped = True
        if kind in ("integer", "float"):
            number = to_float(value)
            if number is not None:
                self.numbers.append(number)
        elif kind in ("date", "datetime"):
            parsed = parse_date(value)
            if parsed is not None:
                self.dates.append(parsed)

    def result(self, rows: int) -> Dict[str, Any]:
        present = self.count - self.missing
        out: Dict[str, Any] = {
            "name": self.name,
            "type": {"value": self.declared_type, "basis": "heuristic", "rule": "chosen when at least 90% of non-empty values fit"},
            "count": self.count, "missing": self.missing, "missing_pct": _round(100 * self.missing / rows, 2) if rows else 0.0,
            "distinct": {"value": len(self.distinct), "exact": not self.distinct_capped, "basis": "fact" if not self.distinct_capped else "estimate"},
            "value_kinds": dict(sorted(self.kinds.items())),
            "top_values": [{"value": v[:200], "count": c} for v, c in sorted(self.distinct.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_VALUES]],
            "flags": [],
        }
        flags = out["flags"]
        if present and not self.distinct_capped:
            if len(self.distinct) == 1:
                flags.append({"code": "CONSTANT", "basis": "fact", "message": "Every non-empty value is the same."})
            if len(self.distinct) == present and present > 1:
                flags.append({"code": "ALL_UNIQUE", "basis": "fact", "message": "Every non-empty value is different."})
                if self.declared_type in ("integer", "text"):
                    flags.append({"code": "LIKELY_IDENTIFIER", "basis": "heuristic", "message": "All values are unique, so this may be an identifier (rule: unique count equals non-empty count)."})
        if self.missing and rows and self.missing / rows >= 0.5:
            flags.append({"code": "MOSTLY_EMPTY", "basis": "fact", "message": f"{round(100 * self.missing / rows)}% of the cells are empty."})
        if self.padded:
            flags.append({"code": "EXTRA_SPACES", "basis": "fact", "message": f"{self.padded:,} value(s) start or end with spaces."})
        if self.numbers:
            values = sorted(self.numbers)
            n = len(values)
            mean = sum(values) / n
            var = sum((x - mean) ** 2 for x in values) / (n - 1) if n > 1 else None
            q1, q3 = _percentile(values, 0.25), _percentile(values, 0.75)
            iqr = q3 - q1
            low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outliers = sum(1 for x in values if x < low or x > high) if iqr > 0 else 0
            lo, hi = values[0], values[-1]
            hist: List[Dict[str, Any]] = []
            if hi > lo:
                width = (hi - lo) / BINS
                counts = [0] * BINS
                for x in values:
                    counts[min(int((x - lo) / width), BINS - 1)] += 1
                hist = [{"from": _round(lo + i * width), "to": _round(lo + (i + 1) * width), "count": c} for i, c in enumerate(counts)]
            else:
                hist = [{"from": _round(lo), "to": _round(hi), "count": n}]
            out["numeric"] = {
                "count": n, "min": _round(lo), "max": _round(hi), "mean": _round(mean), "median": _round(_percentile(values, 0.5)),
                "q1": _round(q1), "q3": _round(q3), "std": _round(math.sqrt(var)) if var is not None else None, "sum": _round(sum(values)),
                "histogram": {"bins": hist, "basis": "fact", "note": f"{BINS} equal-width bins"},
                "outliers": {"count": outliers, "basis": "heuristic", "rule": "outside 1.5 × IQR beyond the quartiles; an outlier is not necessarily an error"},
                "basis": "fact", "std_definition": "sample standard deviation",
            }
            if outliers:
                flags.append({"code": "POSSIBLE_OUTLIERS", "basis": "heuristic", "message": f"{outliers:,} value(s) fall outside 1.5 × IQR (a rule of thumb, not proof of error)."})
        if present:
            out["text_length"] = {"min": self.min_len, "max": self.max_len, "mean": _round(self.total_len / present, 2), "basis": "fact"}
        if self.dates:
            out["dates"] = {"count": len(self.dates), "min": min(self.dates).isoformat(sep=" "), "max": max(self.dates).isoformat(sep=" "), "basis": "fact"}
        return out


def profile_table(raw: Any, declared: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Profile a canonical CSV byte stream. `declared` is the extraction's column list (for the heuristic types)."""
    text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
    try:
        return _profile_rows(csv.reader(text), declared)
    finally:
        text.detach()  # the caller owns (and closes) the underlying stream


def _profile_rows(reader: Any, declared: List[Dict[str, Any]]) -> Dict[str, Any]:
    header = next(reader, [])
    types = {c["name"]: c["type"] for c in declared}
    cols = [_Column(name, types.get(name, "text")) for name in header]
    seen: set = set()
    duplicates = rows = complete = 0
    for row in reader:
        rows += 1
        digest = hashlib.sha1("\x1f".join(row).encode("utf-8", "replace")).digest()
        if digest in seen:
            duplicates += 1
        else:
            seen.add(digest)
        full = True
        for i, col in enumerate(cols):
            value = row[i] if i < len(row) else ""
            col.add(value)
            if is_missing(value):
                full = False
        complete += full
    missing_cells = sum(c.missing for c in cols)
    total_cells = rows * len(cols)
    return {
        "dataset": {
            "rows": rows, "columns": len(cols), "duplicate_rows": duplicates, "complete_rows": complete,
            "missing_cells": missing_cells, "missing_pct": _round(100 * missing_cells / total_cells, 2) if total_cells else 0.0,
            "basis": "fact", "duplicate_rule": "rows identical in every column",
        },
        "columns": [c.result(rows) for c in cols],
    }


def profile_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    pages = doc["pages"]
    words = [len(p["text"].split()) for p in pages]
    total_words = sum(words)
    levels = Counter(h["level"] for h in doc["headings"])
    confs = [p["ocr_confidence"] for p in pages if p.get("ocr_confidence") is not None]
    methods = Counter(p["method"] for p in pages)
    return {
        "document": {
            "pages": {"value": doc["page_count"], "basis": "estimate" if doc["page_count_estimated"] else "fact"},
            "characters": sum(len(p["text"]) for p in pages), "words": {"value": total_words, "basis": "fact", "rule": "runs of non-space characters"},
            "words_per_page": _round(total_words / len(pages), 1) if pages else 0, "pages_by_method": dict(sorted(methods.items())),
            "empty_pages": [p["number"] for p in pages if not p["text"].strip()],
            "headings": {"count": len(doc["headings"]), "by_level": {str(k): v for k, v in sorted(levels.items())},
                         "basis": "heuristic" if any(h.get("heuristic") for h in doc["headings"]) else "fact"},
            "tables": {"count": len(doc["tables"]), "basis": "heuristic" if doc["format"] == "pdf" else "fact"},
            "ocr": {"pages": len(confs), "mean_confidence": _round(sum(confs) / len(confs), 1) if confs else None,
                    "min_confidence": _round(min(confs), 1) if confs else None, "basis": "estimate",
                    "note": "OCR confidence is the engine's own estimate, not a measured accuracy."} if confs else None,
            "reading_time_minutes": {"value": max(1, round(total_words / 230)) if total_words else 0, "basis": "estimate", "rule": "230 words per minute"},
        },
        "flags": ([{"code": "EMPTY_PAGES", "basis": "fact", "message": f"{len([p for p in pages if not p['text'].strip()])} page(s) have no readable text."}]
                  if any(not p["text"].strip() for p in pages) else []),
    }


def content_hash(body: Dict[str, Any]) -> str:
    stable = {k: v for k, v in body.items() if k != "generated_at"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class ProfileService:
    def __init__(self, ctx: Context, repo: WorkspaceRepository, storage: StorageBackend, assets: AssetService, views: DataViews, projects: ProjectService) -> None:
        self.ctx, self.repo, self.storage, self.assets, self.views, self.projects = ctx, repo, storage, assets, views, projects

    def _version(self, principal: Principal, asset, version_id: Optional[str]):
        return self.views._version(principal, asset, version_id)

    def get(self, principal: Principal, asset_id: str, version_id: Optional[str] = None) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id)
        version = self._version(principal, asset, version_id)
        row = self.repo.latest_profile(principal.user_id, version["id"])
        if row is None:
            return {"profile": None, "version_id": version["id"], "can_generate": asset["status"] == AssetStatus.READY}
        return self._present(row, asset, version)

    def generate(self, principal: Principal, asset_id: str, *, version_id: Optional[str] = None, refresh: bool = False) -> Dict[str, Any]:
        asset = self.assets.get_owned(principal, asset_id, writable=True)
        if asset["status"] != AssetStatus.READY:
            raise conflict("ASSET_NOT_READY", "This file could not be read, so it cannot be profiled.")
        version = self._version(principal, asset, version_id)
        existing = self.repo.latest_profile(principal.user_id, version["id"])
        if existing is not None and not refresh:
            return self._present(existing, asset, version)
        self.ctx.limiter.hit("workspace_write:user", principal.user_id)
        extraction = self.repo.extraction_for_version(principal.user_id, version["id"])
        if extraction is None:
            raise not_found("extraction")
        try:
            if asset["kind"] == AssetKind.DATASET:
                if not version["canonical_key"]:
                    raise not_found("data")
                declared = json.loads(extraction["summary"] or "{}").get("columns", [])
                with self.storage.open(version["canonical_key"]) as raw:
                    body = profile_table(raw, declared)
            else:
                body = profile_document(json.loads(self.storage.read_bytes(extraction["text_key"]).decode("utf-8")))
        except StorageError as error:
            raise not_found("data") from error
        now = self.ctx.clock.iso()
        body.update({
            "schema": SCHEMA, "kind": asset["kind"], "profiler_version": PROFILER_VERSION, "generated_at": now,
            "source": {"asset_id": asset["id"], "version_id": version["id"], "version_number": version["version_number"],
                       "checksum_sha256": version["checksum_sha256"], "format": version["format"]},
            "basis_legend": BASIS,
        })
        digest = content_hash(body)
        body["content_hash"] = digest
        profile_id = new_id()
        key = keys.profile_key(asset["project_id"], asset["id"], profile_id)
        self.storage.put(key, [json.dumps(body, ensure_ascii=False).encode("utf-8")], overwrite=True)
        summary = self._summary(body)
        summary["content_hash"] = digest
        self.repo.create_profile(id=profile_id, asset_id=asset["id"], version_id=version["id"], project_id=asset["project_id"], owner=principal.user_id,
                                 profiler_version=PROFILER_VERSION, checksum=version["checksum_sha256"], result_key=key, summary=json.dumps(summary), now=now)
        self.projects.record(asset["project_id"], principal, WEvent.ASSET_PROFILED, asset["id"], {"version_id": version["id"]})
        return self._present(self.repo.latest_profile(principal.user_id, version["id"]), asset, version)

    @staticmethod
    def _summary(body: Dict[str, Any]) -> Dict[str, Any]:
        if body["kind"] == AssetKind.DATASET:
            d = body["dataset"]
            return {k: d[k] for k in ("rows", "columns", "duplicate_rows", "missing_cells", "missing_pct")}
        d = body["document"]
        return {"pages": d["pages"]["value"], "words": d["words"]["value"]}

    def _present(self, row, asset, version) -> Dict[str, Any]:
        try:
            body = json.loads(self.storage.read_bytes(row["result_key"]).decode("utf-8"))
        except (StorageError, ValueError) as error:
            raise not_found("profile") from error
        return {"profile": body, "explanation": explain(f"profile:{body.get('kind', 'dataset')}", body), "id": row["id"], "version_id": version["id"], "generated_at": row["generated_at"], "profiler_version": row["profiler_version"],
                "source_checksum": row["source_checksum"],
                "stale": row["source_checksum"] != version["checksum_sha256"] or row["profiler_version"] != PROFILER_VERSION,
                "can_generate": True}
