#!/usr/bin/env python3
"""Check the REAL providers from this machine (run it yourself; the build sandbox had no network, so this was never run against the live services).

    cd backend && python ../scripts/live_research_check.py

It sends only fixed public phrases ("Louis XVI", an arXiv title) and prints, per provider, one of: OK, UNAVAILABLE (could not be reached, which is not the same as "no results"),
or WRONG (reached, but the answer was not what the question needs, e.g. Louis XVIII for Louis XVI). The exit code is 1 when anything is WRONG, 2 when only UNAVAILABLE."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.core.config import get_settings                                    # noqa: E402
from app.modules.webaccess.providers import PROVIDERS                      # noqa: E402
from app.modules.webaccess.service import build_fetcher                    # noqa: E402


def main() -> int:
    settings = get_settings()
    fetcher = build_fetcher(settings)
    wrong = down = 0

    def report(label, state, detail=""):
        nonlocal wrong, down
        wrong += state == "WRONG"
        down += state == "UNAVAILABLE"
        print(f"{state:<12} {label}  {detail}")

    for key, provider in PROVIDERS.items():
        ok, why = provider.configured(settings)
        if not ok:
            report(key, "UNAVAILABLE", f"not configured: {why}")
            continue
        try:
            d = provider.diagnose(fetcher, settings)
            report(f"{key} connection", "OK" if d.get("ok") else "UNAVAILABLE", d.get("message", d.get("status", "")))
        except Exception as e:                                               # noqa: BLE001
            report(f"{key} connection", "UNAVAILABLE", str(e)[:160])

    def search(key, query, check, label, **kw):
        p = PROVIDERS.get(key)
        if p is None or not p.configured(settings)[0]:
            return
        try:
            hits = p.search(fetcher, settings, query, 5, **kw)
        except Exception as e:                                               # noqa: BLE001
            report(label, "UNAVAILABLE", f"{getattr(e, 'code', type(e).__name__)}: {str(e)[:120]}")
            return
        titles = [h.title for h in hits]
        report(label, "OK" if check(titles) else "WRONG", " | ".join(titles[:4]) or "(no results)")

    search("wikipedia", "Louis XVI", lambda t: bool(t) and t[0].strip() == "Louis XVI", "Wikipedia exact title 'Louis XVI' is the first result, not Louis XVIII", exact=True)
    search("wikipedia", "causes French Revolution", lambda t: any("French Revolution" in x for x in t), "Wikipedia finds the French Revolution article")
    search("arxiv", 'ti:"Attention Is All You Need"', lambda t: any("Attention Is All You Need" in x for x in t), "arXiv finds a paper by its exact title")
    search("searxng", "Louis XVI king of France", lambda t: len(t) > 0, "SearXNG returns results (JSON format must be enabled in its settings.yml)")
    return 1 if wrong else 2 if down else 0


if __name__ == "__main__":
    sys.exit(main())
