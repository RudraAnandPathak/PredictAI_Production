"""Static accessibility checks that need no browser: colour-token contrast (WCAG 2.1 AA) and a few structural rules in the source.
These do not replace a screen-reader / keyboard / zoom pass in a real browser (see docs/phase11/ACCESSIBILITY_REVIEW.md)."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("a11y_contrast", ROOT / "scripts" / "a11y_contrast.py")
a11y = importlib.util.module_from_spec(spec); spec.loader.exec_module(a11y)
SRC = ROOT / "frontend" / "src"


class Contrast(unittest.TestCase):
    def test_both_themes_are_found(self):
        self.assertEqual(sorted(a11y.themes()), ["dark", "light"])

    def test_every_text_and_control_pair_meets_wcag_aa(self):
        failing = [r for r in a11y.check() if not r["pass"]]
        self.assertEqual(failing, [], failing)

    def test_the_checker_really_fails_a_low_contrast_pair(self):
        self.assertLess(a11y.ratio("#777777", "#888888"), 3.0)
        self.assertAlmostEqual(a11y.ratio("#000000", "#ffffff"), 21.0, places=1)

    def test_inputs_and_ghost_buttons_use_the_control_border_not_the_decorative_one(self):
        css = "\n".join(p.read_text() for p in (SRC / "design").glob("*.css"))
        self.assertRegex(css, r"\.field__input \{[^}]*var\(--control-border\)")
        self.assertRegex(css, r"\.btn--ghost \{[^}]*var\(--control-border\)")


class Structure(unittest.TestCase):
    def test_no_clickable_div_or_span_except_the_modal_scrim(self):
        hits = []
        for p in SRC.rglob("*.jsx"):
            for n, line in enumerate(p.read_text().splitlines(), 1):
                if re.search(r"<(div|span)[^>]*onClick", line) and 'aria-hidden="true"' not in line:
                    hits.append(f"{p.relative_to(ROOT)}:{n}")
        self.assertEqual(hits, [])

    def test_no_stylesheet_removes_the_focus_outline_without_a_replacement(self):
        for p in (SRC / "design").glob("*.css"):
            for n, line in enumerate(p.read_text().splitlines(), 1):
                if re.search(r"outline:\s*none", line):
                    self.assertTrue("tabindex" in line or "chart__" in line or ".auth__main" in line, f"{p.name}:{n} {line.strip()[:80]}")   # auth__main is a programmatic focus target (tabIndex=-1), not a control

    def test_the_page_declares_its_language_and_every_image_has_alt_text(self):
        self.assertIn('<html lang="en">', (ROOT / "frontend" / "index.html").read_text())
        for p in SRC.rglob("*.jsx"):
            for tag in re.findall(r"<img\b[^>]*>", p.read_text()):
                self.assertIn("alt=", tag, p.name)

    def test_motion_is_reduced_on_request(self):
        self.assertIn("prefers-reduced-motion", (SRC / "design" / "base.css").read_text())


if __name__ == "__main__":
    unittest.main()
