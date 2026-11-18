"""Offline performance baseline. Run:  PYTHONPATH=<fastapi or stand-in>:.:tests python3 tests/perf_baseline.py [--json OUT]

Everything is MEASURED on the machine that runs it, with synthetic data, in-process (no network, no browser). Numbers from a different machine,
a real uvicorn server, a real browser, or a larger load are different numbers; this file never extrapolates."""
import argparse
import csv
import itertools
import json
import os
import platform
import resource
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, "tests")
import test_identity_api as base
from p10_support import RIHarness
from app.modules.research_intelligence.reasoning.analysis import AnalysisService
from app.modules.research_intelligence.reports import export as exporter
from app.modules.research_intelligence.reports.service import ReportService
from app.modules.workspace import profiling
from app.modules.workspace.parsing.datasets import read_csv
from app.ops import backup
from test_p10_reasoning import S1, S2, S3


def pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def stats_ms(xs):
    return {"n": len(xs), "p50_ms": round(pct(xs, 0.5) * 1000, 2), "p95_ms": round(pct(xs, 0.95) * 1000, 2), "max_ms": round(max(xs) * 1000, 2)}


def timed(fn, n):
    out = []
    for _ in range(n):
        t = time.perf_counter(); fn(); out.append(time.perf_counter() - t)
    return out


def rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--json"); a = ap.parse_args()
    res = {"environment": {"platform": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count(), "note": "in-process test client over the FastAPI STAND-IN (real Starlette), SQLite on local disk, synthetic data"}}
    # ---- API latency and concurrency
    tmp = tempfile.TemporaryDirectory()
    api = base.Api(ri_root=os.path.join(tmp.name, "ri"))
    api.signed_in()
    api.email, api.password = "alice@example.com", base.GOOD_PASSWORD if hasattr(base, 'GOOD_PASSWORD') else None
    hdr = {"X-CSRF-Token": api.csrf}
    wid = api.call("POST", "/research-intelligence/workspaces", {"name": "Perf"}, headers=hdr).json()["data"]["id"]
    lat = {}
    for name, path in (("GET /health", "/health"), ("GET /research-intelligence/limits", "/research-intelligence/limits"), ("GET /research-intelligence/workspaces", "/research-intelligence/workspaces"),
                       ("GET /research-intelligence/home", "/research-intelligence/home"), ("GET /projects", "/projects")):
        lat[name] = stats_ms(timed(lambda p=path: api.call("GET", p, headers=hdr), 100))
    lat["POST note (write)"] = stats_ms(timed(lambda: api.call("POST", f"/research-intelligence/workspaces/{wid}/notes", {"body": "n"}, headers=hdr), 100))
    res["api_latency_sequential"] = lat

    def worker(results, i):
        client = api.browser()
        r = api.call("POST", "/auth/login", {"email": api.email, "password": api.password}, csrf=False, client=client) if hasattr(api, "email") else None
        tok = r.json()["data"]["csrf_token"] if r is not None and r.status_code == 200 else None
        for _ in range(25):
            t = time.perf_counter()
            if tok is None:
                results.append((0, time.perf_counter() - t)); continue
            h = {"X-CSRF-Token": tok}
            rr = api.call("GET", "/research-intelligence/workspaces", headers=h, csrf=False, client=client)
            results.append((rr.status_code, time.perf_counter() - t))
            t = time.perf_counter()                                  # time the write on its own, not together with the read before it
            rr = api.call("POST", f"/research-intelligence/workspaces/{wid}/notes", {"body": "c"}, headers=h, csrf=False, client=client)
            results.append((rr.status_code, time.perf_counter() - t))
    if hasattr(api, "email"):
        conc = {}
        for threads in (1, 4, 8):
            results = []
            ts = [threading.Thread(target=worker, args=(results, i)) for i in range(threads)]
            t0 = time.perf_counter(); [t.start() for t in ts]; [t.join() for t in ts]; wall = time.perf_counter() - t0
            codes = {}
            for c, _ in results: codes[c] = codes.get(c, 0) + 1
            conc[f"{threads} concurrent users"] = {"requests": len(results), "wall_s": round(wall, 2), "req_per_s": round(len(results) / wall, 1), "status_codes": codes, **stats_ms([d for _, d in results])}
        res["api_concurrency"] = conc
    else:
        res["api_concurrency"] = "not measured: the test client does not expose the account credentials"
    api.close(); tmp.cleanup()

    # ---- documents and research pipeline
    t = RIHarness(); user = t.user("perf@example.com"); w = t.workspace(user, "Perf")
    big = (S1 + "\n\n") * 250                                              # ~ hundreds of passages of realistic prose
    t0 = time.perf_counter(); src_big = t.add_text(user, w, "big.txt", big); ingest = time.perf_counter() - t0
    for n, txt in (("a.txt", S1), ("b.txt", S2), ("c.txt", S3)):
        t.add_text(user, w, n, txt)
    chunks = t.ctx().conn.execute("SELECT COUNT(*) FROM ri_chunks").fetchone()[0]
    search = t.search()
    q = timed(lambda: search.search(user, w, "canopy cooling surface temperature", mode="hybrid"), 50)
    t0 = time.perf_counter(); made = AnalysisService(t.ctx()).create(user, w, source_ids=[s["id"] for s in t.sources().list(user, w)["items"][1:4]]); t.run_jobs(); analysis = time.perf_counter() - t0
    status = AnalysisService(t.ctx()).get(user, w, made["id"])["status"]
    assert status == "succeeded", f"analysis ended {status}; the timing would be meaningless"
    rs = ReportService(t.ctx())
    t0 = time.perf_counter(); rep = rs.create(user, w, analysis_id=made["id"], report_type="academic"); build = time.perf_counter() - t0
    exports = {}
    for fmt in ("md", "docx", "pdf", "pptx"):
        t0 = time.perf_counter(); data, _, _ = rs.export(user, w, rep["id"], fmt); exports[fmt] = {"seconds": round(time.perf_counter() - t0, 3), "bytes": len(data)}
    res["research"] = {"synthetic_document_chars": len(big), "ingest_seconds": round(ingest, 3), "chunks_in_workspace": chunks, "hybrid_search": stats_ms(q),
                       "analysis_of_3_sources_seconds": round(analysis, 3), "report_build_seconds": round(build, 3), "exports": exports}
    live = t.h.settings
    t.ctx().conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    out = tempfile.mkdtemp()
    t0 = time.perf_counter(); m = backup.create_backup(live.database_path, live.ri_root, out); bt = time.perf_counter() - t0
    t0 = time.perf_counter(); backup.restore_backup(str(Path(out) / m["backup_id"]), str(Path(out) / "restored")); rt = time.perf_counter() - t0
    res["backup_restore"] = {"files": m["counts"]["files"], "bytes": m["counts"]["bytes"], "backup_seconds": round(bt, 3), "restore_and_verify_seconds": round(rt, 3)}
    t.close()

    # ---- tabular data
    ds = {}
    for rows in (1_000, 100_000):
        p = Path(tempfile.mkdtemp()) / "d.csv"
        with open(p, "w", newline="") as f:
            w_ = csv.writer(f); w_.writerow(["id", "group", "x", "y", "note"])
            for i in range(rows):
                w_.writerow([i, f"g{i % 7}", i * 0.5, (i * 37) % 101, f"row {i}"])
        rss0 = rss_mb(); t0 = time.perf_counter()
        table = read_csv(p)
        prof = profiling._profile_rows(itertools.chain([table.header], table.rows()), [{"name": h, "type": "text"} for h in table.header])
        ds[f"csv_{rows}_rows"] = {"file_mb": round(p.stat().st_size / 1e6, 2), "read_and_profile_seconds": round(time.perf_counter() - t0, 3), "rows_profiled": prof["dataset"]["rows"], "peak_rss_mb_after": rss_mb()}
    res["tabular"] = ds
    res["process_peak_rss_mb"] = rss_mb()
    print(json.dumps(res, indent=2))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
