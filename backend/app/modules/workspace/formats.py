"""Server-side file type validation. The browser's MIME type and the file name are never trusted: the
extension only says what the user *claims*; the content must agree. Nothing is ever extracted to disk or executed."""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional, Tuple

from .constants import EXTENSION_TO_FORMAT, FORMATS, SUPPORTED_EXTENSIONS
from .errors import corrupted, unsupported

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 1024 * 1024 * 1024   # 1 GiB across all members
MAX_ZIP_MEMBER = 512 * 1024 * 1024          # a single member
MAX_ZIP_RATIO = 250                         # uncompressed / compressed, per member

# Signatures of formats that must never be accepted whatever the extension says.
_FORBIDDEN = {
    b"MZ": "a Windows program", b"\x7fELF": "a program", b"\xca\xfe\xba\xbe": "a program", b"\xcf\xfa\xed\xfe": "a program",
    b"\x1f\x8b": "a compressed archive (.gz)", b"Rar!": "a compressed archive (.rar)", b"7z\xbc\xaf": "a compressed archive (.7z)",
    b"\x89PNG": "an image", b"\xff\xd8\xff": "an image", b"GIF8": "an image", b"#!": "a script",
}

READABLE_HINT = "Supported datasets: CSV, XLSX, XLS, ODS, JSON, XML. Supported documents: PDF, DOCX, RTF, TXT, Markdown."


def extension_format(extension: str) -> Optional[str]:
    return EXTENSION_TO_FORMAT.get(extension)


def check_extension(extension: str) -> str:
    fmt = extension_format(extension)
    if fmt is None:
        shown = f".{extension}" if extension else "without an extension"
        raise unsupported(f"Files {shown} are not supported. {READABLE_HINT}")
    return fmt


def kind_of(fmt: str) -> str:
    return FORMATS[fmt][0]


def _looks_text(head: bytes) -> bool:
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    sample = head[:8192]
    if not sample:
        return True
    controls = sum(1 for b in sample if b < 9 or (13 < b < 32 and b != 27))
    return controls / len(sample) < 0.02


def safe_zip(path: Path) -> zipfile.ZipFile:
    """Open an OOXML/ODF container with decompression-bomb and path checks. Members are only ever read in memory."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise corrupted("The file looks damaged: it could not be opened as a valid document. Re-export it from the original program and upload it again.") from error
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        archive.close()
        raise corrupted("The file contains an unusually large number of parts and was rejected for safety.")
    total = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
            archive.close()
            raise corrupted("The file contains unsafe internal paths and was rejected for safety.")
        total += info.file_size
        ratio = info.file_size / max(1, info.compress_size)
        if info.file_size > MAX_ZIP_MEMBER or total > MAX_ZIP_UNCOMPRESSED or (info.file_size > 1_000_000 and ratio > MAX_ZIP_RATIO):
            archive.close()
            raise corrupted("The file expands to an unreasonable size when opened (possible decompression bomb) and was rejected for safety.")
    return archive


def read_member(archive: zipfile.ZipFile, name: str, limit: int = 256 * 1024 * 1024) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise corrupted(f"The file is missing a required part ({name}). It may be damaged or not a real document of this type.") from error
    if info.file_size > limit:
        raise corrupted("A part of the file is too large to process safely.")
    with archive.open(info) as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise corrupted("A part of the file is too large to process safely.")
    return data


def validate_content(path: Path, extension: str) -> Tuple[str, str]:
    """Check that the bytes on disk are what the extension claims. Returns (format, kind) or raises a user-facing error."""
    claimed = check_extension(extension)
    with open(path, "rb") as handle:
        head = handle.read(8192)
    if not head:
        raise corrupted("The file is empty. Check that the export finished and upload it again.")
    for magic, label in _FORBIDDEN.items():
        if head.startswith(magic):
            raise unsupported(f"This file's content is {label}, not a {claimed.upper()} file. Only the listed data and document formats can be uploaded. {READABLE_HINT}")
    if claimed == "pdf":
        if b"%PDF-" not in head[:1024]:
            raise corrupted("This file does not look like a PDF (the PDF header is missing). It may be damaged, or have the wrong extension.")
    elif claimed in ("docx", "xlsx", "ods"):
        if not head.startswith(ZIP_MAGIC):
            raise unsupported(f"This file is not a real .{claimed} file (the content is not in that format). Open it in its program and save or export it again.")
        required = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "ods": "content.xml"}[claimed]
        archive = safe_zip(path)
        try:
            if required not in archive.namelist():
                raise unsupported(f"This file is a compressed package but not a .{claimed} document (it is missing {required}).")
            if claimed == "ods" and read_member(archive, "mimetype", 1000).strip() != b"application/vnd.oasis.opendocument.spreadsheet":
                raise unsupported("This OpenDocument file is not a spreadsheet.")
        finally:
            archive.close()
    elif claimed == "xls":
        if head.startswith(ZIP_MAGIC):
            raise unsupported("This file is a newer Excel workbook saved with the .xls extension. Rename it to .xlsx and upload it again.")
        if not head.startswith(OLE_MAGIC):
            raise unsupported("This file is not a real .xls workbook (the content is not in that format).")
    elif claimed == "rtf":
        if not head.lstrip().startswith(b"{\\rtf"):
            raise unsupported("This file is not a real RTF document (the RTF header is missing).")
    elif claimed == "json":
        if head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] not in (b"{", b"["):
            raise corrupted("This file is not valid JSON: it should start with { or [.")
    elif claimed == "xml":
        if not head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
            raise corrupted("This file is not valid XML: it should start with a < tag.")
    else:  # csv, txt, md
        if head.startswith(ZIP_MAGIC) or head.startswith(OLE_MAGIC) or head.startswith(b"%PDF-"):
            raise unsupported(f"This file's content is not plain text, so it cannot be read as .{extension}. Export it as a text file and try again.")
        if not _looks_text(head):
            raise unsupported(f"This file contains binary data, so it cannot be read as .{extension}. Export it as plain text and try again.")
    return claimed, FORMATS[claimed][0]


__all__ = ["validate_content", "check_extension", "safe_zip", "read_member", "kind_of", "SUPPORTED_EXTENSIONS"]
