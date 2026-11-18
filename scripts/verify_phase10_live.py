#!/usr/bin/env python3
"""Phase 10 live verification. Run this on YOUR machine (the build sandbox had no network, so none of these live checks has ever been run by the people who wrote the code).

    cd backend
    PYTHONPATH=. python ../scripts/verify_phase10_live.py            # everything that can run
    PYTHONPATH=. python ../scripts/verify_phase10_live.py --only providers,qdrant,flow
    PYTHONPATH=. python ../scripts/verify_phase10_live.py --json report.json

Each check prints PASS, FAIL, or SKIP with the reason (a missing key, a server that is not running). SKIP is not PASS: a skipped check stays UNVERIFIED. Exit code: 0 when nothing FAILED,
1 when something FAILED. Send the whole output back so the status documents can be updated with real results.

What it sends to the outside world: only fixed public search phrases to the academic providers (the same ones the application uses), and throw-away test vectors to YOUR Qdrant under a
temporary collection that it deletes afterwards. It sends no document of yours anywhere, and it never prints an API key.

Environment it reads (all optional; missing ones make the matching check SKIP):
    PREDICTAI_RI_OPENALEX_API_KEY, PREDICTAI_RI_SEMANTIC_SCHOLAR_API_KEY, PREDICTAI_RI_CROSSREF_MAILTO (a real address, used as the polite-pool contact),
    PREDICTAI_RI_QDRANT_URL (+ PREDICTAI_RI_QDRANT_API_KEY), PREDICTAI_AI_BASE_URL (default http://127.0.0.1:11434), PREDICTAI_AI_MODEL, PREDICTAI_AI_EMBED_MODEL."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

RESULTS: List[Dict[str, str]] = []


def record(group: str, name: str, state: str, detail: str = "") -> None:
    RESULTS.append({"group": group, "check": name, "state": state, "detail": detail})
    print(f"{state:<5} [{group}] {name}" + (f"  - {detail}" if detail else ""), flush=True)


class Skip(Exception):
    pass


def check(group: str, name: str, fn: Callable[[], str]) -> None:
    try:
        record(group, name, "PASS", fn() or "")
    except Skip as e:
        record(group, name, "SKIP", str(e))
    except AssertionError as e:
        record(group, name, "FAIL", str(e) or "assertion failed")
    except Exception as e:                                                         # noqa: BLE001 - report, never crash the whole run
        record(group, name, "FAIL", f"{type(e).__name__}: {str(e)[:300]}")
        if os.environ.get("VERIFY_TRACE"):
            traceback.print_exc()


def http_json(method: str, url: str, body: Optional[dict] = None, headers: Optional[Dict[str, str]] = None, timeout: float = 15.0) -> Tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:                    # noqa: S310 - the URL comes from the operator's own configuration
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise Skip(f"cannot reach {url.split('?')[0]}: {getattr(e, 'reason', e)}")


# ------------------------------------------------------------------------------------------------ providers (R2)
def providers() -> None:
    from app.core.config import get_settings
    from app.modules.research_intelligence.discovery.providers import GATE, Query, default_providers
    from app.modules.research_intelligence.discovery.records import Record
    from app.modules.research_intelligence.security.urlguard import SafeFetcher
    settings = get_settings()
    fetcher = SafeFetcher(allow_private=False, max_bytes=8 * 1024 * 1024, timeout=20.0)
    all_p = default_providers()
    q = Query(q="urban tree canopy surface temperature", limit=5)

    def run_once(key: str, query: "Query"):
        res = all_p[key].run(fetcher, query, settings, GATE)
        if res.status == "rate_limited" and (res.retry_after or 0) <= 5:          # our own polite pacing: wait the interval and ask again once
            time.sleep((res.retry_after or 1.0) + 0.2)
            res = all_p[key].run(fetcher, query, settings, GATE)
        return res
    for key in ("crossref", "openalex", "arxiv", "semanticscholar"):
        def one(key=key) -> str:
            res = run_once(key, q)
            if res.status in ("skipped", "needs_key"):
                raise Skip(f"{res.message} {res.fix}".strip())
            if res.status == "rate_limited":
                raise Skip("rate limited by the provider (its own limit, or our polite interval); wait a minute and run again")
            if res.status in ("unreachable", "unavailable"):
                raise Skip(f"{res.status}: {res.message}")
            assert res.status == "ok", f"status {res.status}: {res.message} {res.fix}"
            assert res.records, "reached the provider but got no records for a common query"
            r: Record = res.records[0]
            assert r.title, "first record has no title"
            return f"{len(res.records)} records; first: {r.title[:70]!r} ({r.year}) doi={r.doi or '-'}"
        check("providers", f"{key} search", one)

    def doi_lookup() -> str:
        res = run_once("crossref", Query(doi="10.1038/nature14539"))
        if res.status != "ok":
            raise Skip(f"crossref {res.status}: {res.message}")
        assert res.records and (res.records[0].doi or "").lower() == "10.1038/nature14539", "DOI lookup did not return the requested DOI"
        return f"resolved {res.records[0].title[:60]!r}"
    check("providers", "crossref DOI lookup (10.1038/nature14539)", doi_lookup)


# ------------------------------------------------------------------------------------------------ Qdrant
def qdrant() -> None:
    base = (os.environ.get("PREDICTAI_RI_QDRANT_URL") or "").rstrip("/")
    key = os.environ.get("PREDICTAI_RI_QDRANT_API_KEY") or None
    headers = {"api-key": key} if key else {}

    def reach() -> str:
        if not base:
            raise Skip("PREDICTAI_RI_QDRANT_URL is not set (start Qdrant, e.g. `docker run -p 6333:6333 qdrant/qdrant`, then set it)")
        status, _ = http_json("GET", f"{base}/collections", headers=headers)
        assert status == 200, f"GET /collections answered {status}"
        return base
    check("qdrant", "server reachable", reach)

    def roundtrip() -> str:
        if not base:
            raise Skip("PREDICTAI_RI_QDRANT_URL is not set")
        import sqlite3
        from app.modules.research_intelligence.retrieval.vectorstore import QdrantVectorStore, SqliteVectorStore
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""CREATE TABLE ri_embeddings (chunk_seq INTEGER NOT NULL, model TEXT NOT NULL, provider TEXT NOT NULL, workspace_id TEXT NOT NULL, source_id TEXT NOT NULL,
                              owner_user_id TEXT NOT NULL, dims INTEGER NOT NULL, vector BLOB NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (chunk_seq, model));""")
        coll = f"verify_{int(time.time())}"
        store = QdrantVectorStore(base, coll, ledger=SqliteVectorStore(conn, lambda: "2026-01-01T00:00:00Z"), api_key=key)
        model = "verify-model"
        try:
            items = [{"chunk_seq": 1, "source_id": "s1", "workspace_id": "w1", "owner_user_id": "u1", "model": model, "provider": "verify", "vector": [1.0, 0.0, 0.0, 0.0]},
                     {"chunk_seq": 2, "source_id": "s2", "workspace_id": "w1", "owner_user_id": "u1", "model": model, "provider": "verify", "vector": [0.0, 1.0, 0.0, 0.0]},
                     {"chunk_seq": 3, "source_id": "s3", "workspace_id": "w2", "owner_user_id": "u2", "model": model, "provider": "verify", "vector": [1.0, 0.0, 0.0, 0.0]}]
            store.upsert(items)
            hits = store.search(workspace_id="w1", owner_id="u1", model=model, vector=[1.0, 0.0, 0.0, 0.0], k=5, source_ids=None)
            assert hits and hits[0][0] == 1, f"nearest vector should be chunk 1, got {hits}"
            assert 3 not in [h[0] for h in hits], "ISOLATION FAILURE: another owner's vector was returned"
            only = store.search(workspace_id="w1", owner_id="u1", model=model, vector=[1.0, 0.0, 0.0, 0.0], k=5, source_ids=["s2"])
            assert [h[0] for h in only] == [2], f"source filter did not restrict results: {only}"
            store.delete_source(workspace_id="w1", owner_id="u1", source_id="s1")
            after = store.search(workspace_id="w1", owner_id="u1", model=model, vector=[1.0, 0.0, 0.0, 0.0], k=5, source_ids=None)
            assert 1 not in [h[0] for h in after], "deleted source's vector is still returned"
            return "upsert, filtered search, owner isolation, delete all behave"
        finally:
            for name in {store._coll(model)}:
                try:
                    http_json("DELETE", f"{base}/collections/{name}", headers=headers)
                except Skip:
                    pass
    check("qdrant", "store round trip (temporary collection, removed afterwards)", roundtrip)


# ------------------------------------------------------------------------------------------------ Ollama
def ollama() -> None:
    base = (os.environ.get("PREDICTAI_AI_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    chat, embed = os.environ.get("PREDICTAI_AI_MODEL"), os.environ.get("PREDICTAI_AI_EMBED_MODEL")

    def tags() -> str:
        status, data = http_json("GET", f"{base}/api/tags")
        assert status == 200 and isinstance(data, dict), f"/api/tags answered {status}"
        names = [str(m.get("name") or m.get("model")) for m in data.get("models", []) if isinstance(m, dict)]
        assert names, "Ollama is running but has no models installed (`ollama pull <model>`)"
        return f"{len(names)} model(s): {', '.join(names[:5])}"
    check("ollama", "server reachable, models installed", tags)

    def embedding() -> str:
        if not embed:
            raise Skip("PREDICTAI_AI_EMBED_MODEL is not set (e.g. `ollama pull nomic-embed-text`, then set it)")
        status, data = http_json("POST", f"{base}/api/embed", {"model": embed, "input": ["tree canopy cools cities", "shade lowers temperature"]}, timeout=120)
        assert status == 200 and isinstance(data, dict), f"/api/embed answered {status} (is '{embed}' pulled?)"
        vecs = data.get("embeddings")
        assert isinstance(vecs, list) and len(vecs) == 2 and vecs[0] and len(vecs[0]) == len(vecs[1]), "embeddings missing or inconsistent"
        return f"{embed}: 2 vectors of {len(vecs[0])} dimensions"
    check("ollama", "embeddings", embedding)

    def chat_check() -> str:
        if not chat:
            raise Skip("PREDICTAI_AI_MODEL is not set")
        status, data = http_json("POST", f"{base}/api/chat", {"model": chat, "stream": False, "messages": [{"role": "user", "content": "Reply with the single word: ready"}], "options": {"temperature": 0, "num_predict": 8}}, timeout=180)
        assert status == 200 and isinstance(data, dict), f"/api/chat answered {status} (is '{chat}' pulled?)"
        text = ((data.get("message") or {}).get("content") or "").strip()
        assert text, "the model returned no text"
        return f"{chat}: {text[:40]!r}"
    check("ollama", "chat", chat_check)


# ------------------------------------------------------------------------------------------------ application flow (live)
def app_flow() -> None:
    """The real services on a throw-away database, with the REAL network: discovery search -> save -> import -> worker -> multi-source analysis. No FastAPI needed."""
    sys.path.insert(0, os.path.join(ROOT, "backend", "tests"))
    try:
        from p10_support import RIHarness                                           # the same harness the unit tests use (real SQLite, real services)
    except Exception as e:                                                         # noqa: BLE001
        record("flow", "live discovery -> import -> analysis", "SKIP", f"test harness not importable: {type(e).__name__}: {e}")
        return
    from app.modules.research_intelligence.discovery.service import DiscoveryService
    from app.modules.research_intelligence.reasoning.analysis import AnalysisService
    state: Dict[str, object] = {}

    def run() -> str:
        t = RIHarness()
        state["t"] = t
        state["picked"] = 0
        user = t.user("verify@example.com")
        wid = t.workspace(user, "Live verification")
        disc = DiscoveryService(t.ctx())
        for attempt in (1, 2):
            out = disc.search(user, {"query": "urban tree canopy surface temperature"}, wid)
            statuses = {p.get("provider") or p.get("key"): p.get("status") for p in out.get("providers", [])}
            if attempt == 1 and "ok" not in statuses.values() and "rate_limited" in statuses.values():
                time.sleep(5)                                                       # the providers were just used by the checks above; their polite interval is a few seconds
                continue
            break
        usable = [k for k, v in statuses.items() if v == "ok"]
        if not usable:
            raise Skip(f"no provider answered (statuses: {statuses}); fix the network or keys and run again")
        assert out["items"], f"providers {usable} answered ok but the merged list is empty"
        picked = out["items"][:3]
        state["picked"] = len(picked)
        for it in picked:
            lib = disc.save(user, out["search_id"], it["rid"])
            disc.import_to_workspace(user, wid, lib["library_id"], False, t.sources())
        outcomes = t.run_jobs()
        assert outcomes and all(o == "succeeded" for o in outcomes), f"worker outcomes: {outcomes}"
        ready = [s for s in t.sources().list(user, wid, "ready")["items"]]
        assert len(ready) == len(picked), f"{len(ready)} of {len(picked)} imported sources are ready"
        return f"providers ok: {', '.join(usable)}; merged {len(out['items'])} results; imported {len(picked)} as abstract-only; worker {outcomes}"
    check("flow", "live discovery -> save -> import -> worker", run)

    def analysis_inline() -> str:
        t = state.get("t")
        if t is None:
            raise Skip("the previous step did not run")
        if state.get("picked", 0) < 2:
            raise Skip("fewer than two papers were imported in the previous step")
        user = t.user("verify@example.com")
        wid = t.ctx().conn.execute("SELECT id FROM rw_workspaces WHERE owner_user_id = ?", (user.user_id,)).fetchone()["id"]
        svc = AnalysisService(t.ctx())
        made = svc.create(user, wid)
        outcomes = t.run_jobs()
        got = svc.get(user, wid, made["id"])
        assert got["status"] == "succeeded", f"analysis ended {got['status']}: {got.get('error_code')} {got.get('error_message')}"
        res = got["result"]
        state["analysis"] = (user, wid, made["id"])
        return (f"{len(res['sources'])} sources compared (abstract-level evidence); {len(res['findings']['common'])} shared point(s), "
                f"{len(res['findings']['contradictions'])} possible contradiction(s); worker {outcomes}")
    check("flow", "multi-source analysis of the imported papers", analysis_inline)

    def report_exports() -> str:
        t = state.get("t")
        if t is None or "analysis" not in state:
            raise Skip("no finished analysis from the previous step")
        from app.modules.research_intelligence.reports.service import ReportService
        user, wid, aid = state["analysis"]
        rs = ReportService(t.ctx())
        rep = rs.create(user, wid, analysis_id=aid, report_type="literature_review")
        sizes = []
        for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK"), ("pptx", b"PK"), ("md", b"#")):
            data, _ctype, name = rs.export(user, wid, rep["id"], fmt)
            assert data.startswith(magic), f"{fmt} export does not look like a {fmt} file"
            sizes.append(f"{fmt} {len(data)} B")
        assert "peer reviewed" in rs.export(user, wid, rep["id"], "md")[0].decode(), "the not-peer-reviewed notice is missing from the export"
        return "report built from the live analysis and exported: " + ", ".join(sizes) + " (open the files yourself to judge layout)"
    check("flow", "report from the live analysis exports to PDF/DOCX/PPTX/Markdown", report_exports)
    if state.get("t") is not None:
        state["t"].close()


# ------------------------------------------------------------------------------------------------ frontend
def frontend() -> None:
    fe = os.path.join(ROOT, "frontend")

    def node_tests() -> str:
        if not shutil.which("node"):
            raise Skip("node is not installed")
        r = subprocess.run(["node", "--test"], cwd=fe, capture_output=True, text=True, timeout=300)
        tail = (r.stdout + r.stderr).strip().splitlines()[-12:]
        assert r.returncode == 0, "node --test failed:\n" + "\n".join(tail)
        pass_line = next((l for l in tail if l.strip().startswith("# pass")), "")
        return pass_line.strip("# ").strip() or "node --test passed"
    check("frontend", "unit tests (node --test)", node_tests)

    def build() -> str:
        if not shutil.which("npm"):
            raise Skip("npm is not installed")
        if not os.path.isdir(os.path.join(fe, "node_modules")):
            raise Skip("run `npm install` in frontend/ first")
        r = subprocess.run(["npm", "run", "build"], cwd=fe, capture_output=True, text=True, timeout=600)
        tail = (r.stdout + r.stderr).strip().splitlines()[-8:]
        assert r.returncode == 0, "vite build failed:\n" + "\n".join(tail)
        return "vite build succeeded"
    check("frontend", "production build (vite)", build)


GROUPS = {"providers": providers, "qdrant": qdrant, "ollama": ollama, "flow": app_flow, "frontend": frontend}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma list of: " + ", ".join(GROUPS))
    ap.add_argument("--json", help="also write the results to this file")
    args = ap.parse_args()
    chosen = [g.strip() for g in args.only.split(",")] if args.only else list(GROUPS)
    unknown = [g for g in chosen if g not in GROUPS]
    if unknown:
        print("unknown group(s): " + ", ".join(unknown))
        return 2
    print(f"PredictAI Phase 10 live verification - {time.strftime('%Y-%m-%d %H:%M:%S')} - python {sys.version.split()[0]}\n")
    for g in chosen:
        GROUPS[g]()
    counts = {s: sum(1 for r in RESULTS if r["state"] == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\nSummary: {counts['PASS']} PASS, {counts['FAIL']} FAIL, {counts['SKIP']} SKIP (skipped checks remain UNVERIFIED)")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"results": RESULTS, "summary": counts}, fh, indent=2)
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
