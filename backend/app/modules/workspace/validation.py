"""Input cleaning for user-supplied text and file names. File names are display metadata only: they never
become storage paths."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, Optional, Tuple

from .constants import MAX_DESCRIPTION, MAX_FILENAME, MAX_NAME
from .errors import invalid

_CONTROL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")
_FILENAME_BAD = re.compile(r'[<>:"|?*\\/\x00-\x1f\x7f​-‏‪-‮⁦-⁩]')


def clean_text(value: Any, field: str, *, maximum: int, required: bool, multiline: bool = False) -> Optional[str]:
    if value is None:
        if required:
            raise invalid(field, "This field is required.", "required")
        return None
    if not isinstance(value, str):
        raise invalid(field, "Enter text.")
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]", "", text)
    if not multiline:
        text = _CONTROL.sub(" ", text)
    text = text.strip()
    if not text:
        if required:
            raise invalid(field, "This field is required.", "required")
        return None
    if len(text) > maximum:
        raise invalid(field, f"Use {maximum} characters or fewer.", "too_long")
    return text


def clean_project_name(value: Any) -> str:
    return clean_text(value, "name", maximum=MAX_NAME, required=True) or ""


def clean_description(value: Any) -> Optional[str]:
    return clean_text(value, "description", maximum=MAX_DESCRIPTION, required=False, multiline=True)


def clean_metadata(value: Any) -> Optional[str]:
    """Small flat JSON object of short strings, numbers, booleans and lists of short strings (tags)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise invalid("metadata", "Metadata must be an object.")
    if len(value) > 20:
        raise invalid("metadata", "Use 20 metadata entries or fewer.", "too_long")
    cleaned: Dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", key):
            raise invalid("metadata", "Metadata keys may use letters, digits, '_', '-' and '.' (40 characters at most).")
        if isinstance(item, bool) or item is None or isinstance(item, (int, float)):
            cleaned[key] = item
        elif isinstance(item, str) and len(item) <= 200:
            cleaned[key] = _CONTROL.sub(" ", item).strip()
        elif isinstance(item, list) and len(item) <= 20 and all(isinstance(t, str) and len(t) <= 40 for t in item):
            cleaned[key] = [_CONTROL.sub(" ", t).strip() for t in item]
        else:
            raise invalid("metadata", f"Metadata value for '{key}' is not allowed (use short text, numbers, true/false or a short list of tags).")
    text = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
    if len(text) > 4000:
        raise invalid("metadata", "Metadata is too large.", "too_long")
    return text


def sanitize_filename(raw: Any) -> Tuple[str, str]:
    """Return (display name, lower-case extension without dot). Directory parts, control and bidi characters are removed."""
    if not isinstance(raw, str):
        raise invalid("filename", "A file name is required.", "required")
    name = unicodedata.normalize("NFC", raw).replace("\\", "/").split("/")[-1]
    name = _FILENAME_BAD.sub("", name).strip().strip(".").strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        raise invalid("filename", "The file name is empty or not allowed.")
    if len(name) > MAX_FILENAME:
        stem, dot, ext = name.rpartition(".")
        keep = MAX_FILENAME - (len(ext) + 1 if dot else 0)
        name = (stem[:keep] + "." + ext) if dot else name[:MAX_FILENAME]
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    return name, extension
