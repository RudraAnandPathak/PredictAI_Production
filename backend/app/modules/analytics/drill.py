"""Turns a chart/table `select` descriptor into ordinary filters, so drill-down uses the same filter engine as every analysis
(no second implementation of 'which rows are in this bar')."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import plan_invalid
from .plan import Filter, normalise_filters

MAX_SELECTED_ROWS = 200


def _num(v: Any, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise plan_invalid(where, "Use a number.")
    return float(v)


def select_to_filters(select: Any, types: Mapping[str, str]) -> Dict[str, Any]:
    """Returns {"filters": [Filter...], "rows": Optional[List[int]]}. Raises a field-level validation error for anything unexpected."""
    if not isinstance(select, dict) or not isinstance(select.get("type"), str):
        raise plan_invalid("select", "Choose a part of the chart.")
    kind = select["type"]
    raw: List[Dict[str, Any]] = []
    rows: Optional[List[int]] = None
    if kind == "all":
        pass
    elif kind == "category":
        raw.append({"column": select.get("column"), "op": "eq", "value": select.get("value")})
    elif kind == "range":
        lo, hi = _num(select.get("lo"), "select.lo"), _num(select.get("hi"), "select.hi")
        raw += [{"column": select.get("column"), "op": "ge", "value": lo},
                {"column": select.get("column"), "op": "le" if select.get("hi_inclusive") else "lt", "value": hi}]
    elif kind == "xy_range":
        for axis in ("x", "y"):
            if select.get(f"{axis}_lo") is not None or select.get(f"{axis}_hi") is not None:
                raw += [{"column": select.get(f"{axis}_column"), "op": "ge", "value": _num(select.get(f"{axis}_lo"), f"select.{axis}_lo")},
                        {"column": select.get(f"{axis}_column"), "op": "le", "value": _num(select.get(f"{axis}_hi"), f"select.{axis}_hi")}]
        if not raw:
            raise plan_invalid("select", "Drag a rectangle on the chart first.")
    elif kind == "cell":
        raw += [{"column": select.get("x_column"), "op": "eq", "value": select.get("x_value")},
                {"column": select.get("y_column"), "op": "eq", "value": select.get("y_value")}]
    elif kind == "pair":
        for axis in ("x", "y"):
            raw.append({"column": select.get(f"{axis}_column"), "op": "not_null"})
    elif kind == "period":
        try:
            start = date.fromisoformat(str(select.get("start")))
            end = date.fromisoformat(str(select.get("end"))) + timedelta(days=1)   # the period's last day is inclusive
        except ValueError:
            raise plan_invalid("select", "Use dates written like 2025-01-31.") from None
        raw += [{"column": select.get("column"), "op": "ge", "value": start.isoformat()}, {"column": select.get("column"), "op": "lt", "value": end.isoformat()}]
    elif kind == "rows":
        values = select.get("rows")
        if not isinstance(values, list) or not values or len(values) > MAX_SELECTED_ROWS or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in values):
            raise plan_invalid("select.rows", f"Choose between 1 and {MAX_SELECTED_ROWS} rows.")
        rows = sorted(set(values))
    else:
        raise plan_invalid("select.type", "This selection is not supported.")
    return {"filters": normalise_filters(raw, types), "rows": rows}
