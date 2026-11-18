"""A daily series from the person's own dataset (a date column and a numeric column), read through the same ownership and size rules as every analysis."""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from ..analytics.executor import limits_for
from ..analytics.frame import load_snapshot
from ..analytics.plan import DATETIME_TYPES, NUMERIC_TYPES
from ..identity.constants import Perm
from ..workspace.errors import invalid

AGGREGATES = ("mean", "sum", "max", "min", "last")


def load_series(ws: Any, principal: Any, asset_id: str, date_column: Any, value_column: Any, aggregate: Any = "mean") -> Dict[str, Any]:
    """{series: {YYYY-MM-DD: value}, dataset: {...}, rows_used, rows_skipped, warnings}. Raises a validation error that names the column when it is unsuitable."""
    if aggregate not in AGGREGATES:
        raise invalid("aggregate", "Choose one of: " + ", ".join(AGGREGATES) + ".")
    asset, version, declared, types = ws.analytics.open_dataset(principal, asset_id)
    for field, col, allowed, what in (("date_column", date_column, DATETIME_TYPES, "a date column"), ("value_column", value_column, NUMERIC_TYPES, "a numeric column")):
        if not isinstance(col, str) or col not in types:
            raise invalid(field, "Choose a column that exists in this dataset.")
        if types[col] not in allowed:
            raise invalid(field, f"'{col}' is not {what}.")
    limits = limits_for(principal.can(Perm.COMPUTE_EXTENDED))
    with ws.storage.open(version["canonical_key"]) as stream:
        snap = load_snapshot(stream, declared, rows_total=int(version["row_count"] or 0), max_rows=int(limits["max_rows"]), max_cells=int(limits["max_cells"]))
    df = snap.df[[date_column, value_column]].copy()
    total = len(df)
    df = df[df[date_column].notna() & df[value_column].notna()]
    df = df[np.isfinite(df[value_column].astype(float))]
    day = pd.to_datetime(df[date_column]).dt.strftime("%Y-%m-%d")
    grouped = df[value_column].astype(float).groupby(day).agg(aggregate)
    warnings = list(snap.warnings)
    if total - len(df):
        warnings.append(f"{total - len(df)} row(s) without a usable date or value were left out.")
    if grouped.empty:
        raise invalid("value_column", "No rows have both a date and a number in these columns.")
    return {"series": {str(k): float(v) for k, v in grouped.items()}, "dataset": {"asset_id": asset["id"], "name": asset["name"], "version_id": version["id"], "checksum_sha256": version["checksum_sha256"],
                                                                                    "date_column": date_column, "value_column": value_column, "aggregate": aggregate},
            "rows_used": int(len(df)), "rows_skipped": int(total - len(df)), "days": int(len(grouped)), "warnings": warnings}
