"""Shared building blocks for the operations. Every number the platform reports is computed in this package."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from ..errors import AnalysisError, AnalysisTimeout
from ..plan import role_of


@dataclass
class OpContext:
    df: pd.DataFrame
    types: Dict[str, str]
    invalid: Dict[str, int]
    invalid_rows: Dict[str, List[int]]
    seed: int
    null_policy: str
    rows_total: int
    deadline: Optional[float] = None
    prior_notes: List[str] = field(default_factory=list)

    def check(self) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise AnalysisTimeout()

    def rng(self, salt: int = 0) -> np.random.Generator:
        return np.random.default_rng([self.seed, salt])


def num(x: Any, digits: int = 10) -> Optional[float]:
    """A JSON-safe, rounded float (None for NaN/inf) so repeated runs hash identically."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    if f == 0:
        return 0.0
    return float(f"{f:.{digits}g}")


def py(value: Any) -> Any:
    """Recursively convert numpy/pandas scalars to plain Python for JSON."""
    if isinstance(value, dict):
        return {str(k): py(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [py(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return num(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.datetime64):
        return pd.Timestamp(value).isoformat()
    if value is pd.NaT:
        return None
    if isinstance(value, np.ndarray):
        return py(value.tolist())
    return value


def is_numeric(ctx: OpContext, column: str) -> bool:
    return role_of(ctx.types[column]) == "numeric"


def numeric(ctx: OpContext, column: str) -> pd.Series:
    return ctx.df[column].astype(float)


def category(ctx: OpContext, column: str) -> pd.Series:
    """A column as category labels (integers rendered without '.0'); missing stays missing."""
    series = ctx.df[column]
    if role_of(ctx.types[column]) == "numeric":
        if ctx.types[column] == "integer":
            return series.map(lambda v: None if pd.isna(v) else str(int(v)))
        return series.map(lambda v: None if pd.isna(v) else repr(float(v)))
    if role_of(ctx.types[column]) == "datetime":
        return series.map(lambda v: None if pd.isna(v) else pd.Timestamp(v).date().isoformat())
    return series


def pair_frame(ctx: OpContext, columns: Sequence[str]) -> pd.DataFrame:
    """Rows where every listed column is present (complete cases) — what a pairwise statistic on those columns uses."""
    return ctx.df[list(columns)].dropna()


def evidence_for(ctx: OpContext, *, columns: Sequence[str], n: int, method: str, formula: str, uncertainty: Optional[Dict[str, Any]] = None,
                 assumptions: Optional[List[Dict[str, Any]]] = None, limitations: Optional[List[str]] = None, practical: Optional[str] = None,
                 null_policy: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ev = {
        "columns": list(columns), "sample_size": int(n), "rows_in_scope": int(len(ctx.df)),
        "null_policy": null_policy or f"{ctx.null_policy}: rows missing any value used by this analysis are left out of it",
        "method": method, "formula_ref": formula,
        "uncertainty": uncertainty if uncertainty is not None else {"available": False, "reason": "This result is an exact count or summary of the rows in scope, not an estimate."},
        "assumptions": assumptions or [], "limitations": limitations or [], "practical_relevance": practical,
    }
    if extra:
        ev.update(extra)
    return py(ev)


def result(status: str, data: Any, evidence: Dict[str, Any], warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"status": status, "data": py(data), "evidence": evidence, "warnings": list(warnings or [])}


def not_enough(ctx: OpContext, columns: Sequence[str], n: int, need: int, what: str, method: str = "n/a") -> Dict[str, Any]:
    from ..constants import NOT_ENOUGH_EVIDENCE

    return result("not_run", {"message": NOT_ENOUGH_EVIDENCE, "reason": f"{what} needs at least {need} complete observations; there are {n}."},
                  evidence_for(ctx, columns=columns, n=n, method=method, formula="not run",
                               limitations=[f"Fewer than {need} observations: no estimate is reported rather than a misleading one."]),
                  [f"{what}: too few observations ({n})."])


# ------------------------------------------------------------------------------------------------ statistics helpers
def fisher_ci(r: float, n: int, conf: float = 0.95, se_scale: float = 1.0) -> Optional[Tuple[float, float]]:
    if n <= 3 or abs(r) >= 1:
        return None
    z = np.arctanh(r)
    se = se_scale / math.sqrt(n - 3)
    crit = stats.norm.ppf(0.5 + conf / 2)
    return float(np.tanh(z - crit * se)), float(np.tanh(z + crit * se))


def magnitude(effect: float, scale: str = "r") -> str:
    """Conventional (Cohen) bands for an absolute correlation-type effect. A convention, not a verdict on importance."""
    a = abs(effect)
    return "negligible" if a < 0.1 else "small" if a < 0.3 else "medium" if a < 0.5 else "large"


def hedges_g(a: np.ndarray, b: np.ndarray, conf: float = 0.95) -> Optional[Dict[str, Any]]:
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    v1, v2 = a.var(ddof=1), b.var(ddof=1)
    sp = math.sqrt(((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2))
    if sp == 0:
        return None
    d = (a.mean() - b.mean()) / sp
    j = 1 - 3 / (4 * (n1 + n2) - 9)
    g = d * j
    se = math.sqrt((n1 + n2) / (n1 * n2) + g * g / (2 * (n1 + n2 - 2)))
    crit = stats.norm.ppf(0.5 + conf / 2)
    return {"g": num(g), "ci": [num(g - crit * se), num(g + crit * se)], "confidence": conf, "ci_method": "normal approximation"}


def benjamini_hochberg(pvalues: Sequence[Optional[float]]) -> List[Optional[float]]:
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    out: List[Optional[float]] = [None] * len(pvalues)
    m = len(idx)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvalues[i])
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, pvalues[i] * m / rank)
        out[i] = num(min(prev, 1.0))
    return out


def holm(pvalues: Sequence[Optional[float]]) -> List[Optional[float]]:
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    out: List[Optional[float]] = [None] * len(pvalues)
    m = len(idx)
    order = sorted(idx, key=lambda i: pvalues[i])
    running = 0.0
    for k, i in enumerate(order):
        running = max(running, (m - k) * pvalues[i])
        out[i] = num(min(running, 1.0))
    return out


def outlier_fences(x: np.ndarray, k: float = 1.5) -> Tuple[float, float, float, float]:
    q1, q3 = np.percentile(x, [25, 75])
    iqr = q3 - q1
    return float(q1 - k * iqr), float(q3 + k * iqr), float(q1), float(q3)


def histogram(values: np.ndarray, bins: Optional[int] = None) -> Dict[str, Any]:
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {"edges": [], "counts": [], "bins": 0}
    if values.min() == values.max():
        return {"edges": [num(values.min()), num(values.max())], "counts": [int(len(values))], "bins": 1, "rule": "all values equal"}
    if bins is None:
        edges = np.histogram_bin_edges(values, bins="fd")
        if len(edges) - 1 > 50 or len(edges) - 1 < 5:
            bins = int(min(50, max(5, len(edges) - 1)))
            edges = np.histogram_bin_edges(values, bins=bins)
        rule = "Freedman-Diaconis, limited to 5-50 bins"
    else:
        edges = np.histogram_bin_edges(values, bins=bins)
        rule = f"{bins} equal-width bins (chosen)"
    counts, edges = np.histogram(values, bins=edges)
    return {"edges": [num(e) for e in edges], "counts": [int(c) for c in counts], "bins": int(len(counts)), "rule": rule,
            "last_bin_includes_upper_edge": True}


def share(part: int, whole: int) -> Optional[float]:
    return num(100.0 * part / whole, 6) if whole else None
