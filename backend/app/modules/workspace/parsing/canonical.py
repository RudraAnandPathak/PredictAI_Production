"""The canonical table: every dataset is normalised once into UTF-8 CSV so preview, profiling and cleaning share one
reader. The original upload is never touched. Counts of missing and invalid values come from the same single pass."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional

from ..errors import corrupted
from .coltypes import ColumnTally

Rows = Callable[[], Iterator[List[str]]]
csv.field_size_limit(16 * 1024 * 1024)


@dataclass
class ParsedTable:
    header: List[str]
    rows: Rows                      # call to get a fresh iterator over data rows (header excluded)
    meta: Dict[str, Any] = field(default_factory=dict)
    warnings: List[Dict[str, str]] = field(default_factory=list)


def warn(table_or_list, code: str, message: str) -> None:
    target = table_or_list.warnings if hasattr(table_or_list, "warnings") else table_or_list
    target.append({"code": code, "message": message})


def unique_names(raw: List[str]) -> List[str]:
    """Header cells become unique, non-empty column names. Duplicates get _2, _3 …; empty names become column_N."""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for index, name in enumerate(raw, start=1):
        base = re.sub(r"\s+", " ", (name or "").strip())[:200] or f"column_{index}"
        key = base.lower()
        seen[key] = seen.get(key, 0) + 1
        out.append(base if seen[key] == 1 else f"{base}_{seen[key]}")
    return out


def looks_like_header(first: List[str], second: Optional[List[str]]) -> bool:
    """A first row is a header when at most half its cells are empty, at least one has text, and none is numeric or a date.
    (Single letters such as 'n' or 'y' are valid column names, so boolean-looking words do not disqualify a header.)"""
    cells = [c.strip() for c in first]
    filled = [c for c in cells if c]
    if not filled or len(filled) * 2 < len(cells):
        return False
    from .coltypes import classify

    return not any(classify(c) in ("integer", "float", "date", "datetime") for c in filled)


def write_canonical(table: ParsedTable, out_chunks: Callable[[bytes], None], *, max_rows: int, max_columns: int) -> Dict[str, Any]:
    """Stream the table to canonical CSV bytes via `out_chunks` and return the column summary."""
    width = len(table.header)
    ragged = widened = blanks = 0
    # pass 1: widest row, row count (so the header can be extended instead of dropping extra cells)
    count = 0
    for row in table.rows():
        if not any((c or "").strip() for c in row):
            blanks += 1
            continue
        count += 1
        width = max(width, len(row))
        if count > max_rows:
            raise corrupted(f"This dataset has more than {max_rows:,} rows, which is above the supported limit. Split it into smaller files.")
    if width > max_columns:
        raise corrupted(f"This dataset has {width:,} columns, which is above the supported limit of {max_columns:,}.")
    if count == 0:
        raise corrupted("The file contains a header but no data rows." if table.header else "The file contains no data.")
    header = list(table.header) + [f"column_{i}" for i in range(len(table.header) + 1, width + 1)]
    if width > len(table.header):
        widened = width - len(table.header)
        warn(table, "EXTRA_COLUMNS", f"Some rows had more cells than the header; {widened} extra column(s) were added so no values are lost.")
    names = unique_names(header)
    tallies = [ColumnTally() for _ in names]
    buffer: List[str] = []

    class Sink:
        def write(self, text: str) -> int:
            buffer.append(text)
            if len(buffer) >= 512:
                flush()
            return len(text)

    def flush() -> None:
        if buffer:
            out_chunks("".join(buffer).encode("utf-8"))
            buffer.clear()

    writer = csv.writer(Sink(), lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(names)
    written = 0
    for row in table.rows():
        if not any((c or "").strip() for c in row):
            continue
        if len(row) < width:
            ragged += 1
            row = list(row) + [""] * (width - len(row))
        cells = ["" if c is None else str(c) for c in row]
        for i, cell in enumerate(cells):
            tallies[i].add(cell)
        writer.writerow(cells)
        written += 1
    flush()
    if ragged:
        warn(table, "SHORT_ROWS", f"{ragged:,} row(s) had fewer cells than the header; the missing cells were left empty.")
    if blanks:
        warn(table, "BLANK_ROWS", f"{blanks:,} completely blank row(s) were skipped.")
    columns = []
    for i, (name, tally) in enumerate(zip(names, tallies)):
        kind, invalid = tally.inferred()
        columns.append({"index": i, "name": name, "original_name": header[i] if header[i] != name else None, "type": kind,
                        "missing": tally.missing, "invalid": invalid})
    return {"columns": columns, "rows": written, "blank_rows_skipped": blanks, "short_rows_padded": ragged, "type_inference": "heuristic"}
