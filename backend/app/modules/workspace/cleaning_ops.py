"""The cleaning engine: a small, closed set of explainable operations over a table of text cells. Pure Python, no I/O.

An operation list is validated against the table's columns, then run as a pipeline. The same plan on the same data always gives
the same result (statistics for mean/median/mode are taken from the *source* column, before any step runs, and the explanation
says so). Nothing here can touch files or run user-supplied code."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from .errors import invalid
from .parsing.coltypes import FALSE_TOKENS, TRUE_TOKENS, is_missing, parse_date, to_float

MAX_OPERATIONS = 25
MAX_VALUE = 500
FILTER_OPS = {"eq": "equals", "ne": "does not equal", "gt": "is greater than", "gte": "is at least", "lt": "is less than", "lte": "is at most",
              "contains": "contains", "not_contains": "does not contain", "is_empty": "is empty", "not_empty": "is not empty"}
NUMERIC_FILTERS = {"gt", "gte", "lt", "lte"}
CASES = {"lower": str.lower, "upper": str.upper, "title": str.title}
CONVERT_TO = ("integer", "float", "boolean", "date")
DATE_FORMATS = {"iso": ("%Y-%m-%d", "%Y/%m/%d"), "dmy": ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"), "mdy": ("%m/%d/%Y", "%m-%d-%Y")}
MISSING_NOTE = "empty (including NA, null, none, - and ?)"
_MONEY = re.compile(r"[\s$€£₹¥,]")

OP_NAMES = ("drop_duplicates", "fill_missing", "drop_missing", "convert_type", "rename_column", "drop_columns", "filter_rows", "trim_whitespace", "change_case")


def _num_text(x: float) -> str:
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return repr(round(x, 6))


def convert_value(value: str, to: str, date_format: str = "iso") -> Optional[str]:
    """Converted text, or None when the value cannot be converted."""
    text = value.strip()
    if to in ("integer", "float"):
        cleaned = _MONEY.sub("", text)
        if cleaned.endswith("%"):
            return None
        number = to_float(cleaned)
        if number is None:
            return None
        if to == "integer":
            return str(int(number)) if number == int(number) and abs(number) < 1e15 else None
        return _num_text(number)
    if to == "boolean":
        low = text.lower()
        if low in TRUE_TOKENS or low == "1":
            return "true"
        if low in FALSE_TOKENS or low == "0":
            return "false"
        return None
    if to == "date":
        from datetime import datetime

        for fmt in DATE_FORMATS.get(date_format, ()):
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        if date_format == "iso":
            parsed = parse_date(text)
            return parsed.strftime("%Y-%m-%d") if parsed else None
        return None
    return None


def _predicate(op: str, value: str) -> Callable[[str], bool]:
    low = value.lower()
    if op == "is_empty":
        return is_missing
    if op == "not_empty":
        return lambda v: not is_missing(v)
    if op == "eq":
        return lambda v: v.strip().lower() == low.strip()
    if op == "ne":
        return lambda v: v.strip().lower() != low.strip()
    if op == "contains":
        return lambda v: low in v.lower()
    if op == "not_contains":
        return lambda v: low not in v.lower()
    limit = to_float(value)

    def numeric(v: str) -> bool:
        number = to_float(_MONEY.sub("", v.strip()))
        if number is None or limit is None:
            return False
        return {"gt": number > limit, "gte": number >= limit, "lt": number < limit, "lte": number <= limit}[op]

    return numeric


class Plan:
    """Validated operations plus the schema they produce. `source_index[i]` is the source column behind result column i."""

    def __init__(self, operations: Any, header: List[str]) -> None:
        if not isinstance(operations, list) or not operations:
            raise invalid("operations", "Add at least one cleaning step.")
        if len(operations) > MAX_OPERATIONS:
            raise invalid("operations", f"Use {MAX_OPERATIONS} steps or fewer in one cleaning run.")
        self.source_header = list(header)
        self.names = list(header)
        self.source_index: List[Optional[int]] = list(range(len(header)))
        self.ops: List[Dict[str, Any]] = []
        self.stat_requests: Dict[Tuple[int, str], None] = {}
        self.explanations: List[str] = []
        for number, raw in enumerate(operations, start=1):
            self._add(number, raw)

    # -- helpers
    def _fail(self, number: int, message: str, field: str = "operations") -> Exception:
        return invalid(f"{field}[{number - 1}]", f"Step {number}: {message}")

    def _col(self, number: int, raw: Dict[str, Any], key: str = "column") -> int:
        name = raw.get(key)
        if not isinstance(name, str) or not name:
            raise self._fail(number, "choose a column.", key)
        matches = [i for i, n in enumerate(self.names) if n == name]
        if not matches:
            raise self._fail(number, f"there is no column called “{name[:60]}” at this point.", key)
        return matches[0]

    def _cols(self, number: int, raw: Dict[str, Any], *, optional: bool = False) -> List[int]:
        names = raw.get("columns")
        if names is None and optional:
            return list(range(len(self.names)))
        if not isinstance(names, list) or not names or len(names) > 1000 or not all(isinstance(n, str) for n in names):
            raise self._fail(number, "choose one or more columns.", "columns")
        out = []
        for name in dict.fromkeys(names):
            hits = [i for i, n in enumerate(self.names) if n == name]
            if not hits:
                raise self._fail(number, f"there is no column called “{name[:60]}” at this point.", "columns")
            out.append(hits[0])
        return out

    def _value(self, number: int, raw: Dict[str, Any], key: str = "value") -> str:
        value = raw.get(key)
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise self._fail(number, "enter a value.", key)
        text = str(value)
        if len(text) > MAX_VALUE or "\x00" in text:
            raise self._fail(number, f"values can be up to {MAX_VALUE} characters.", key)
        return text

    def _label(self, idxs: List[int]) -> str:
        shown = ", ".join(f"“{self.names[i]}”" for i in idxs[:4])
        return shown + (f" and {len(idxs) - 4} more" if len(idxs) > 4 else "")

    def _add(self, number: int, raw: Any) -> None:
        if not isinstance(raw, dict) or raw.get("op") not in OP_NAMES:
            raise self._fail(number, "this step type is not supported.", "op")
        op = raw["op"]
        spec: Dict[str, Any] = {"op": op}
        if op == "drop_duplicates":
            idxs = self._cols(number, raw, optional=True) if raw.get("columns") else list(range(len(self.names)))
            spec.update(idx=idxs)
            if raw.get("columns"):
                spec["columns"] = [self.names[i] for i in idxs]
            self.explanations.append("Remove repeated rows, keeping the first of each" + (f", comparing only {self._label(idxs)}." if raw.get("columns") else " (rows identical in every column)."))
        elif op == "fill_missing":
            i = self._col(number, raw)
            strategy = raw.get("strategy")
            if strategy not in ("constant", "mean", "median", "mode"):
                raise self._fail(number, "choose how to fill: a fixed value, mean, median or most common value.", "strategy")
            spec.update(idx=i, column=self.names[i], strategy=strategy)
            if strategy == "constant":
                spec["value"] = self._value(number, raw)
                what = f"the value “{spec['value'][:60]}”"
            else:
                if self.source_index[i] is None:
                    raise self._fail(number, "this column has no original values to take a statistic from.", "column")
                self.stat_requests[(self.source_index[i], strategy)] = None
                what = {"mean": "the mean", "median": "the median", "mode": "the most common value"}[strategy] + " of the original column"
            self.explanations.append(f"In “{self.names[i]}”, replace {MISSING_NOTE} cells with {what}.")
        elif op == "drop_missing":
            idxs = self._cols(number, raw, optional=True)
            how = raw.get("how", "any")
            if how not in ("any", "all"):
                raise self._fail(number, "choose “any” or “all”.", "how")
            spec.update(idx=idxs, how=how)
            if raw.get("columns") is not None:
                spec["columns"] = [self.names[i] for i in idxs]
            scope = self._label(idxs) if raw.get("columns") is not None else "any column"
            self.explanations.append(f"Remove rows where {'any of' if how == 'any' else 'all of'} {scope} {'is' if len(idxs) == 1 else 'are'} {MISSING_NOTE}." if raw.get("columns") is not None and len(idxs) > 1 else
                                     f"Remove rows where {scope} is {MISSING_NOTE}." if how == "any" else f"Remove rows that are {MISSING_NOTE} in every column.")
        elif op == "convert_type":
            i = self._col(number, raw)
            to = raw.get("to")
            if to not in CONVERT_TO:
                raise self._fail(number, "choose a type to convert to.", "to")
            on_invalid = raw.get("on_invalid", "empty")
            if on_invalid not in ("empty", "keep"):
                raise self._fail(number, "choose what to do with values that cannot be converted.", "on_invalid")
            fmt = raw.get("date_format", "iso")
            if to == "date" and fmt not in DATE_FORMATS:
                raise self._fail(number, "choose a date format (year-month-day, day/month/year or month/day/year).", "date_format")
            spec.update(idx=i, column=self.names[i], to=to, on_invalid=on_invalid)
            if to == "date":
                spec["date_format"] = fmt
            labels = {"iso": "year-month-day", "dmy": "day/month/year", "mdy": "month/day/year"}
            self.explanations.append(f"Convert “{self.names[i]}” to {to}" + (f" (reading dates as {labels[fmt]})" if to == "date" else "") +
                                     ". Values that cannot be converted are " + ("left empty." if on_invalid == "empty" else "kept as they are."))
        elif op == "rename_column":
            i = self._col(number, raw)
            new = raw.get("new_name")
            new = re.sub(r"\s+", " ", new).strip() if isinstance(new, str) else ""
            if not new or len(new) > 200 or "\x00" in new:
                raise self._fail(number, "enter a new name (up to 200 characters).", "new_name")
            if any(n.lower() == new.lower() for j, n in enumerate(self.names) if j != i):
                raise self._fail(number, f"another column is already called “{new}”.", "new_name")
            spec.update(idx=i, column=self.names[i], new_name=new)
            self.explanations.append(f"Rename column “{self.names[i]}” to “{new}”. The data is unchanged.")
            self.names[i] = new
        elif op == "drop_columns":
            idxs = self._cols(number, raw)
            if len(idxs) >= len(self.names):
                raise self._fail(number, "at least one column must remain.", "columns")
            spec.update(idx=idxs, columns=[self.names[i] for i in idxs])
            self.explanations.append(f"Remove {'column' if len(idxs) == 1 else 'columns'} {self._label(idxs)}.")
            keep = [i for i in range(len(self.names)) if i not in set(idxs)]
            self.names = [self.names[i] for i in keep]
            self.source_index = [self.source_index[i] for i in keep]
        elif op == "filter_rows":
            i = self._col(number, raw)
            fop = raw.get("filter")
            if fop not in FILTER_OPS:
                raise self._fail(number, "choose a condition.", "filter")
            action = raw.get("action", "keep")
            if action not in ("keep", "remove"):
                raise self._fail(number, "choose to keep or remove the matching rows.", "action")
            value = "" if fop in ("is_empty", "not_empty") else self._value(number, raw)
            if fop in NUMERIC_FILTERS and to_float(value) is None:
                raise self._fail(number, "this condition needs a number.", "value")
            spec.update(idx=i, column=self.names[i], filter=fop, value=value, action=action)
            tail = "" if not value else f" “{value[:60]}”"
            self.explanations.append(f"{'Keep only' if action == 'keep' else 'Remove'} rows where “{self.names[i]}” {FILTER_OPS[fop]}{tail}.")
        elif op == "trim_whitespace":
            idxs = self._cols(number, raw, optional=True)
            spec.update(idx=idxs)
            if raw.get("columns") is not None:
                spec["columns"] = [self.names[i] for i in idxs]
            self.explanations.append(f"Remove spaces at the start and end of values and collapse repeated spaces in {self._label(idxs) if raw.get('columns') is not None else 'every column'}.")
        elif op == "change_case":
            idxs = self._cols(number, raw)
            case = raw.get("case")
            if case not in CASES:
                raise self._fail(number, "choose lower, upper or title case.", "case")
            spec.update(idx=idxs, columns=[self.names[i] for i in idxs], case=case)
            self.explanations.append(f"Change {self._label(idxs)} to {case} case.")
        self.ops.append(spec)

    # -- schema facts
    @property
    def schema_changed(self) -> bool:
        return self.names != self.source_header

    def public_operations(self) -> List[Dict[str, Any]]:
        """Normalised operations for the history (no internal indexes)."""
        return [{k: v for k, v in op.items() if k not in ("idx",)} for op in self.ops]

    # -- statistics from the source column (one pass over the source)
    def compute_stats(self, rows: Iterable[List[str]]) -> Dict[Tuple[int, str], Any]:
        if not self.stat_requests:
            return {}
        by_col: Dict[int, List[str]] = {}
        for (col, kind) in self.stat_requests:
            by_col.setdefault(col, []).append(kind)
        numbers: Dict[int, List[float]] = {c: [] for c in by_col}
        modes: Dict[int, Counter] = {c: Counter() for c in by_col}
        for row in rows:
            for col in by_col:
                value = row[col] if col < len(row) else ""
                if is_missing(value):
                    continue
                modes[col][value] += 1
                number = to_float(_MONEY.sub("", value.strip()))
                if number is not None:
                    numbers[col].append(number)
        stats: Dict[Tuple[int, str], Any] = {}
        for col, kinds in by_col.items():
            for kind in kinds:
                if kind == "mode":
                    if not modes[col]:
                        raise invalid("operations", f"Column “{self.source_header[col]}” has no values to take the most common one from.")
                    stats[(col, kind)] = sorted(modes[col].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                else:
                    values = sorted(numbers[col])
                    if not values:
                        raise invalid("operations", f"Column “{self.source_header[col]}” has no numbers, so its {kind} cannot be used. Choose a fixed value or the most common value.")
                    if kind == "mean":
                        stats[(col, kind)] = _num_text(sum(values) / len(values))
                    else:
                        mid = len(values) // 2
                        stats[(col, kind)] = _num_text(values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2)
        return stats

    # -- execution
    def run(self, rows: Iterable[List[str]], stats: Dict[Tuple[int, str], Any], on_removed: Optional[Callable[[int, List[str], str], None]] = None) -> Tuple[Iterator[Tuple[int, List[str], List[str]]], List[Dict[str, int]]]:
        """Returns (generator of (source_row_number, source_cells, result_cells) for kept rows, per-step effect counters)."""
        effects: List[Dict[str, int]] = [{"rows_removed": 0, "cells_changed": 0, "unconvertible": 0} for _ in self.ops]
        stages = [self._stage(i, spec, effects[i], stats) for i, spec in enumerate(self.ops)]
        blank_removed = {"rows_removed": 0}
        self.blank_effect = blank_removed

        def generate() -> Iterator[Tuple[int, List[str], List[str]]]:
            for number, source in enumerate(rows, start=1):
                cells = list(source)
                dropped_by = None
                for i, stage in enumerate(stages):
                    cells = stage(cells)
                    if cells is None:
                        effects[i]["rows_removed"] += 1
                        dropped_by = self.explanations[i]
                        break
                if cells is None:
                    if on_removed:
                        on_removed(number, list(source), dropped_by or "")
                    continue
                if not any(c.strip() for c in cells):
                    blank_removed["rows_removed"] += 1
                    if on_removed:
                        on_removed(number, list(source), "The row became completely empty.")
                    continue
                yield number, list(source), cells

        return generate(), effects

    def _stage(self, index: int, spec: Dict[str, Any], ef: Dict[str, int], stats: Dict[Tuple[int, str], Any]) -> Callable[[List[str]], Optional[List[str]]]:
        op = spec["op"]
        if op == "rename_column":
            return lambda cells: cells
        if op == "drop_columns":
            drop = set(spec["idx"])
            return lambda cells: [c for j, c in enumerate(cells) if j not in drop]
        if op == "drop_duplicates":
            idxs, seen = spec["idx"], set()

            def dedupe(cells: List[str]) -> Optional[List[str]]:
                key = tuple(cells[j] for j in idxs)
                if key in seen:
                    return None
                seen.add(key)
                return cells
            return dedupe
        if op == "drop_missing":
            idxs, how = spec["idx"], spec["how"]
            check = any if how == "any" else all
            return lambda cells: None if check(is_missing(cells[j]) for j in idxs) else cells
        if op == "fill_missing":
            j = spec["idx"]
            fill = spec["value"] if spec["strategy"] == "constant" else stats[(self.source_index_at(index, j), spec["strategy"])]
            spec["fill_value"] = fill

            def fill_cells(cells: List[str]) -> List[str]:
                if is_missing(cells[j]):
                    cells[j] = fill
                    ef["cells_changed"] += 1
                return cells
            return fill_cells
        if op == "convert_type":
            j, to, keep, fmt = spec["idx"], spec["to"], spec["on_invalid"] == "keep", spec.get("date_format", "iso")

            def convert(cells: List[str]) -> List[str]:
                v = cells[j]
                if is_missing(v):
                    return cells
                out = convert_value(v, to, fmt)
                if out is None:
                    ef["unconvertible"] += 1
                    if not keep:
                        cells[j] = ""
                        ef["cells_changed"] += 1
                elif out != v:
                    cells[j] = out
                    ef["cells_changed"] += 1
                return cells
            return convert
        if op == "filter_rows":
            j, match, keep_matches = spec["idx"], _predicate(spec["filter"], spec["value"]), spec["action"] == "keep"
            return lambda cells: cells if match(cells[j]) == keep_matches else None
        if op == "trim_whitespace":
            idxs = spec["idx"]

            def trim(cells: List[str]) -> List[str]:
                for j in idxs:
                    new = " ".join(cells[j].split())
                    if new != cells[j]:
                        cells[j] = new
                        ef["cells_changed"] += 1
                return cells
            return trim
        if op == "change_case":
            idxs, fn = spec["idx"], CASES[spec["case"]]

            def recase(cells: List[str]) -> List[str]:
                for j in idxs:
                    new = fn(cells[j])
                    if new != cells[j]:
                        cells[j] = new
                        ef["cells_changed"] += 1
                return cells
            return recase
        raise AssertionError(op)

    def source_index_at(self, op_index: int, column_index: int) -> int:
        """Source column behind result-position `column_index` as the schema stood just before step `op_index` ran."""
        source = list(range(len(self.source_header)))
        for spec in self.ops[:op_index]:
            if spec["op"] == "drop_columns":
                drop = set(spec["idx"])
                source = [s for j, s in enumerate(source) if j not in drop]
        return source[column_index]
