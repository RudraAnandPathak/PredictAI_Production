"""Input rules for emails, passwords and display names. Pure functions."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from .errors import validation_failed

Problem = Dict[str, Any]

EMAIL_MAX = 254
PASSWORD_MIN = 10
PASSWORD_MAX = 128
DISPLAY_NAME_MAX = 60

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_EMAIL = re.compile(rf"^[A-Za-z0-9.!#$%&'*+/=?^_`{{|}}~-]+@{_LABEL}(?:\.{_LABEL})+$")

# Most common passwords that are long enough to pass the length rule, plus simple patterns.
COMMON_PASSWORDS = frozenset("""
password12 password123 password1234 password12345 passw0rd123 p@ssword123 p@ssw0rd123 qwertyuiop qwerty1234
qwerty12345 qwerty123456 qwertyuiop1 1qaz2wsx3edc 1q2w3e4r5t 1q2w3e4r5t6y 123456789a 1234567890 12345678910
0123456789 9876543210 abcdefghij abcd123456 abc1234567 abcdef1234 iloveyou123 iloveyou12 welcome1234 welcome123
letmein1234 letmein123 administrator admin12345 admin123456 changeme123 changeme1234 trustno1234 monkey1234
dragon12345 football123 baseball123 sunshine123 princess123 superman123 michael1234 master12345 shadow12345
starwars123 whatever123 freedom123 computer123 internet123 predictai123 predictai1234 predictai12345
""".split())
_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890")


def normalize_email(raw: Any) -> Optional[str]:
    """Lower-cased address if it is syntactically acceptable, otherwise None."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or len(value) > EMAIL_MAX or not _EMAIL.match(value):
        return None
    local, _, domain = value.rpartition("@")
    if len(local) > 64 or ".." in local or local.startswith(".") or local.endswith("."):
        return None
    if not re.search(r"\.[A-Za-z]{2,}$", domain):
        return None
    return f"{local}@{domain}".lower()


def clean_display_name(raw: Any) -> Optional[str]:
    """Trimmed single-spaced name, or None when empty. Raises ValueError when invalid."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("Display name must be text.")
    value = " ".join(unicodedata.normalize("NFKC", raw).split())
    if not value:
        return None
    if len(value) > DISPLAY_NAME_MAX:
        raise ValueError(f"Display name can be at most {DISPLAY_NAME_MAX} characters.")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValueError("Display name contains characters that are not allowed.")
    return value


def _is_trivial(lowered: str) -> bool:
    if len(set(lowered)) <= 3:
        return True
    for row in _KEYBOARD_ROWS:
        for candidate in (row, row[::-1]):
            if len(lowered) <= len(candidate) + 2 and lowered in candidate + candidate[:2]:
                return True
    digits = [ord(ch) for ch in lowered]
    steps = {b - a for a, b in zip(digits, digits[1:])}
    return steps in ({1}, {-1})


def password_problems(
    password: Any, *, email: Optional[str] = None, display_name: Optional[str] = None, field: str = "password"
) -> List[Problem]:
    """Every rule the password breaks (empty list = acceptable). No composition rules."""
    if not isinstance(password, str):
        return [{"field": field, "issue": "invalid", "message": "Password is required."}]
    value = unicodedata.normalize("NFKC", password)
    problems: List[Problem] = []

    def add(issue: str, message: str) -> None:
        problems.append({"field": field, "issue": issue, "message": message})

    if len(value) < PASSWORD_MIN:
        add("too_short", f"Use at least {PASSWORD_MIN} characters.")
    if len(value) > PASSWORD_MAX:
        add("too_long", f"Use at most {PASSWORD_MAX} characters.")
    lowered = value.lower()
    if lowered in COMMON_PASSWORDS or (len(value) >= PASSWORD_MIN and _is_trivial(lowered)):
        add("too_common", "Choose something less common or predictable.")
    local = (email or "").split("@")[0].lower()
    if len(local) >= 4 and local in lowered:
        add("contains_email", "Do not include your email name in your password.")
    if display_name and len(display_name) >= 4 and display_name.lower() in lowered:
        add("contains_name", "Do not include your name in your password.")
    return problems


def require_valid_password(password: Any, **context: Any) -> None:
    problems = password_problems(password, **context)
    if problems:
        raise validation_failed(problems)
