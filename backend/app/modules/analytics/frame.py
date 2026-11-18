"""Dataset snapshot -> typed DataFrame, plus filters. The row index of the frame is the 1-based data-row number in the stored
canonical table of that dataset version, so any result can be traced back to rows (drill-down) and a snapshot is
(asset, version, checksum, filters). Value parsing reuses the Phase 3 helpers so "missing" and "numeric" mean one thing."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ..workspace.parsing.coltypes import FALSE_TOKENS, TRUE_TOKENS, is_missing, parse_date, to_float
from .errors import AnalysisError, too_large
from .plan import DATETIME_TYPES, NUMERIC_TYPES, Filter, role_of

csv.field_size_limit(16 * 1024 * 1024)


@dataclass
class Snapshot:
    df: pd.DataFrame
    types: Dict[str, str]
    invalid: Dict[str, int]
    rows_total: int                       # rows in the stored table
    sampled: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    invalid_rows: Dict[str, List[int]] = field(default_factory=dict)   # row numbers whose non-missing value did not fit the column type (capped)

    @property
    def rows_loaded(self) -> int:
        return int(len(self.df))


def _parse_column(values: Sequence[str], type_name: str):
    """Returns (series values, indices of values that are present but do not fit the type)."""
    bad_idx: List[int] = []
    if type_name in NUMERIC_TYPES:
        out = np.full(len(values), np.nan)
        for i, v in enumerate(values):
            if is_missing(v):
                continue
            number = to_float(v)
            if number is None:
                bad_idx.append(i)
            else:
                out[i] = number
        return out, bad_idx
    if type_name in DATETIME_TYPES:
        parsed: List[Any] = []
        for i, v in enumerate(values):
            if is_missing(v):
                parsed.append(None)
                continue
            d = parse_date(v)
            if d is None:
                bad_idx.append(i)
            parsed.append(d)
        return pd.to_datetime(pd.Series(parsed, dtype="object"), errors="coerce").to_numpy(), bad_idx
    if type_name == "boolean":
        out_b: List[Optional[str]] = []
        for i, v in enumerate(values):
            low = (v or "").strip().lower()
            if is_missing(v):
                out_b.append(None)
            elif low in TRUE_TOKENS:
                out_b.append("true")
            elif low in FALSE_TOKENS:
                out_b.append("false")
            else:
                bad_idx.append(i)
                out_b.append(None)
        return out_b, bad_idx
    return [None if is_missing(v) else v.strip() for v in values], bad_idx


def load_snapshot(stream: BinaryIO, declared: Sequence[Mapping[str, Any]], *, rows_total: int, max_rows: int, max_cells: int,
                  sample_rows: Optional[int] = None, seed: int = 0) -> Snapshot:
    """Read the canonical CSV of one dataset version. Refuses (never silently truncates) a table above the limits; a deterministic,
    seeded row sample is used only when the plan asked for one and is recorded."""
    n_cols = max(1, len(declared))
    sampled = None
    wanted = None
    if sample_rows is not None and sample_rows < rows_total:
        if sample_rows > max_rows:
            raise too_large("The requested sample is larger than your analysis limit.")
        wanted = np.sort(np.random.default_rng(seed).choice(rows_total, size=sample_rows, replace=False))
        sampled = {"method": "uniform without replacement", "requested_rows": sample_rows, "population_rows": rows_total, "seed": seed}
        use_rows = sample_rows
    else:
        use_rows = rows_total
    if use_rows > max_rows:
        raise too_large(f"This dataset has {rows_total:,} rows; your limit for one analysis is {max_rows:,}. Filter it, or ask for a seeded sample of at most {max_rows:,} rows.")
    if use_rows * n_cols > max_cells:
        raise too_large("This dataset has too many cells for one analysis. Choose a sample or split the data.")
    text = io.TextIOWrapper(stream, encoding="utf-8", newline="")
    try:
        reader = csv.reader(text)
        header = next(reader, [])
        cols: List[List[str]] = [[] for _ in header]
        numbers: List[int] = []
        wanted_set = iter(wanted.tolist()) if wanted is not None else None
        nxt = next(wanted_set, None) if wanted_set is not None else None
        for index, row in enumerate(reader):
            if wanted is not None:
                if nxt is None:
                    break
                if index != nxt:
                    continue
                nxt = next(wanted_set, None)
            numbers.append(index + 1)
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            for j in range(len(header)):
                cols[j].append(row[j])
    finally:
        text.detach()
    by_name = {d["name"]: d for d in declared}
    data: Dict[str, Any] = {}
    types: Dict[str, str] = {}
    invalid: Dict[str, int] = {}
    invalid_rows: Dict[str, List[int]] = {}
    warnings: List[str] = []
    for name, values in zip(header, cols):
        type_name = (by_name.get(name) or {}).get("type") or "text"
        parsed, bad = _parse_column(values, type_name)
        data[name] = parsed
        types[name] = type_name
        invalid[name] = len(bad)
        if bad:
            invalid_rows[name] = [numbers[i] for i in bad[:5000]]
        if type_name == "mixed":
            warnings.append("A column with mixed value types was treated as text.")
    df = pd.DataFrame(data, index=pd.Index(numbers, name="row"))
    for name, type_name in types.items():
        if role_of(type_name) == "categorical":
            df[name] = df[name].astype("object").where(df[name].notna(), None)
    return Snapshot(df=df, types=types, invalid=invalid, rows_total=rows_total, sampled=sampled, warnings=sorted(set(warnings)), invalid_rows=invalid_rows)


# ------------------------------------------------------------------------------------------------ filters
def _num(value: Any, column: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AnalysisError("FILTER_INVALID", f"The filter on '{column[:60]}' needs a number.")
    if number != number or number in (float("inf"), float("-inf")):
        raise AnalysisError("FILTER_INVALID", f"The filter on '{column[:60]}' needs a finite number.")
    return number


def _when(value: Any, column: str) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(str(value))
    except (ValueError, TypeError):
        raise AnalysisError("FILTER_INVALID", f"The filter on '{column[:60]}' needs a date such as 2024-03-31.")
    if pd.isna(stamp):
        raise AnalysisError("FILTER_INVALID", f"The filter on '{column[:60]}' needs a date such as 2024-03-31.")
    return stamp.tz_localize(None) if stamp.tzinfo else stamp


def filter_mask(df: pd.DataFrame, types: Mapping[str, str], flt: Filter) -> pd.Series:
    """Rows for which the filter holds. Comparisons never match rows where the column is missing (use is_null for those)."""
    column, op = flt.column, flt.op
    if column not in df.columns:
        raise AnalysisError("FILTER_INVALID", "A filter refers to a column that is not in this dataset.")
    series = df[column]
    role = role_of(types[column])
    if op == "is_null":
        return series.isna()
    if op == "not_null":
        return series.notna()
    if role == "numeric":
        conv = lambda v: _num(v, column)  # noqa: E731
    elif role == "datetime":
        conv = lambda v: _when(v, column)  # noqa: E731
    else:
        conv = lambda v: str(v).strip()  # noqa: E731
        if types[column] == "boolean":
            conv = lambda v: str(v).strip().lower()  # noqa: E731
    present = series.notna()
    if op == "between":
        lo, hi = conv(flt.value[0]), conv(flt.value[1])
        if lo > hi:
            lo, hi = hi, lo
        return present & (series >= lo) & (series <= hi)
    if op in ("in", "not_in"):
        values = [conv(v) for v in flt.value]
        hit = series.isin(values)
        return present & (hit if op == "in" else ~hit)
    if op == "contains":
        if role != "categorical":
            raise AnalysisError("FILTER_INVALID", f"'contains' works on text columns; '{column[:60]}' is not one.")
        needle = str(flt.value).lower()
        return present & series.astype("object").map(lambda s: isinstance(s, str) and needle in s.lower())
    value = conv(flt.value)
    if role == "categorical" and op not in ("eq", "ne"):
        raise AnalysisError("FILTER_INVALID", f"'{op}' needs a numeric or date column; '{column[:60]}' is neither.")
    if op == "eq":
        return present & (series == value)
    if op == "ne":
        return present & (series != value)
    if op == "gt":
        return present & (series > value)
    if op == "ge":
        return present & (series >= value)
    if op == "lt":
        return present & (series < value)
    if op == "le":
        return present & (series <= value)
    raise AnalysisError("FILTER_INVALID", "Unknown filter operator.")


def apply_filters(df: pd.DataFrame, types: Mapping[str, str], filters: Sequence[Filter]) -> pd.DataFrame:
    if not filters:
        return df
    mask = pd.Series(True, index=df.index)
    for flt in filters:
        mask &= filter_mask(df, types, flt)
    return df[mask]
