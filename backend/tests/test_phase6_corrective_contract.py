"""The shape of what the corrected Phase 6 research API returns, checked against real examples shared with the frontend (shared/contracts/phase6c.*.example.json).

These cover what the corrective work added or changed: a plan that would send phrases to several providers (one entry per phrase, with each provider's own wording), a follow-up session, the
combined report (calculated findings, Wikipedia, arXiv, web), citations that open an original or a calculation, and the provider test / search / open endpoints.
The examples are real service responses, trimmed. To refresh them after an intended change run:  PREDICTAI_WRITE_CONTRACTS=1 python -m unittest test_phase6_corrective_contract
The responses come from the in-process fake web (the same documented API shapes), not from the live services."""
import json
import os
import re
import unittest
from pathlib import Path

from research_combined_support import QUESTION, CombinedBase
from test_phase6_contract import trim
from test_predictive_contract import differences

CONTRACTS = Path(__file__).resolve().parents[2] / "shared" / "contracts"
WRITE = os.environ.get("PREDICTAI_WRITE_CONTRACTS") == "1"


class CorrectiveContracts(CombinedBase):
    def collect(self):
        out = {}
        air = self.airline()
        self.wikipedia()
        self.arxiv(relevant=True)
        self.searx([{"title": "Airline seat inventory", "url": self.aviation_page(), "content": "business class seats availability"}])
        self.allow()
        out["capabilities"] = self.svc.capabilities(self.alice, self.pid)
        draft = self.svc.create_session(self.alice, self.pid, {"question": QUESTION, "options": {"datasets": [air["id"]], "web": True, "providers": ["wikipedia", "arxiv", "searxng"]}})
        out["session-web"] = draft
        s, b = self.research(QUESTION, datasets=[air["id"]], web=True, providers=["wikipedia", "arxiv", "searxng"])
        out["bundle-combined"] = self.svc.get_bundle(self.alice, b["id"])
        wiki = next(e for e in b["evidence"] if str(e["location"].get("deep_url", "")).startswith("https://en.wikipedia.org/"))
        out["citation-wikipedia"] = self.svc.inspect_citation(self.alice, b["id"], wiki["id"])
        calc = next(e for e in b["evidence"] if e["citation"].get("kind") == "calculation")
        out["citation-calculation"] = self.svc.inspect_citation(self.alice, b["id"], calc["id"])
        out["follow-up"] = self.svc.create_follow_up(self.alice, s["id"], {"question": "What does the article say about fares?", "options": {"web": False}})
        out["providers-diagnose"] = self.svc.diagnose_providers(self.alice, self.pid, {})
        hits = self.svc.provider_search(self.alice, self.pid, {"provider": "wikipedia", "query": "business class"})
        out["providers-search"] = hits
        out["providers-open-wikipedia"] = self.svc.provider_open(self.alice, self.pid, {"provider": "wikipedia", "ref": {"title": "Business class"}})
        out["providers-open-arxiv"] = self.svc.provider_open(self.alice, self.pid, {"provider": "arxiv", "ref": {"arxiv_id": "2101.00001"}})
        return {k: json.loads(json.dumps(trim(v), default=str)) for k, v in out.items()}

    def test_the_examples_match_what_the_services_return(self):
        got = self.collect()
        CONTRACTS.mkdir(parents=True, exist_ok=True)
        if WRITE:
            for name, value in got.items():
                (CONTRACTS / f"phase6c.{name}.example.json").write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        problems = []
        for name, value in got.items():
            path = CONTRACTS / f"phase6c.{name}.example.json"
            self.assertTrue(path.exists(), f"missing contract example {path.name}; run with PREDICTAI_WRITE_CONTRACTS=1")
            expected = json.loads(path.read_text(encoding="utf-8"))
            problems += [f"{name} {d}" for d in differences(expected, value)]
        self.assertEqual(problems, [])
        stale = {p.name for p in CONTRACTS.glob("phase6c.*.example.json")} - {f"phase6c.{n}.example.json" for n in got}
        self.assertEqual(stale, set(), "contract examples that no test produces any more")

    def test_the_examples_carry_no_private_value_or_secret(self):
        text = " ".join(p.read_text(encoding="utf-8") for p in CONTRACTS.glob("phase6c.*.example.json"))
        for forbidden in ("ZXCANARY", "PREDICTAI_AI_KEY", "sk-", "Bearer "):
            self.assertNotIn(forbidden, text)
        addresses = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text))                             # no real person's address: only the reserved example domains may appear
        self.assertEqual({a for a in addresses if not re.search(r"@(?:[\w-]+\.)*example\.(?:com|org|net)$", a)}, set())


if __name__ == "__main__":
    unittest.main()
