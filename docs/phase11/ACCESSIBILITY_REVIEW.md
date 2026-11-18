# Accessibility review

**Status: partial, static only. No browser, screen reader, keyboard walk-through or zoom test was run** (none is available in the build sandbox). Nothing here is a claim of WCAG conformance. What follows is what source analysis and a computed contrast check can show, one finding that was fixed, and the procedure to finish the audit on a Mac.

## What was measured (offline)
| Check | Method | Result |
|---|---|---|
| Colour contrast of design tokens, both themes | `scripts/a11y_contrast.py` (WCAG 2.1 formula) → `test_p11_accessibility` | **74/74 pairs pass** after the fix below: text and status colours ≥ 4.5:1 on all four surfaces; focus ring and control borders ≥ 3:1 |
| Clickable `div`/`span` (not keyboard-reachable) | source scan | 1 hit: the modal scrim, which is `aria-hidden` and has a Close button and Escape handling; no others |
| Focus outline removed without replacement | CSS scan | none; global `:focus-visible` ring exists; 3 `outline: none` rules are focus-target `main` (tabIndex −1) and chart marks that have their own `:focus-visible` style |
| Page language | `index.html` | `lang="en"` |
| Images without alt | source scan | no `<img>` in the app; 9 inline SVGs, 21 `aria-hidden`/`role="img"` markers |
| Reduced motion | CSS | `prefers-reduced-motion` handled in `base.css` and 3 module stylesheets |
| Form field wiring | `components/forms/Field.jsx` (188 uses) sets `aria-invalid` and `aria-describedby`; 69 raw inputs elsewhere, 8 without an `id`/`aria-label` are checkboxes wrapped in `<label>` | no unlabeled control found by this heuristic |
| Landmarks and headings | source counts | `<main>`, `<nav>`, skip link present; 36 `<h1>`; tables use `<th scope>` (61) and 16 `<caption>` |
| Live regions | source count | 54 `aria-live`/`role=status|alert` |
| Tabs / dialog semantics | source | `role=tab/tablist/tabpanel` (6), `aria-selected`; one modal with `role=dialog`/`aria-modal` |

These are **counts of mechanisms present**, not proof they behave correctly.

## Finding fixed
**A-1 (WCAG 1.4.11 Non-text contrast) — fixed.** The boundary of text inputs, ghost buttons and segmented controls used `--border-strong`, measured at **1.5–1.9:1** in both themes (needs 3:1 where the border is what identifies the control). Added `--control-border` (dark `#6e6e8a`, light `#80809a`; ≥ 3.4:1 on every surface) and applied it to `.field__input`, `.btn--ghost`, `.seg`. Decorative borders are unchanged. Regression-guarded by `test_p11_accessibility`. **The visual result has not been looked at in a browser** — expect slightly stronger input outlines.

## Known gaps (not fixed, not hidden)
- **A-2 Large single JavaScript bundle** is a performance, not an accessibility, issue — see `PERFORMANCE_BASELINE.md`.
- **A-3 Charts** are SVG with focusable marks and tooltips; whether a screen reader announces them usefully, and whether every chart has a data-table alternative, **was not verified**. The analytics screens offer data export and drill-down rows, which helps but is not a tested equivalent.
- **A-4 Drag-and-drop / drop zone** has a file input fallback (`sr-only` input with a description); keyboard behaviour **not tested**.
- **A-5 Touch-target size, responsive layout, 200–400 % zoom reflow, and Windows High Contrast** — **not tested**.
- **A-6 Consistent UX / beginner-friendliness** (brief 4.1–4.2) was not evaluated with people.

## Procedure to finish on the Mac (≈ 1–2 h)
1. `npm run dev`, open Chrome; install the axe DevTools or run Lighthouse (Accessibility) on: sign-in, projects, a dataset page, an analysis with a chart, predictive training result, Research Intelligence Home/Discover/Assistant/Reports, Owner dashboard. Record each violation with its page.
2. **Keyboard only** (no mouse): complete sign-in, upload a file, run an analysis, send an assistant question, export a report. Focus must always be visible, never trapped (except inside the open modal), and Escape must close the modal and return focus to its trigger.
3. **VoiceOver** (Cmd+F5): navigate by headings and landmarks on the same pages; confirm form errors are announced, status changes (job finished, export ready) are announced, and a chart has an understandable text equivalent.
4. **Zoom** to 200 % and 400 %, and set the window to ~320 px wide: no horizontal scrolling of the page, nothing clipped.
5. System **Reduce Motion** on: confirm animations stop. Toggle light/dark and re-check contrast of any colour not from tokens (charts, badges).
6. Add each finding to this file with its status; this gate stays "Not tested" until steps 1–5 are recorded.
