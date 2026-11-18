#!/usr/bin/env python3
"""WCAG 2.1 contrast check of the design tokens (frontend/src/design/tokens.css), both themes. Offline, stdlib only.
Text pairs need 4.5:1 (AA, normal text); focus ring and the accent used as an interface boundary need 3:1 (AA, non-text).
Measures colour tokens only: it cannot see components that override colours, images, or anything rendered in a browser.

    python3 scripts/a11y_contrast.py [--json]"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "design" / "tokens.css"
TEXT = ["--text", "--text-muted", "--text-faint", "--accent", "--warm", "--ok", "--danger"]     # used as text colours
SURFACES = ["--bg", "--bg-elevated", "--surface", "--surface-2"]
NON_TEXT = ["--focus", "--control-border"]      # --border-strong is decorative (panels, badges); inputs, ghost buttons and segmented controls use --control-border


def lum(h: str) -> float:
    h = h.lstrip("#"); r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def themes() -> dict[str, dict[str, str]]:
    css = CSS.read_text(); out = {}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        toks = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", m.group(2)))
        selector = m.group(1).split("*/")[-1]                      # ignore the file's leading comment
        if "--bg" in toks: out["light" if "data-theme='light'" in selector else "dark"] = toks
    return out


def check() -> list[dict]:
    rows = []
    for name, t in themes().items():
        for fg in TEXT:
            for bg in SURFACES:
                if fg in t and bg in t: rows.append({"theme": name, "fg": fg, "bg": bg, "ratio": round(ratio(t[fg], t[bg]), 2), "need": 4.5, "kind": "text"})
        for fg in NON_TEXT:
            for bg in SURFACES:
                if fg in t and bg in t: rows.append({"theme": name, "fg": fg, "bg": bg, "ratio": round(ratio(t[fg], t[bg]), 2), "need": 3.0, "kind": "non-text"})
        if "--accent-contrast" in t: rows.append({"theme": name, "fg": "--accent-contrast", "bg": "--accent-strong", "ratio": round(ratio(t["--accent-contrast"], t["--accent-strong"]), 2), "need": 4.5, "kind": "button text"})
    for r in rows: r["pass"] = r["ratio"] >= r["need"]
    return rows


if __name__ == "__main__":
    rows = check()
    if "--json" in sys.argv: print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            if not r["pass"]: print(f"FAIL {r['theme']:5} {r['fg']:18} on {r['bg']:14} {r['ratio']:5} < {r['need']}  ({r['kind']})")
        print(f"{sum(r['pass'] for r in rows)}/{len(rows)} pairs meet WCAG 2.1 AA ({', '.join(sorted(themes()))} themes)")
    raise SystemExit(0 if all(r["pass"] for r in rows) else 1)
