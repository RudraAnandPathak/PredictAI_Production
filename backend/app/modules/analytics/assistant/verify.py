"""Independent verification of numbers an answer states.

This module recomputes headline figures straight from the stored table using only the standard library (csv, statistics, math) - no
pandas, numpy or scipy and no code shared with the analysis engine except the dataset's own cell-parsing rules. If the engine and this
check disagree, the answer is marked unverified instead of being shown as established."""
from __future__ import annotations

import csv
import math
import statistics
from datetime import datetime
from typing import Any, BinaryIO, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ...workspace.parsing.coltypes import is_missing, parse_date, to_float

csv.field_size_limit(16 * 1024 * 1024)
ABS_TOL, REL_TOL = 1e-6, 1e-6


def _rows(stream: BinaryIO, declared: Sequence[Mapping[str, Any]], max_rows: int) -> Tuple[List[str], List[List[str]]]:
    import io
    with io.TextIOWrapper(stream, encoding="utf-8", newline="") as text:
        reader = csv.reader(text)
        header = next(reader, [])
        rows = []
        for r in reader:
            rows.append(r)
            if len(rows) > max_rows:
                raise ValueError("too many rows to verify")
    return header, rows


def _value(raw: str, kind: str) -> Any:
    if is_missing(raw):
        return None
    if kind in ("integer", "float"):
        return to_float(raw)
    if kind in ("date", "datetime"):
        return parse_date(raw)
    return raw.strip()


def _passes(v: Any, flt: Mapping[str, Any]) -> bool:
    op, want = flt["op"], flt.get("value")
    if op == "is_null":
        return v is None
    if op == "not_null":
        return v is not None
    if v is None:
        return False
    if isinstance(v, datetime) or hasattr(v, "year"):
        def d(x):
            r = parse_date(str(x))
            return r
        want = [d(x) for x in want] if isinstance(want, list) else d(want)
    elif isinstance(v, float):
        want = [float(x) for x in want] if isinstance(want, list) else float(want)
    else:
        want = [str(x).strip() for x in want] if isinstance(want, list) else str(want).strip()
    if op == "eq": return v == want
    if op == "ne": return v != want
    if op == "gt": return v > want
    if op == "ge": return v >= want
    if op == "lt": return v < want
    if op == "le": return v <= want
    if op == "between":
        lo, hi = sorted(want)
        return lo <= v <= hi
    if op == "in": return v in want
    if op == "not_in": return v not in want
    if op == "contains": return isinstance(v, str) and str(want).lower() in v.lower()
    raise ValueError("unknown filter")


def load(stream: BinaryIO, declared: Sequence[Mapping[str, Any]], filters: Sequence[Mapping[str, Any]], max_rows: int) -> Dict[str, List[Any]]:
    header, rows = _rows(stream, declared, max_rows)
    kinds = {c["name"]: c["type"] for c in declared}
    idx = {name: header.index(name) for name in kinds if name in header}
    cols: Dict[str, List[Any]] = {name: [] for name in idx}
    for r in rows:
        vals = {name: _value(r[i] if i < len(r) else "", kinds[name]) for name, i in idx.items()}
        if all(_passes(vals[f["column"]], f) for f in filters):
            for name in idx:
                cols[name].append(vals[name])
    return cols


def _present(xs: Iterable[Any]) -> List[float]:
    return [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]


def _agg(name: str, xs: List[Any]) -> Optional[float]:
    v = _present(xs)
    if name == "count":
        return float(len(v))
    if name == "nunique":
        return float(len(set(v)))
    if not v:
        return None
    if name == "mean": return statistics.fmean(v)
    if name == "sum": return math.fsum(v)
    if name == "median": return float(statistics.median(v))
    if name == "min": return float(min(v))
    if name == "max": return float(max(v))
    if name == "std": return statistics.stdev(v) if len(v) > 1 else None
    raise ValueError(name)


def _ranks(xs: List[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 3:
        return None
    ma, mb = math.fsum(a) / len(a), math.fsum(b) / len(b)
    sa = math.sqrt(math.fsum((x - ma) ** 2 for x in a))
    sb = math.sqrt(math.fsum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None
    return math.fsum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def recompute(cols: Mapping[str, List[Any]], spec: Mapping[str, Any]) -> Optional[float]:
    kind = spec["kind"]
    if kind == "agg":
        return _agg(spec["agg"], cols[spec["column"]] if spec.get("column") else next(iter(cols.values())))
    if kind == "group_agg":
        keys, vals = cols[spec["group"]], cols[spec["column"]] if spec.get("column") else None
        sel = [(v if vals is not None else 1.0) for k, v in zip(keys, vals if vals is not None else keys) if k is not None and str(k) == str(spec["key"])]
        return _agg(spec["agg"], sel)
    if kind == "missing":
        return float(sum(1 for v in cols[spec["column"]] if v is None))
    if kind == "rows":
        return float(len(next(iter(cols.values()))))
    if kind in ("pearson", "spearman"):
        pairs = [(a, b) for a, b in zip(cols[spec["x"]], cols[spec["y"]]) if a is not None and b is not None]
        a, b = [p[0] for p in pairs], [p[1] for p in pairs]
        if kind == "spearman":
            a, b = _ranks(a), _ranks(b)
        return _pearson(a, b)
    if kind == "pairs":
        return float(sum(1 for a, b in zip(cols[spec["x"]], cols[spec["y"]]) if a is not None and b is not None))
    raise ValueError(kind)


def close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def verify_facts(stream: BinaryIO, declared: Sequence[Mapping[str, Any]], filters: Sequence[Mapping[str, Any]], facts: Sequence[Dict[str, Any]], max_rows: int) -> Dict[str, Any]:
    """Annotates each fact that carries a `check` spec with `verified` = matched / mismatch / not_checked and returns a summary."""
    cols = load(stream, declared, filters, max_rows)
    matched = mismatched = skipped = 0
    for f in facts:
        spec = f.get("check")
        if not spec:
            f["verification"] = "not_checked"
            skipped += 1
            continue
        try:
            other = recompute(cols, spec)
        except (KeyError, ValueError, ZeroDivisionError):
            f["verification"] = "not_checked"
            skipped += 1
            continue
        f["independent_value"] = other
        ok = close(f["value"], other)
        f["verification"] = "matched" if ok else "mismatch"
        matched += ok
        mismatched += (not ok)
    return {"method": "independent recomputation from the stored table with the Python standard library", "checked": matched + mismatched, "matched": matched, "mismatched": mismatched,
            "not_checked": skipped, "status": "FAILED" if mismatched else ("PASSED" if matched else "NOT_CHECKED")}
