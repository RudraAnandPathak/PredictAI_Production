"""The typed analysis plan. A plan is DATA: a dataset snapshot reference, filters and a list of operations drawn from a closed
vocabulary, each with typed parameters. Nothing in a plan is ever evaluated as code, so a hostile or hallucinated plan can at
worst be refused. Column names are checked against the snapshot's schema; unknown operations and parameters are rejected."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .constants import LIMITS, PLAN_SCHEMA, SEED_DEFAULT
from .errors import plan_invalid

NUMERIC_TYPES = ("integer", "float")
DATETIME_TYPES = ("date", "datetime")
FILTER_OPS = ("eq", "ne", "gt", "ge", "lt", "le", "between", "in", "not_in", "contains", "is_null", "not_null")
NULL_POLICIES = ("pairwise", "listwise")


def role_of(type_name: str) -> str:
    if type_name in NUMERIC_TYPES:
        return "numeric"
    if type_name in DATETIME_TYPES:
        return "datetime"
    return "categorical"


@dataclass(frozen=True)
class P:
    """One typed operation parameter."""
    kind: str                       # column | columns | int | float | str | bool | enum | enums | floats
    required: bool = False
    role: str = "any"               # for column(s): numeric | categorical | datetime | any
    default: Any = None
    choices: Tuple[Any, ...] = ()
    lo: Optional[float] = None
    hi: Optional[float] = None
    max_items: int = 60


@dataclass(frozen=True)
class OpSpec:
    name: str
    title: str
    params: Mapping[str, P]
    fn: Any = None                  # set by the registry; plans never carry callables
    inferential: bool = False       # needs assumptions/limitations/uncertainty in its evidence


@dataclass
class Filter:
    column: str
    op: str
    value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"column": self.column, "op": self.op}
        if self.op not in ("is_null", "not_null"):
            out["value"] = self.value
        return out


@dataclass
class Operation:
    op: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "params": self.params}


@dataclass
class AnalysisPlan:
    kind: str
    asset_id: str
    version_id: str
    operations: List[Operation]
    filters: List[Filter] = field(default_factory=list)
    null_policy: str = "pairwise"
    seed: int = SEED_DEFAULT
    sample_rows: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    def core(self) -> Dict[str, Any]:
        """Everything that determines the numbers (and nothing else); this is what is hashed."""
        return {"schema": PLAN_SCHEMA, "kind": self.kind, "dataset": {"asset_id": self.asset_id, "version_id": self.version_id},
                "filters": [f.to_dict() for f in self.filters], "operations": [o.to_dict() for o in self.operations],
                "null_policy": self.null_policy, "seed": self.seed, "sample_rows": self.sample_rows}

    def to_dict(self) -> Dict[str, Any]:
        return {**self.core(), "notes": list(self.notes), "expected_outputs": [o.op for o in self.operations]}

    def columns(self) -> List[str]:
        names: List[str] = []
        for f in self.filters:
            names.append(f.column)
        for o in self.operations:
            for v in o.params.values():
                if isinstance(v, str):
                    names.append(v)
                elif isinstance(v, list):
                    names.extend(x for x in v if isinstance(x, str))
        return names


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def plan_hash(plan: AnalysisPlan) -> str:
    return sha256_json(plan.core())


# ------------------------------------------------------------------------------------------------ validation
def _column(name: Any, types: Mapping[str, str], field_name: str, role: str) -> str:
    if not isinstance(name, str) or name not in types:
        raise plan_invalid(field_name, "Choose a column that exists in this dataset.")
    actual = role_of(types[name])
    if role == "numeric" and actual != "numeric":
        raise plan_invalid(field_name, f"'{name[:60]}' is not a numeric column.")
    if role == "datetime" and actual != "datetime":
        raise plan_invalid(field_name, f"'{name[:60]}' is not a date column.")
    if role == "categorical" and actual == "datetime":
        raise plan_invalid(field_name, f"'{name[:60]}' is a date column; choose a category column.")
    return name


def _number(value: Any, spec: P, field_name: str, integer: bool) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise plan_invalid(field_name, "Enter a number.")
    if integer and int(value) != value:
        raise plan_invalid(field_name, "Enter a whole number.")
    value = int(value) if integer else float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise plan_invalid(field_name, "Enter a finite number.")
    if spec.lo is not None and value < spec.lo or spec.hi is not None and value > spec.hi:
        raise plan_invalid(field_name, f"Enter a value between {spec.lo} and {spec.hi}.")
    return value


def normalise_params(spec: OpSpec, raw: Any, types: Mapping[str, str], where: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise plan_invalid(where, "Parameters must be an object.")
    unknown = sorted(set(raw) - set(spec.params))
    if unknown:
        raise plan_invalid(f"{where}.{unknown[0][:40]}", "This parameter is not recognised for this operation.")
    out: Dict[str, Any] = {}
    for name, p in spec.params.items():
        field_name = f"{where}.{name}"
        value = raw.get(name, None)
        if value is None:
            if p.required:
                raise plan_invalid(field_name, "This is required.")
            if p.default is not None:
                out[name] = p.default
            continue
        if p.kind == "column":
            out[name] = _column(value, types, field_name, p.role)
        elif p.kind == "columns":
            if not isinstance(value, list) or len(value) > min(p.max_items, LIMITS["max_columns_per_op"]):
                raise plan_invalid(field_name, "Choose a list of columns (not too many).")
            cols = [_column(v, types, field_name, p.role) for v in value]
            if len(set(cols)) != len(cols):
                raise plan_invalid(field_name, "Each column can be listed once.")
            out[name] = cols
        elif p.kind == "enums":
            if not isinstance(value, list) or not value or len(value) > len(p.choices) or any(v not in p.choices for v in value) or len(set(value)) != len(value):
                raise plan_invalid(field_name, "Choose from: " + ", ".join(str(c) for c in p.choices) + ".")
            out[name] = list(value)
        elif p.kind == "int":
            out[name] = _number(value, p, field_name, True)
        elif p.kind == "float":
            out[name] = _number(value, p, field_name, False)
        elif p.kind == "floats":
            if not isinstance(value, list) or not value or len(value) > p.max_items:
                raise plan_invalid(field_name, "Enter a short list of numbers.")
            out[name] = [_number(v, p, field_name, False) for v in value]
        elif p.kind == "bool":
            if not isinstance(value, bool):
                raise plan_invalid(field_name, "Use true or false.")
            out[name] = value
        elif p.kind == "enum":
            if value not in p.choices:
                raise plan_invalid(field_name, "Choose one of: " + ", ".join(str(c) for c in p.choices) + ".")
            out[name] = value
        elif p.kind == "str":
            if not isinstance(value, str) or len(value) > 200:
                raise plan_invalid(field_name, "Enter short text.")
            out[name] = value
    return out


def normalise_filters(raw: Any, types: Mapping[str, str]) -> List[Filter]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > LIMITS["max_filters"]:
        raise plan_invalid("filters", f"Use at most {LIMITS['max_filters']} filters.")
    out: List[Filter] = []
    for i, item in enumerate(raw):
        where = f"filters.{i}"
        if not isinstance(item, dict) or set(item) - {"column", "op", "value"}:
            raise plan_invalid(where, "A filter has a column, an operator and a value.")
        column = _column(item.get("column"), types, f"{where}.column", "any")
        op = item.get("op")
        if op not in FILTER_OPS:
            raise plan_invalid(f"{where}.op", "Choose one of: " + ", ".join(FILTER_OPS) + ".")
        value = item.get("value")
        if op in ("is_null", "not_null"):
            value = None
        elif op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise plan_invalid(f"{where}.value", "Give a lower and an upper bound.")
            _scalar(value[0], f"{where}.value"), _scalar(value[1], f"{where}.value")
        elif op in ("in", "not_in"):
            if not isinstance(value, list) or not value or len(value) > 200:
                raise plan_invalid(f"{where}.value", "Give a short list of values.")
            for v in value:
                _scalar(v, f"{where}.value")
        else:
            _scalar(value, f"{where}.value")
        out.append(Filter(column, op, value))
    return out


def _scalar(value: Any, where: str) -> None:
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float)):
        if isinstance(value, bool):
            return
        raise plan_invalid(where, "Use a text or number value.")
    if isinstance(value, str) and len(value) > 200:
        raise plan_invalid(where, "That value is too long.")
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise plan_invalid(where, "Use a finite number.")


def build_plan(*, kind: str, asset_id: str, version_id: str, operations: Any, filters: Any, null_policy: Any, seed: Any, sample_rows: Any,
               registry: Mapping[str, OpSpec], types: Mapping[str, str]) -> AnalysisPlan:
    """Validate untrusted input into an AnalysisPlan (or raise a field-level validation error)."""
    if not isinstance(operations, list) or not operations:
        raise plan_invalid("operations", "Choose at least one analysis.")
    if len(operations) > LIMITS["max_operations"]:
        raise plan_invalid("operations", f"Use at most {LIMITS['max_operations']} analyses in one request.")
    ops: List[Operation] = []
    for i, item in enumerate(operations):
        where = f"operations.{i}"
        if not isinstance(item, dict) or set(item) - {"op", "params"}:
            raise plan_invalid(where, "Each analysis has an 'op' and 'params'.")
        spec = registry.get(item.get("op")) if isinstance(item.get("op"), str) else None
        if spec is None:
            raise plan_invalid(f"{where}.op", "This analysis is not available.")
        ops.append(Operation(spec.name, normalise_params(spec, item.get("params") or {}, types, f"{where}.params")))
    policy = null_policy if null_policy is not None else "pairwise"
    if policy not in NULL_POLICIES:
        raise plan_invalid("null_policy", "Use 'pairwise' or 'listwise'.")
    if seed is None:
        seed = SEED_DEFAULT
    if isinstance(seed, bool) or not isinstance(seed, int) or not (0 <= seed < 2**31):
        raise plan_invalid("seed", "Use a whole number below 2,147,483,648.")
    if sample_rows is not None and (isinstance(sample_rows, bool) or not isinstance(sample_rows, int) or sample_rows < 50):
        raise plan_invalid("sample_rows", "A sample needs at least 50 rows.")
    return AnalysisPlan(kind=kind, asset_id=asset_id, version_id=version_id, operations=ops, filters=normalise_filters(filters, types),
                        null_policy=policy, seed=seed, sample_rows=sample_rows)
