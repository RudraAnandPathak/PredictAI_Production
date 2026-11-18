"""Discovery service: search several academic providers at once, merge duplicates, rank, remember the search, keep a personal library and import papers into workspaces.

Everything is owner-scoped. The browser never sends bibliographic metadata back: "save" names a search and a result id, and the server copies the record it stored, so a saved
paper's title, authors and DOI always come from a provider's answer.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...identity.authorization import Principal, require
from ...identity.constants import Perm
from ...identity.context import Context
from ...p7 import choice
from ...workspace.errors import conflict, invalid, not_found
from ..core.common import record_usage
from ..security.urlguard import SafeFetcher
from .providers import GATE, PROVIDER_ORDER, Provider, ProviderGate, ProviderResult, Query, default_providers
from .records import PUB_TYPES, Record, author_matches, merge_records, normalise_doi, rank_records, terms

SORTS = ("relevance", "date", "citations")
MAX_STORED = 150
EV_SEARCH = "ri.discovery.searched"
EV_SAVED = "ri.discovery.saved"
EV_IMPORTED = "ri.discovery.imported"


def _text(value: Any, name: str, maximum: int = 300) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise invalid(name, "Enter text.")
    v = re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()
    if len(v) > maximum:
        raise invalid(name, f"Use at most {maximum} characters.")
    return v


def _year(value: Any, name: str) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        y = int(value)
    except (TypeError, ValueError):
        raise invalid(name, "Enter a four-digit year.") from None
    if not 1000 <= y <= 2200:
        raise invalid(name, "Enter a four-digit year.")
    return y


class DiscoveryService:
    def __init__(self, ctx: Context, *, fetcher: Any = None, providers: Optional[Dict[str, Provider]] = None, gate: Optional[ProviderGate] = None, searx_fetcher_factory: Any = None) -> None:
        self.ctx, self.conn = ctx, ctx.conn
        self._fetcher, self.providers, self.gate = fetcher, providers or default_providers(searx_fetcher_factory), gate or GATE

    # ------------------------------------------------------------------ helpers
    def fetcher(self) -> Any:
        if self._fetcher is not None:
            return self._fetcher
        s = self.ctx.settings
        mail = (s.ri_crossref_mailto or "").strip()
        ua = "PredictAI-Research/1.0 (" + (f"mailto:{mail}" if mail else "self-hosted research assistant") + ")"
        return SafeFetcher(user_agent=ua, timeout=float(s.ri_discovery_timeout_s), max_bytes=6 * 1024 * 1024, allow_private=False)

    def enabled(self) -> List[str]:
        return [k for k in self.ctx.settings.ri_discovery_providers.split(",") if k in self.providers]

    def provider_info(self, principal: Principal) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        import os
        on = set(self.enabled())
        items = []
        for k in PROVIDER_ORDER:
            p = self.providers.get(k)
            if p is None:
                continue
            d = p.describe(self.ctx.settings)
            d["enabled"] = k in on
            d["key_configured"] = {"openalex": bool(os.environ.get("PREDICTAI_RI_OPENALEX_API_KEY", "").strip()),
                                   "semanticscholar": bool(os.environ.get("PREDICTAI_RI_SEMANTIC_SCHOLAR_API_KEY", "").strip())}.get(k)
            items.append(d)
        return {"items": items, "types": list(PUB_TYPES), "sorts": list(SORTS),
                "note": "Your search words are sent to each enabled provider (and nothing else). Providers differ in coverage and in what they report; duplicates are merged."}

    def _query(self, body: Dict[str, Any]) -> Tuple[Query, List[str]]:
        q = Query(q=_text(body.get("query"), "query", 400), title=_text(body.get("title"), "title"), author=_text(body.get("author"), "author", 120), field=_text(body.get("field"), "field", 80),
                  pub_type=_text(body.get("type"), "type", 30), open_access=bool(body.get("open_access")), sort=_text(body.get("sort"), "sort", 20) or "relevance",
                  year_from=_year(body.get("year_from"), "year_from"), year_to=_year(body.get("year_to"), "year_to"), limit=self.ctx.settings.ri_discovery_per_provider)
        doi = _text(body.get("doi"), "doi", 200)
        if doi:
            q.doi = normalise_doi(doi) or ""
            if not q.doi:
                raise invalid("doi", "That is not a DOI. A DOI looks like 10.1000/xyz123.")
        if not (q.q or q.title or q.author or q.doi):
            raise invalid("query", "Enter search words, a title, an author or a DOI.")
        if q.pub_type:
            choice(q.pub_type, "type", PUB_TYPES)
        choice(q.sort, "sort", SORTS)
        if q.year_from and q.year_to and q.year_from > q.year_to:
            raise invalid("year_to", "The end year must not be before the start year.")
        wanted = body.get("providers")
        if wanted is None:
            keys = self.enabled()
        else:
            if not isinstance(wanted, list) or not all(isinstance(x, str) for x in wanted) or len(wanted) > 10:
                raise invalid("providers", "Choose providers from the list.")
            keys = [k for k in dict.fromkeys(wanted)]
            bad = [k for k in keys if k not in self.providers]
            if bad:
                raise invalid("providers", f"Unknown provider: {bad[0]}.")
            keys = [k for k in PROVIDER_ORDER if k in keys]
        if not keys:
            raise invalid("providers", "No search provider is enabled.")
        return q, keys

    # ------------------------------------------------------------------ search
    def search(self, principal: Principal, body: Dict[str, Any], workspace_id: Any = None) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        self.ctx.limiter.hit("ri_discover:user", principal.user_id)
        if not isinstance(body, dict):
            raise invalid("body", "Send the search as JSON.")
        q, keys = self._query(body)
        wid = None
        if workspace_id:
            r = self.conn.execute("SELECT id FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
            if r is None:
                raise not_found("research workspace")
            wid = r["id"]
        fetcher = self.fetcher()
        settings = self.ctx.settings
        with ThreadPoolExecutor(max_workers=max(1, len(keys))) as pool:
            futures = [(k, pool.submit(self.providers[k].run, fetcher, q, settings, self.gate)) for k in keys]
            results: List[ProviderResult] = [f.result() for _, f in futures]
        merged = merge_records([(r.provider, r.records) for r in results if r.status == "ok"])
        kept, filter_notes = self._post_filter(merged, {"author": q.author, "year_from": q.year_from, "year_to": q.year_to, "type": q.pub_type, "field": q.field, "open_access": q.open_access})
        ranked = rank_records(kept, q.terms(), sort=q.sort)[:MAX_STORED]
        stored = [{"record": rec.to_dict(), "score": score, "reasons": reasons} for rec, score, reasons in ranked]
        sid, now = str(uuid.uuid4()), self.ctx.clock.iso()
        query_json = {"query": q.q, "title": q.title, "author": q.author, "doi": q.doi, "year_from": q.year_from, "year_to": q.year_to, "field": q.field, "type": q.pub_type,
                      "open_access": q.open_access, "sort": q.sort, "providers": keys}
        statuses = [r.public() for r in results]
        self.conn.execute("INSERT INTO ri_discovery_searches (id, owner_user_id, workspace_id, query, providers, total, results, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (sid, principal.user_id, wid, json.dumps(query_json), json.dumps(statuses), len(stored), json.dumps(stored), now))
        self._trim_history(principal.user_id)
        record_usage(self.ctx, principal.user_id, wid, "discovery", 1, ",".join(keys))
        self.ctx.audit.record(EV_SEARCH, "success", actor=principal.user_id, metadata={"providers": keys, "results": len(stored), "ok": sum(1 for r in results if r.status == "ok")})
        page = self._page(principal.user_id, stored, 1, 20, {}, q.sort)
        return {"search_id": sid, "query": query_json, "providers": statuses, "notes": filter_notes + self._notes(results, stored), "created_at": now, **page}

    @staticmethod
    def _notes(results: Sequence[ProviderResult], stored: List[Dict[str, Any]]) -> List[str]:
        out: List[str] = []
        ok = [r for r in results if r.status == "ok"]
        if not ok:
            out.append("No provider answered, so there are no results. See each provider's message below.")
        elif not stored:
            out.append("The providers that answered found nothing for this search. Try fewer filters or different words.")
        failed = [r.label for r in results if r.status not in ("ok", "skipped")]
        if failed and ok:
            out.append("Some providers did not answer (" + ", ".join(failed) + "), so these results may be incomplete.")
        for r in ok:
            out.extend(r.notes)
        return list(dict.fromkeys(out))

    def _post_filter(self, records: List[Record], f: Dict[str, Any]) -> Tuple[List[Record], List[str]]:
        """Filters the providers could not all apply themselves. A result that lacks the information a filter needs is hidden and counted, never guessed."""
        notes: List[str] = []
        out = list(records)
        def apply(label: str, pred) -> None:                                                           # noqa: ANN001
            nonlocal out
            keep = [r for r in out if pred(r)]
            if len(keep) != len(out):
                notes.append(f"{len(out) - len(keep)} result(s) were hidden by the {label} filter or lack that information.")
            out = keep
        if f.get("author"):
            apply("author", lambda r: author_matches(r, f["author"]))
        if f.get("year_from"):
            apply("start-year", lambda r: r.year is not None and r.year >= f["year_from"])
        if f.get("year_to"):
            apply("end-year", lambda r: r.year is not None and r.year <= f["year_to"])
        if f.get("type"):
            apply("type", lambda r: r.pub_type == f["type"])
        if f.get("field"):
            fl = f["field"].lower()
            apply("field", lambda r: any(fl in x.lower() for x in r.fields))
        if f.get("open_access"):
            apply("open-access", lambda r: bool(r.oa_url))
        return out, notes

    def _trim_history(self, user_id: str) -> None:
        keep = self.ctx.settings.ri_discovery_history_max
        self.conn.execute("DELETE FROM ri_discovery_searches WHERE owner_user_id = ? AND id NOT IN (SELECT id FROM ri_discovery_searches WHERE owner_user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?)",
                          (user_id, user_id, keep))

    # ------------------------------------------------------------------ results of a stored search (paging, filtering, sorting without asking the providers again)
    def _row(self, principal: Principal, search_id: Any) -> sqlite3.Row:
        r = self.conn.execute("SELECT * FROM ri_discovery_searches WHERE id = ? AND owner_user_id = ?", (search_id if isinstance(search_id, str) else "", principal.user_id)).fetchone()
        if r is None:
            raise not_found("search")
        return r

    def _page(self, user_id: str, stored: List[Dict[str, Any]], page: int, size: int, flt: Dict[str, Any], sort: str) -> Dict[str, Any]:
        items = stored
        if flt.get("provider"):
            items = [x for x in items if flt["provider"] in x["record"]["providers"]]
        if flt.get("year_from"):
            items = [x for x in items if x["record"]["year"] and x["record"]["year"] >= flt["year_from"]]
        if flt.get("year_to"):
            items = [x for x in items if x["record"]["year"] and x["record"]["year"] <= flt["year_to"]]
        if flt.get("type"):
            items = [x for x in items if x["record"]["type"] == flt["type"]]
        if flt.get("open_access"):
            items = [x for x in items if x["record"]["oa_url"]]
        if flt.get("has_abstract"):
            items = [x for x in items if x["record"]["abstract"]]
        if flt.get("within"):
            ts = terms(flt["within"])
            items = [x for x in items if all(t in set(terms(x["record"]["title"] + " " + (x["record"]["abstract"] or ""))) for t in ts)]
        if sort == "date":
            items = sorted(items, key=lambda x: (x["record"]["published"] or "", x["score"]), reverse=True)
        elif sort == "citations":
            items = sorted(items, key=lambda x: (x["record"]["citation_count"] if x["record"]["citation_count"] is not None else -1, x["score"]), reverse=True)
        elif sort == "relevance":
            items = sorted(items, key=lambda x: -x["score"])
        pages = max(1, -(-len(items) // size))
        page = max(1, min(page, pages))
        window = items[(page - 1) * size: page * size]
        saved = {r["dedupe_key"] for r in self.conn.execute("SELECT dedupe_key FROM ri_library WHERE owner_user_id = ?", (user_id,)).fetchall()}
        out = []
        for x in window:
            rec = dict(x["record"])
            out.append({**rec, "score": x["score"], "why": x["reasons"], "saved": Record.from_dict(rec).key in saved})
        return {"items": out, "page": page, "page_size": size, "pages": pages, "total": len(items), "total_unfiltered": len(stored)}

    def get_search(self, principal: Principal, search_id: Any, *, page: Any = 1, page_size: Any = 20, sort: Any = None, **flt: Any) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        r = self._row(principal, search_id)
        q = json.loads(r["query"])
        try:
            page, size = int(page), int(page_size)
        except (TypeError, ValueError):
            raise invalid("page", "Use whole numbers for the page and page size.") from None
        size = max(5, min(size, 50))
        sort = sort or q.get("sort") or "relevance"
        choice(sort, "sort", SORTS)
        clean = {"provider": _text(flt.get("provider"), "provider", 30), "year_from": _year(flt.get("year_from"), "year_from"), "year_to": _year(flt.get("year_to"), "year_to"),
                 "type": _text(flt.get("type"), "type", 30), "open_access": bool(flt.get("open_access")), "has_abstract": bool(flt.get("has_abstract")), "within": _text(flt.get("within"), "within", 200)}
        return {"search_id": r["id"], "query": q, "providers": json.loads(r["providers"]), "created_at": r["created_at"], "filters": {k: v for k, v in clean.items() if v}, **self._page(principal.user_id, json.loads(r["results"]), page, size, clean, sort)}

    # ------------------------------------------------------------------ history
    def history(self, principal: Principal, limit: Any = 30) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        n = max(1, min(int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 30, 100))
        rows = self.conn.execute("SELECT id, query, providers, total, created_at FROM ri_discovery_searches WHERE owner_user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?", (principal.user_id, n)).fetchall()
        return {"items": [{"id": r["id"], "query": json.loads(r["query"]), "total": r["total"], "providers": [{"provider": p["provider"], "status": p["status"], "count": p["count"]} for p in json.loads(r["providers"])],
                           "created_at": r["created_at"]} for r in rows]}

    def delete_search(self, principal: Principal, search_id: Any) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        r = self._row(principal, search_id)
        self.conn.execute("DELETE FROM ri_discovery_searches WHERE id = ? AND owner_user_id = ?", (r["id"], principal.user_id))
        return {"deleted": True, "id": r["id"]}

    def clear_history(self, principal: Principal) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        n = self.conn.execute("DELETE FROM ri_discovery_searches WHERE owner_user_id = ?", (principal.user_id,)).rowcount
        return {"deleted": n}

    # ------------------------------------------------------------------ library
    def _lib_present(self, r: sqlite3.Row, *, detail: bool = True) -> Dict[str, Any]:
        rec = json.loads(r["record"])
        used = [dict(x) for x in self.conn.execute("SELECT l.workspace_id, l.source_id, w.name AS workspace_name FROM ri_library_links l JOIN rw_workspaces w ON w.id = l.workspace_id "
                                                   "WHERE l.library_id = ? AND l.owner_user_id = ?", (r["id"], r["owner_user_id"])).fetchall()]
        out = {**rec, "library_id": r["id"], "saved_at": r["created_at"], "in_workspaces": used}
        if not detail:
            out.pop("abstract", None)
        return out

    def save(self, principal: Principal, search_id: Any, rid: Any) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        row = self._row(principal, search_id)
        hit = next((x for x in json.loads(row["results"]) if x["record"].get("rid") == rid), None)
        if hit is None:
            raise not_found("search result")
        rec = Record.from_dict(hit["record"])
        existing = self.conn.execute("SELECT * FROM ri_library WHERE owner_user_id = ? AND dedupe_key IN (%s) LIMIT 1" % ",".join("?" * len(rec.dedupe_keys())),
                                     (principal.user_id, *rec.dedupe_keys())).fetchone() if rec.dedupe_keys() else None
        if existing is not None:
            return {**self._lib_present(existing), "already_saved": True}
        lid, now = str(uuid.uuid4()), self.ctx.clock.iso()
        self.conn.execute("INSERT INTO ri_library (id, owner_user_id, dedupe_key, title, year, doi, record, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (lid, principal.user_id, rec.key, rec.title[:400], rec.year, rec.doi, json.dumps(hit["record"]), now))
        self.ctx.audit.record(EV_SAVED, "success", actor=principal.user_id, metadata={"library_id": lid})
        return {**self._lib_present(self.conn.execute("SELECT * FROM ri_library WHERE id = ?", (lid,)).fetchone()), "already_saved": False}

    def library(self, principal: Principal, *, query: Any = None, limit: Any = 100, offset: Any = 0) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        lim = max(1, min(int(limit) if str(limit).isdigit() else 100, 200))
        off = max(0, int(offset) if str(offset).isdigit() else 0)
        rows = self.conn.execute("SELECT * FROM ri_library WHERE owner_user_id = ? ORDER BY created_at DESC, id", (principal.user_id,)).fetchall()
        items = [self._lib_present(r, detail=False) for r in rows]
        if isinstance(query, str) and query.strip():
            ts = terms(query)
            items = [i for i in items if all(t in set(terms(i["title"] + " " + " ".join(i.get("authors") or []))) for t in ts)]
        return {"items": items[off: off + lim], "total": len(items)}

    def library_get(self, principal: Principal, library_id: Any) -> Dict[str, Any]:
        return self._lib_present(self._lib_row(principal, library_id))

    def _lib_row(self, principal: Principal, library_id: Any) -> sqlite3.Row:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        r = self.conn.execute("SELECT * FROM ri_library WHERE id = ? AND owner_user_id = ?", (library_id if isinstance(library_id, str) else "", principal.user_id)).fetchone()
        if r is None:
            raise not_found("saved paper")
        return r

    def library_remove(self, principal: Principal, library_id: Any) -> Dict[str, Any]:
        r = self._lib_row(principal, library_id)
        self.ctx.limiter.hit("ri_write:user", principal.user_id)
        used = self.conn.execute("SELECT COUNT(*) FROM ri_library_links WHERE library_id = ? AND owner_user_id = ?", (r["id"], principal.user_id)).fetchone()[0]
        self.conn.execute("DELETE FROM ri_library_links WHERE library_id = ? AND owner_user_id = ?", (r["id"], principal.user_id))
        self.conn.execute("DELETE FROM ri_library WHERE id = ? AND owner_user_id = ?", (r["id"], principal.user_id))
        return {"deleted": True, "id": r["id"], "workspace_sources_kept": used}            # sources already imported into workspaces are separate and stay until deleted there

    # ------------------------------------------------------------------ compare
    def compare(self, principal: Principal, library_ids: Any) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        if not isinstance(library_ids, list) or not 2 <= len(library_ids) <= 6 or not all(isinstance(x, str) for x in library_ids):
            raise invalid("ids", "Choose between 2 and 6 saved papers to compare.")
        rows = [self._lib_row(principal, i) for i in dict.fromkeys(library_ids)]
        if len(rows) < 2:
            raise invalid("ids", "Choose at least two different papers.")
        recs = [(r["id"], json.loads(r["record"])) for r in rows]
        bag = {i: Counter(terms(r["title"] + " " + (r["abstract"] or ""))) for i, r in recs}
        shared = set.intersection(*(set(c) for c in bag.values())) if bag else set()
        items = []
        for i, r in recs:
            others = set().union(*(set(c) for j, c in bag.items() if j != i))
            unique = [w for w, _ in bag[i].most_common() if w not in others][:10]
            items.append({"library_id": i, "title": r["title"], "authors": r["authors"][:6], "year": r["year"], "venue": r["venue"], "type": r["type"], "doi": r["doi"], "access": r["access"],
                          "citation_count": r["citation_count"], "citation_source": r["citation_source"], "has_abstract": bool(r["abstract"]), "distinct_terms": unique})
        years = [r["year"] for _, r in recs if r["year"]]
        return {"items": items, "shared_terms": sorted(shared, key=lambda w: -sum(c[w] for c in bag.values()))[:15], "year_span": [min(years), max(years)] if years else None,
                "note": "This compares titles, abstracts and bibliographic data only. Citation counts come from different providers and are not comparable across them. Import the papers into a workspace to compare their full text."}

    # ------------------------------------------------------------------ import into a workspace
    def import_to_workspace(self, principal: Principal, workspace_id: Any, library_id: Any, full_text: bool, sources: Any) -> Dict[str, Any]:
        row = self._lib_row(principal, library_id)
        rec = json.loads(row["record"])
        if rec.get("kind") == "web" and not rec.get("url"):
            raise invalid("library_id", "This result has no address to import.")
        out = sources.add_discovered(principal, workspace_id, rec, full_text=bool(full_text), library_id=row["id"])
        self.ctx.audit.record(EV_IMPORTED, "success", actor=principal.user_id, metadata={"library_id": row["id"], "workspace_id": workspace_id, "full_text_requested": bool(full_text)})
        return out
