"""The shape of what the Phase 6 API returns, checked against real examples shared with the frontend (shared/contracts/phase6.*.example.json).

The examples are real service responses, trimmed (lists cut to a few items). If a field the interface reads changes name or kind, this test fails, and so does the frontend test that reads
the same files. Adding new fields is always allowed. To refresh the examples after an intended change run:  PREDICTAI_WRITE_CONTRACTS=1 python -m unittest test_phase6_contract"""
import json
import os
import unittest
from pathlib import Path

from test_predictive_contract import differences
from test_workspace_uploads import upload
from world_support import WorldBase, item

CONTRACTS = Path(__file__).resolve().parents[2] / "shared" / "contracts"
WRITE = os.environ.get("PREDICTAI_WRITE_CONTRACTS") == "1"


def trim(value, items=3):
    if isinstance(value, list):
        return [trim(v, items) for v in value[:items]]
    if isinstance(value, dict):
        return {k: trim(v, items) for k, v in value.items()}
    return value


class Phase6Contracts(WorldBase):
    def collect(self):
        out = {}
        alice, pid = self.alice, self.pid
        out["research-capabilities"] = self.svc.capabilities(alice, pid)
        out["research-settings"] = self.svc.settings(alice, pid)
        doc = self.add_pdf("river.pdf", ["The river rose by 2.4 metres on Tuesday after heavy rainfall.\nOfficials evacuated 1,200 residents from low lying areas.\nThe levee held at the northern bridge."])
        s = self.create("How high did the river rise after the heavy rainfall?", documents=[doc])
        out["research-session"] = s
        done = self.run_to_end(s["id"])
        out["research-job"] = done
        bundle = self.svc.get_bundle(alice, done["result_id"])
        out["research-bundle"] = bundle
        out["research-citation"] = self.svc.inspect_citation(alice, done["result_id"], bundle["bundle"]["evidence"][0]["id"])
        job2 = self.svc.run(alice, s["id"], None)
        self.runtime.runner.wait(job2["id"], 60)
        out["research-compare"] = self.svc.compare(alice, done["result_id"], self.svc.job(alice, job2["id"])["result_id"])
        self.svc.add_note(alice, s["id"], {"body": "Ask the council about the levee"})
        out["research-notes"] = self.svc.list_notes(alice, s["id"])
        out["research-sessions"] = self.svc.list_sessions(alice, pid)
        out["research-sources"] = self.svc.list_sources(alice, pid)
        # news
        self.allow_news()
        for o, title, summary in (("a", "River floods northern village", "The river rose by 2.4 metres and 1,200 residents were evacuated."),
                                  ("b", "Floods hit northern village as river rises", "Officials evacuated 1,200 residents after the river rose 2.4 metres."),
                                  ("c", "River flood: village evacuated", "The river rose 2.4 metres and 1,200 residents left the village.")):
            self.add_source(self.feed(f"/{o}.xml", [item(title, summary, when=self.days_ago(1), base=f"https://{o}.example")], f"Outlet {o.upper()}"), o.upper(), publisher=f"Outlet {o.upper()}")
        out["world-sources-before"] = self.world.list_sources(alice, pid)
        ingest = self.ingest()
        out["world-ingest-job"] = ingest
        out["world-overview"] = self.world.overview(alice, pid)
        out["world-sources"] = self.world.list_sources(alice, pid)
        arts = self.world.list_articles(alice, pid)
        out["world-articles"] = arts
        out["world-article"] = self.world.get_article(alice, arts["items"][0]["id"])
        evs = self.world.list_events(alice, pid)
        out["world-events"] = evs
        out["world-event"] = self.world.get_event(alice, evs["items"][0]["id"])
        out["world-event-summary"] = self.world.event_summary(alice, evs["items"][0]["id"])
        out["world-topics"] = self.world.topics(alice, pid)
        out["world-trends"] = self.world.trends(alice, pid, 7)
        out["world-freshness"] = self.world.freshness(alice, pid)
        # influence on a short planted dataset
        import random
        from datetime import timedelta
        rng = random.Random(4)
        per_day, items = {}, []
        for d in range(40):
            per_day[d] = rng.randint(0, 5)
            for k in range(per_day[d]):
                items.append(item(f"Harbour report d{d} k{k} unique{d}x{k}", f"Dredging update number {d} {k} with words alpha{d}{k} beta{d}{k}.", when=self.days_ago(40 - d - 0.3), base="https://h.example", guid=f"g{d}-{k}"))
        self.add_source(self.feed("/long.xml", items), "Harbour", publisher="Harbour", policy={"item_cap": 100})
        self.ingest()
        base = (self.now - timedelta(days=40)).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = ["day,value"] + [f"{(base + timedelta(days=d)).strftime('%Y-%m-%d')},{(per_day[d - 2] * 3.0 if d >= 2 else 0.0) + rng.random() * 0.4:.4f}" for d in range(40)]
        asset = upload(self.ws, alice, pid, "sales.csv", ("\n".join(rows) + "\n").encode())["asset"]
        job = self.world.start_influence(alice, pid, {"asset_id": asset["id"], "date_column": "day", "value_column": "value", "max_lag": 5, "label": "Sales vs harbour coverage"})
        self.runtime.runner.wait(job["id"], 120)
        done = self.svc.job(alice, job["id"])
        out["world-influence-job"] = done
        out["world-influence"] = self.world.get_influence(alice, done["result_id"])
        out["world-influence-list"] = self.world.list_influence(alice, pid)
        out["evidence-for-consumer"] = self.rs.evidence.evidence_for_project(alice, pid)
        out["exam-capabilities"] = self.rs.exam.capabilities()
        out["registry-capabilities"] = self.rs.registry.capabilities()
        return {k: json.loads(json.dumps(trim(v), default=str)) for k, v in out.items()}

    def test_the_examples_match_what_the_services_return(self):
        got = self.collect()
        CONTRACTS.mkdir(parents=True, exist_ok=True)
        if WRITE:
            for name, value in got.items():
                (CONTRACTS / f"phase6.{name}.example.json").write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        problems = []
        for name, value in got.items():
            path = CONTRACTS / f"phase6.{name}.example.json"
            self.assertTrue(path.exists(), f"missing contract example {path.name}; run with PREDICTAI_WRITE_CONTRACTS=1")
            expected = json.loads(path.read_text(encoding="utf-8"))
            problems += [f"{name} {d}" for d in differences(expected, value)]
        self.assertEqual(problems, [])
        stale = {p.name for p in CONTRACTS.glob("phase6.*.example.json")} - {f"phase6.{n}.example.json" for n in got}
        self.assertEqual(stale, set(), "contract examples that no test produces any more")


if __name__ == "__main__":
    unittest.main()
