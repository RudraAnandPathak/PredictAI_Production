"""XML hardening shared by the XLSX, ODS, DOCX and XML parsers. Documents that declare a DTD or entities are refused
outright (that is how entity-expansion and external-entity attacks work); nothing is resolved from disk or the network."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import IO, Iterator, Tuple

from ..errors import corrupted

_DANGEROUS = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
MAX_DEPTH = 200


def ensure_safe_xml(head: bytes) -> None:
    if _DANGEROUS.search(head):
        raise corrupted("The file declares custom XML entities or a DTD, which PredictAI refuses to process for safety.")


def parse_bytes(data: bytes) -> ET.Element:
    ensure_safe_xml(data[:65536] if len(data) > 65536 else data)
    if _DANGEROUS.search(data):
        ensure_safe_xml(data)
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError, RecursionError) as error:
        raise corrupted("The file contains malformed XML, so it could not be read. Re-export it from the original program.") from error


def iterparse(stream: IO[bytes], events=("start", "end")) -> Iterator[Tuple[str, ET.Element]]:
    """Streaming parse. The first chunk is checked for DTD/entity declarations (they must precede the root element)."""
    first = stream.read(65536)
    ensure_safe_xml(first)
    import io

    chained = _Chain(first, stream)
    depth = 0
    try:
        for event, element in ET.iterparse(chained, events=events):
            if event == "start":
                depth += 1
                if depth > MAX_DEPTH:
                    raise corrupted("The file is nested too deeply and was rejected for safety.")
            elif event == "end":
                depth -= 1
            yield event, element
    except ET.ParseError as error:
        raise corrupted("The file contains malformed XML, so it could not be read. Re-export it from the original program.") from error
    except RecursionError as error:
        raise corrupted("The file is nested too deeply and was rejected for safety.") from error


class _Chain:
    def __init__(self, head: bytes, rest: IO[bytes]) -> None:
        self.head, self.rest = head, rest

    def read(self, size: int = -1) -> bytes:
        if self.head:
            if size is None or size < 0 or size >= len(self.head):
                out, self.head = self.head, b""
                return out + (self.rest.read(size - len(out)) if size and size > 0 else self.rest.read())
            out, self.head = self.head[:size], self.head[size:]
            return out
        return self.rest.read(size)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
