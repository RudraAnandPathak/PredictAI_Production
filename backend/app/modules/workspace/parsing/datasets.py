"""Dataset readers. Each returns a ParsedTable (header + re-iterable rows) without loading more than it must.
Cell values are carried as text; typing is inferred later from the canonical table."""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ....core.errors import AppError
from ..errors import corrupted
from . import xmlsafe
from .canonical import ParsedTable, looks_like_header, unique_names, warn

MAX_CELL_CHARS = 100_000
_COL = re.compile(r"^([A-Z]+)(\d+)$")


def unavailable(what: str, hint: str) -> AppError:
    return AppError("PARSER_UNAVAILABLE", f"{what} files cannot be read on this server yet. {hint}", 422)


# ------------------------------------------------------------------ CSV / TSV
def _decode_choice(raw: bytes):
    """Return (encoding, note). BOMs win; then strict UTF-8; then chardet if installed; then Windows-1252."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", None
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", None
    try:
        raw.decode("utf-8")
        return "utf-8", None
    except UnicodeDecodeError:
        pass
    try:
        import chardet  # optional

        guess = chardet.detect(raw[:200_000])
        if guess.get("encoding") and (guess.get("confidence") or 0) >= 0.6:
            codec = guess["encoding"]
            "".encode(codec)
            return codec, f"The text encoding was detected as {codec} (a guess); check that accented characters look right."
    except Exception:
        pass
    return "cp1252", "The text encoding is not UTF-8; it was read as Windows-1252 (a guess). Check that accented characters look right."


def read_csv(path: Path, fmt_hint: str = "csv") -> ParsedTable:
    with open(path, "rb") as handle:
        sample = handle.read(262144)
    if b"\x00" in sample and not sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise corrupted("This file contains binary data, so it is not a text CSV file.")
    encoding, note = _decode_choice(sample)
    text_sample = sample.decode(encoding if encoding != "utf-16" else "utf-16", errors="replace")[:65536]
    delimiter = ","
    if path.suffix.lower() == ".tsv":
        delimiter = "\t"
    else:
        try:
            delimiter = csv.Sniffer().sniff(text_sample, delimiters=",;\t|").delimiter
        except csv.Error:
            counts = {d: text_sample.count(d) for d in (",", ";", "\t", "|")}
            delimiter = max(counts, key=counts.get) if max(counts.values()) else ","

    def open_text():
        return open(path, "r", encoding=encoding, errors="replace", newline="")

    def raw_rows() -> Iterator[List[str]]:
        with open_text() as handle:
            for row in csv.reader(handle, delimiter=delimiter):
                yield [c[:MAX_CELL_CHARS] for c in row]

    iterator = raw_rows()
    try:
        first = next(iterator)
        second = next(iterator, None)
    except csv.Error as error:
        raise corrupted("The file is not valid CSV and could not be read.") from error
    except StopIteration:
        raise corrupted("The file is empty.")
    finally:
        iterator.close()
    has_header = looks_like_header(first, second)
    table = ParsedTable(header=[], rows=lambda: iter(()), meta={"encoding": encoding, "delimiter": {"\t": "tab"}.get(delimiter, delimiter), "header_detected": has_header})
    if note:
        warn(table, "ENCODING_GUESS", note)
    if has_header:
        table.header = unique_names(first)

        def rows() -> Iterator[List[str]]:
            it = raw_rows()
            next(it, None)
            yield from _safe_rows(it)
    else:
        table.header = [f"column_{i}" for i in range(1, len(first) + 1)]
        warn(table, "NO_HEADER", "No header row was detected, so columns were named column_1, column_2 … Rename them if needed.")

        def rows() -> Iterator[List[str]]:
            yield from _safe_rows(raw_rows())
    table.rows = rows
    return table


def _safe_rows(it: Iterator[List[str]]) -> Iterator[List[str]]:
    try:
        yield from it
    except csv.Error as error:
        raise corrupted("The file is not valid CSV (for example an unclosed quote) and could not be read.") from error


# ------------------------------------------------------------------ JSON
def _flatten(value: Any, prefix: str, out: Dict[str, str], depth: int = 0) -> None:
    if isinstance(value, dict) and depth < 4 and value:
        for key, inner in value.items():
            _flatten(inner, f"{prefix}.{key}" if prefix else str(key), out, depth + 1)
    elif isinstance(value, (dict, list)):
        out[prefix or "value"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif value is None:
        out[prefix or "value"] = ""
    elif isinstance(value, bool):
        out[prefix or "value"] = "true" if value else "false"
    else:
        out[prefix or "value"] = str(value)


def _records_from_json(data: Any):
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        lists = [(k, v) for k, v in data.items() if isinstance(v, list) and v and all(isinstance(x, (dict, list)) for x in v)]
        if len(lists) == 1:
            return lists[0][1], lists[0][0]
        if lists:
            raise corrupted("This JSON file contains several lists of records. Keep one list per file (or export each separately).")
        return [data], None
    raise corrupted("This JSON file is a single value, not a table of records.")


def read_json(path: Path) -> ParsedTable:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise corrupted("This JSON file is not valid UTF-8 text.") from error
    note: Optional[str] = None
    try:
        data = json.loads(text)
        records, root = _records_from_json(data)
    except RecursionError as error:
        raise corrupted("This JSON file is nested too deeply to read.") from error
    except json.JSONDecodeError as error:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        try:
            records = [json.loads(ln) for ln in lines]
            if not lines:
                raise ValueError
            root, note = None, "The file was read as JSON Lines (one record per line)."
        except (ValueError, RecursionError):
            raise corrupted(f"This is not valid JSON (problem near line {error.lineno}, column {error.colno}). Check for missing commas or quotes.") from error
    if not records:
        raise corrupted("This JSON file contains no records.")
    flat: List[Dict[str, str]] = []
    keys: Dict[str, None] = {}
    for item in records:
        row: Dict[str, str] = {}
        if isinstance(item, dict):
            _flatten(item, "", row)
        elif isinstance(item, list):
            for i, cell in enumerate(item, start=1):
                _flatten(cell, f"column_{i}", row)
        else:
            _flatten(item, "value", row)
        flat.append(row)
        for k in row:
            keys.setdefault(k, None)
    header = list(keys)
    table = ParsedTable(header=header, rows=lambda: ([r.get(k, "") for k in header] for r in flat),
                        meta={"records_path": root, "nested_values": "flattened to dotted column names (arrays kept as JSON text)"})
    if note:
        warn(table, "JSON_LINES", note)
    return table


# ------------------------------------------------------------------ XML
def read_xml(path: Path) -> ParsedTable:
    root = xmlsafe.parse_bytes(path.read_bytes())
    children = list(root)
    if not children:
        raise corrupted("This XML file has no repeating records to turn into a table.")
    tags = [xmlsafe.local(c.tag) for c in children]
    records = children
    if len(set(tags)) == len(tags) and len(children) > 0 and len(children) < 2 or (len(set(tags)) > 1 and max(tags.count(t) for t in set(tags)) < 2):
        # wrapper element: descend one level to find the repeated record element
        inner = list(children[0]) if len(children) == 1 else []
        if inner and len(set(xmlsafe.local(c.tag) for c in inner)) == 1 and len(inner) > 1:
            records = inner
        else:
            records = [root]
    else:
        common = max(set(tags), key=tags.count)
        records = [c for c in children if xmlsafe.local(c.tag) == common]
    flat: List[Dict[str, str]] = []
    keys: Dict[str, None] = {}
    for rec in records:
        row: Dict[str, str] = {}
        for k, v in rec.attrib.items():
            row["@" + xmlsafe.local(k)] = v[:MAX_CELL_CHARS]
        sub = list(rec)
        if not sub:
            row["value"] = (rec.text or "").strip()[:MAX_CELL_CHARS]
        for child in sub:
            name = xmlsafe.local(child.tag)
            grand = list(child)
            if grand:
                for g in grand:
                    row[f"{name}.{xmlsafe.local(g.tag)}"] = (g.text or "").strip()[:MAX_CELL_CHARS]
            else:
                value = (child.text or "").strip()[:MAX_CELL_CHARS]
                row[name] = f"{row[name]}; {value}" if name in row else value
            for k, v in child.attrib.items():
                row[f"{name}@{xmlsafe.local(k)}"] = v[:MAX_CELL_CHARS]
        flat.append(row)
        for k in row:
            keys.setdefault(k, None)
    if not keys:
        raise corrupted("This XML file has no readable values to turn into a table.")
    header = list(keys)
    return ParsedTable(header=header, rows=lambda: ([r.get(k, "") for k in header] for r in flat),
                       meta={"record_element": xmlsafe.local(records[0].tag), "record_count": len(records)})


# ------------------------------------------------------------------ XLSX
def _guard_zip(fn):
    """Rows are produced lazily, after read_dataset has returned, so damage found mid-stream must still surface as FILE_CORRUPTED."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            yield from fn(*args, **kwargs)
        except zipfile.BadZipFile as error:
            raise corrupted("This file is damaged (it is not a readable spreadsheet package).") from error
        except (KeyError, ValueError, OSError, zipfile.LargeZipFile) as error:
            raise corrupted("This spreadsheet is damaged or uses an unsupported structure.") from error
    return wrapper


_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DATE_BUILTIN = set(range(14, 23)) | set(range(45, 48))
_DATE_CHARS = re.compile(r"[ymdhs]", re.I)


def _col_index(ref: str) -> int:
    letters = _COL.match(ref)
    if not letters:
        return 0
    n = 0
    for ch in letters.group(1):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _excel_value(serial: float, with_time: bool) -> str:
    base = datetime(1899, 12, 30) + timedelta(days=serial)
    if not with_time or (base.hour == 0 and base.minute == 0 and base.second == 0):
        return base.strftime("%Y-%m-%d")
    return base.strftime("%Y-%m-%d %H:%M:%S")


def _number_text(raw: str) -> str:
    try:
        number = float(raw)
    except ValueError:
        return raw
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def read_xlsx(path: Path, zf: zipfile.ZipFile) -> ParsedTable:
    """`zf` is used only for metadata while this function runs; row iteration opens its own handle (rows are read lazily)."""
    names = set(zf.namelist())
    if "xl/workbook.xml" not in names:
        raise corrupted("This is not a valid Excel workbook (xl/workbook.xml is missing).")
    if any(n.startswith("xl/vbaProject") for n in names):
        raise corrupted("Macro-enabled workbooks are not accepted. Save a macro-free .xlsx copy and upload that.")
    workbook = xmlsafe.parse_bytes(zf.read("xl/workbook.xml"))
    sheets = [(s.get("name") or "", s.get(_NS_REL + "id")) for s in workbook.iter(_NS_MAIN + "sheet")]
    if not sheets:
        raise corrupted("This workbook has no worksheets.")
    rels: Dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        for rel in xmlsafe.parse_bytes(zf.read("xl/_rels/workbook.xml.rels")):
            rels[rel.get("Id") or ""] = rel.get("Target") or ""
    sheet_name, rid = sheets[0]
    target = rels.get(rid or "", "worksheets/sheet1.xml")
    sheet_path = target.lstrip("/") if target.startswith("/") else "xl/" + target
    if sheet_path not in names:
        raise corrupted("The first worksheet could not be found inside the workbook.")
    shared: List[str] = []
    if "xl/sharedStrings.xml" in names:
        with zf.open("xl/sharedStrings.xml") as handle:
            for event, el in xmlsafe.iterparse(handle, events=("end",)):
                if el.tag == _NS_MAIN + "si":
                    shared.append("".join(t.text or "" for t in el.iter(_NS_MAIN + "t"))[:MAX_CELL_CHARS])
                    el.clear()
    date_styles = set()
    if "xl/styles.xml" in names:
        styles = xmlsafe.parse_bytes(zf.read("xl/styles.xml"))
        custom = {int(n.get("numFmtId", -1)): n.get("formatCode", "") for n in styles.iter(_NS_MAIN + "numFmt")}
        xfs = styles.find(_NS_MAIN + "cellXfs")
        for i, xf in enumerate(list(xfs) if xfs is not None else []):
            fid = int(xf.get("numFmtId", 0))
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", custom.get(fid, ""))
            if fid in _DATE_BUILTIN or (fid in custom and _DATE_CHARS.search(code)):
                date_styles.add(i)

    @_guard_zip
    def rows() -> Iterator[List[str]]:
        with zipfile.ZipFile(path) as own, own.open(sheet_path) as handle:
            current: Dict[int, str] = {}
            last_row = 0
            for event, el in xmlsafe.iterparse(handle, events=("end",)):
                if el.tag == _NS_MAIN + "row":
                    number = int(el.get("r") or last_row + 1)
                    cells: Dict[int, str] = {}
                    for pos, c in enumerate(el.findall(_NS_MAIN + "c")):
                        idx = _col_index(c.get("r") or "") if c.get("r") else pos
                        t = c.get("t")
                        v = c.find(_NS_MAIN + "v")
                        if t == "inlineStr":
                            value = "".join(x.text or "" for x in c.iter(_NS_MAIN + "t"))
                        elif v is None or v.text is None:
                            value = ""
                        elif t == "s":
                            try:
                                value = shared[int(v.text)]
                            except (ValueError, IndexError):
                                value = ""
                        elif t == "b":
                            value = "true" if v.text.strip() == "1" else "false"
                        elif t in ("str", "e"):
                            value = v.text
                        else:
                            if int(c.get("s") or 0) in date_styles:
                                try:
                                    value = _excel_value(float(v.text), "." in v.text)
                                except (ValueError, OverflowError):
                                    value = v.text
                            else:
                                value = _number_text(v.text)
                        cells[idx] = value[:MAX_CELL_CHARS]
                    while last_row + 1 < number and number - last_row < 10_000:
                        last_row += 1
                        yield []
                    last_row = number
                    width = (max(cells) + 1) if cells else 0
                    yield [cells.get(i, "") for i in range(width)]
                    el.clear()

    return _from_grid("xlsx", rows, {"sheet": sheet_name, "sheets_in_file": len(sheets)}, extra_sheets=[s[0] for s in sheets[1:]])


# ------------------------------------------------------------------ ODS
def read_ods(path: Path, zf: zipfile.ZipFile) -> ParsedTable:
    if "content.xml" not in zf.namelist():
        raise corrupted("This is not a valid OpenDocument spreadsheet (content.xml is missing).")
    T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    O = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
    X = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    sheet_names: List[str] = []
    with zf.open("content.xml") as first_pass:
        for _, el in xmlsafe.iterparse(first_pass, events=("start",)):
            if el.tag == T + "table":
                sheet_names.append(el.get(T + "name") or "")
    if not sheet_names:
        raise corrupted("This spreadsheet has no sheets.")

    @_guard_zip
    def rows() -> Iterator[List[str]]:
        depth = 0
        with zipfile.ZipFile(path) as own, own.open("content.xml") as handle:
            for event, el in xmlsafe.iterparse(handle, events=("start", "end")):
                if event == "start" and el.tag == T + "table":
                    depth += 1
                    continue
                if event == "end" and el.tag == T + "table":
                    return  # first sheet only
                if event == "end" and el.tag == T + "table-row" and depth == 1:
                    repeat_rows = min(int(el.get(T + "number-rows-repeated") or 1), 1000)
                    cells: List[str] = []
                    for c in el:
                        if c.tag not in (T + "table-cell", T + "covered-table-cell"):
                            continue
                        rep = min(int(c.get(T + "number-columns-repeated") or 1), 1000)
                        vtype = c.get(O + "value-type")
                        if vtype in ("float", "percentage", "currency"):
                            value = _number_text(c.get(O + "value") or "")
                        elif vtype == "date":
                            value = (c.get(O + "date-value") or "").replace("T", " ")
                        elif vtype == "boolean":
                            value = c.get(O + "boolean-value") or ""
                        else:
                            value = "\n".join("".join(p.itertext()) for p in c.findall(X + "p"))
                        cells.extend([value[:MAX_CELL_CHARS]] * rep)
                    while cells and cells[-1] == "":
                        cells.pop()
                    for _ in range(repeat_rows if cells else 1):
                        yield list(cells)
                    el.clear()

    return _from_grid("ods", rows, {"sheet": sheet_names[0], "sheets_in_file": len(sheet_names)}, extra_sheets=sheet_names[1:])


# ------------------------------------------------------------------ XLS (legacy)
def read_xls(path: Path) -> ParsedTable:
    try:
        import xlrd  # type: ignore
    except ImportError:
        raise unavailable("Legacy Excel (.xls)", "Open the file in Excel or LibreOffice, save it as .xlsx or .csv, and upload that instead. "
                          "Server administrators can enable .xls by installing the optional 'xlrd' package (see requirements-data.txt).")
    try:
        book = xlrd.open_workbook(str(path), on_demand=True, formatting_info=False)
        sheet = book.sheet_by_index(0)
    except Exception as error:  # xlrd raises several unrelated types for damaged files
        raise corrupted("This .xls file is damaged or password-protected and could not be read.") from error

    def rows() -> Iterator[List[str]]:
        for r in range(sheet.nrows):
            out: List[str] = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        out.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode).strftime("%Y-%m-%d %H:%M:%S").replace(" 00:00:00", ""))
                    except Exception:
                        out.append(str(cell.value))
                elif cell.ctype == xlrd.XL_CELL_NUMBER:
                    out.append(_number_text(repr(cell.value)))
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    out.append("true" if cell.value else "false")
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    out.append("")
                else:
                    out.append(str(cell.value)[:MAX_CELL_CHARS])
            yield out

    return _from_grid("xls", rows, {"sheet": sheet.name, "sheets_in_file": book.nsheets}, extra_sheets=book.sheet_names()[1:])


# ------------------------------------------------------------------ shared
def _from_grid(fmt: str, rows, meta: Dict[str, Any], *, extra_sheets: List[str]) -> ParsedTable:
    """Spreadsheet grids: skip leading blank rows; the first non-blank row is the header when it looks like one."""
    it = rows()
    first = None
    skipped = 0
    for row in it:
        if any((c or "").strip() for c in row):
            first = row
            break
        skipped += 1
    it.close()
    if first is None:
        raise corrupted("The first worksheet is empty.")
    table = ParsedTable(header=[], rows=lambda: iter(()), meta=meta)
    # peek the following row to judge header-ness
    probe = rows()
    seen_first = False
    second = None
    for row in probe:
        if not any((c or "").strip() for c in row):
            continue
        if not seen_first:
            seen_first = True
            continue
        second = row
        break
    probe.close()
    has_header = looks_like_header(first, second)
    meta["header_detected"] = has_header
    if has_header:
        table.header = unique_names(first)

        def data() -> Iterator[List[str]]:
            started = False
            for row in rows():
                if not started:
                    if any((c or "").strip() for c in row):
                        started = True
                    continue
                yield row
    else:
        table.header = [f"column_{i}" for i in range(1, len(first) + 1)]
        warn(table, "NO_HEADER", "No header row was detected, so columns were named column_1, column_2 … Rename them if needed.")

        def data() -> Iterator[List[str]]:
            yield from rows()
    table.rows = data
    if extra_sheets:
        warn(table, "OTHER_SHEETS_IGNORED", f"Only the first sheet was read. {len(extra_sheets)} other sheet(s) were not imported: {', '.join(extra_sheets[:5])}{' …' if len(extra_sheets) > 5 else ''}.")
    return table


def read_dataset(path: Path, fmt: str) -> ParsedTable:
    if fmt == "csv":
        return read_csv(path)
    if fmt == "json":
        return read_json(path)
    if fmt == "xml":
        return read_xml(path)
    if fmt == "xls":
        return read_xls(path)
    if fmt in ("xlsx", "ods"):
        try:
            with zipfile.ZipFile(path) as zf:
                return read_xlsx(path, zf) if fmt == "xlsx" else read_ods(path, zf)
        except zipfile.BadZipFile as error:
            raise corrupted("This file is damaged (it is not a readable spreadsheet package).") from error
        except (KeyError, ValueError, OSError) as error:
            raise corrupted("This spreadsheet is damaged or uses an unsupported structure.") from error
    raise corrupted("Unsupported dataset format.")
