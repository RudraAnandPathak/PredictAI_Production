"""Optical character recognition through the free local `tesseract` and `pdftoppm` programs (no cloud service, no cost).
Programs are called with argument lists (never a shell) and only ever see server-generated temp paths."""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

PAGE_TIMEOUT = 90
_LANG = re.compile(r"^[a-z]{3}(_[A-Za-z]+)?(\+[a-z]{3}(_[A-Za-z]+)?)*$")


def available() -> bool:
    return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


def engine_version() -> str:
    exe = shutil.which("tesseract")
    if not exe:
        return "unavailable"
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0].strip()[:60]
    except Exception:
        return "unknown"


def ocr_pdf_page(pdf: Path, page_number: int, languages: str, dpi: int = 200) -> Dict[str, object]:
    """Render one PDF page and recognise it. Returns {text, confidence (0-100 mean of word confidences) or None, words}."""
    if not _LANG.match(languages):
        languages = "eng"
    with tempfile.TemporaryDirectory(prefix="pai-ocr-") as tmp:
        stem = str(Path(tmp) / "pg")
        subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(page_number), "-l", str(page_number), "-png", "-singlefile", str(pdf), stem],
                       capture_output=True, timeout=PAGE_TIMEOUT, check=True)
        return ocr_image(Path(stem + ".png"), languages)


def ocr_image(image: Path, languages: str) -> Dict[str, object]:
    done = subprocess.run(["tesseract", str(image), "stdout", "-l", languages, "tsv"], capture_output=True, text=True, timeout=PAGE_TIMEOUT, check=True)
    reader = csv.DictReader(io.StringIO(done.stdout), delimiter="\t", quoting=csv.QUOTE_NONE)
    lines: Dict[tuple, List[str]] = {}
    confidences: List[float] = []
    for row in reader:
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            conf = -1
        if not text or conf < 0:
            continue
        confidences.append(conf)
        lines.setdefault((row.get("block_num"), row.get("par_num"), row.get("line_num")), []).append(text)
    text_out = "\n".join(" ".join(words) for words in lines.values())
    mean = round(sum(confidences) / len(confidences), 1) if confidences else None
    return {"text": text_out, "confidence": mean, "words": len(confidences)}
