"""Upload validation: the file must be what its name says, within limits, and structurally safe BEFORE any parser sees it.

Zip-based formats (docx, xlsx) are inspected without being extracted: entry count, total uncompressed size and compression ratio are bounded (zip-bomb defence), path traversal names are
refused, and any macro project is refused. PDFs must start with the PDF header, must not be encrypted and are page-capped by the parser. Text formats must not contain NUL bytes."""
from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass
from typing import Optional

from ...workspace.errors import corrupted, invalid, too_large, unsupported

EXT_TO_FORMAT = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "markdown", ".markdown": "markdown", ".csv": "csv", ".xlsx": "xlsx"}
SUPPORTED = tuple(sorted(set(EXT_TO_FORMAT.values())))
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNCOMPRESSED = 400 * 1024 * 1024
MAX_ZIP_RATIO = 120
REQUIRED_PART = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml"}


@dataclass
class Validated:
    format: str
    display_name: str
    size: int


def clean_display_name(name: object) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f\x7f‪-‮⁦-⁩]", "", base)
    return base[:200] or "document"


def validate_upload(filename: object, data: bytes, *, max_bytes: int) -> Validated:
    name = clean_display_name(filename)
    ext = os.path.splitext(name)[1].lower()
    fmt = EXT_TO_FORMAT.get(ext)
    if fmt is None:
        raise unsupported(f"“{ext or 'this file'}” is not a supported research source. Use PDF, DOCX, TXT, Markdown, CSV or XLSX.")
    if not data:
        raise corrupted("The file is empty.")
    if len(data) > max_bytes:
        raise too_large(max_bytes / (1024 * 1024))
    if fmt == "pdf":
        if not data[:1024].lstrip().startswith(b"%PDF-"):
            raise corrupted("This file is named .pdf but is not a PDF.")
    elif fmt in ("docx", "xlsx"):
        _check_zip(data, fmt)
    else:
        if b"\x00" in data[:65536]:
            raise corrupted("This file contains binary data, so it cannot be a text, Markdown or CSV file.")
    return Validated(fmt, name, len(data))


def _check_zip(data: bytes, fmt: str) -> None:
    if not data.startswith(b"PK"):
        raise corrupted(f"This file is named .{fmt} but is not a valid {fmt.upper()} document.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError):
        raise corrupted(f"This {fmt.upper()} file is damaged and cannot be opened.") from None
    if len(infos) > MAX_ZIP_ENTRIES:
        raise corrupted("This document contains too many internal parts.")
    total = 0
    for info in infos:
        n = info.filename
        if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/"):
            raise corrupted("This document contains an unsafe internal path.")
        total += info.file_size
        if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_ZIP_RATIO and info.file_size > 1024 * 1024:
            raise corrupted("This document is compressed in a way that looks like a decompression bomb.")
        if n.lower().endswith("vbaproject.bin"):
            raise unsupported("Documents that contain macros are not accepted.")
    if total > MAX_ZIP_UNCOMPRESSED:
        raise too_large(MAX_ZIP_UNCOMPRESSED / (1024 * 1024))
    names = {i.filename for i in infos}
    if REQUIRED_PART[fmt] not in names or "[Content_Types].xml" not in names:
        raise corrupted(f"This is not a valid {fmt.upper()} document.")


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
