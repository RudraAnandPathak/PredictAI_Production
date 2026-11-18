"""A stand-in Wikipedia, arXiv and SearXNG for the research-intelligence tests, answering in the documented response shapes from the modelled material in `history_corpus`.

The real provider code runs unchanged (query building, MediaWiki / arXiv / SearXNG requests, parsing, section reading, ranking, evidence, report); only the network is replaced. These services
are STAND-INS: they show how the engine treats answers of those shapes, not how the live services rank or answer. Where a live ranker could put the wrong page first, `ADVERSARIAL_ORDER` makes the
stand-in do exactly that, so the tests prove the engine does not rely on search order."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import history_corpus as C
from fixtures import pdf
from research_combined_support import FIXTURES, CombinedBase
from test_webaccess_arxiv import atom, feed
from test_webaccess_wikipedia import api, reply
from test_workspace_uploads import upload
from web_support import page

WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> List[str]:
    return WORD.findall((text or "").lower())


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def _params(handler) -> Dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(handler.path).query).items()}


# ---------------------------------------------------------------------------------------------------------------------------------------------- MediaWiki
class FakeMediaWiki:
    def __init__(self, pages: Optional[List[Dict[str, Any]]] = None, order: Optional[Dict[str, List[str]]] = None, redirects: Optional[Dict[str, str]] = None) -> None:
        self.pages = list(pages if pages is not None else C.ALL_PAGES)
        self.order = {k.lower(): v for k, v in (order if order is not None else C.ADVERSARIAL_ORDER).items()}
        self.redirects = dict(redirects if redirects is not None else C.REDIRECTS)
        self.by_title = {p["title"].lower(): p for p in self.pages}
        self.by_id = {p["pageid"]: p for p in self.pages}
        self.queries: List[str] = []
        self.titles_asked: List[str] = []                                                       # exact-title lookups (titles=), as opposed to searches

    def find(self, title: str) -> Optional[Dict[str, Any]]:
        t = re.sub(r"[\s_]+", " ", title or "").strip().lower()
        t = self.redirects.get(t, t).lower()
        return self.by_title.get(t)

    # ---- list=search
    def search(self, p: Dict[str, str]) -> Dict[str, Any]:
        q = re.sub(r"(intitle|incategory):", "", p.get("srsearch", "")).replace('"', " ")
        self.queries.append(p.get("srsearch", ""))
        key = " ".join(words(q))
        qw = set(words(q))
        scored = []
        for pg in self.pages:
            tw, ew = set(words(pg["title"])), set(words(pg["extract"]))
            score = 3 * len(qw & tw) + len(qw & ew)
            if score:
                scored.append((score, pg))
        scored.sort(key=lambda x: (-x[0], x[1]["pageid"]))
        ranked = [pg for _, pg in scored]
        forced = [self.by_title[t.lower()] for t in self.order.get(key, []) if t.lower() in self.by_title]
        results = forced + [pg for pg in ranked if pg not in forced] if forced else ranked
        limit = int(p.get("srlimit", 10))
        return {"batchcomplete": True, "query": {"searchinfo": {"totalhits": len(results)}, "search": [
            {"ns": 0, "title": pg["title"], "pageid": pg["pageid"], "size": pg["wordcount"] * 6, "wordcount": pg["wordcount"], "snippet": plain(pg["extract"])[:140], "timestamp": "2026-01-02T03:04:05Z"}
            for pg in results[:limit]]}}

    # ---- prop=extracts|info|pageprops|categories|revisions  (by pageids or by titles)
    def details(self, p: Dict[str, str]) -> Dict[str, Any]:
        out, redirected = [], []
        if p.get("pageids"):
            wanted = [self.by_id.get(int(i)) for i in p["pageids"].split("|") if i.isdigit()]
        else:
            wanted = []
            self.titles_asked += [t for t in (p.get("titles") or "").split("|") if t]
            for t in (p.get("titles") or "").split("|"):
                pg = self.find(t)
                if pg is None:
                    out.append({"ns": 0, "title": t, "missing": True})
                    continue
                if pg["title"].lower() != re.sub(r"[\s_]+", " ", t).strip().lower() and p.get("redirects"):
                    redirected.append({"from": t, "to": pg["title"]})
                wanted.append(pg)
        for pg in wanted:
            if pg is None:
                continue
            props = {"wikibase-shortdesc": pg["shortdesc"]}
            if pg["disambiguation"]:
                props["disambiguation"] = ""
            out.append({"pageid": pg["pageid"], "ns": 0, "title": pg["title"], "extract": plain(pg["extract"]), "fullurl": f"https://en.wikipedia.org/wiki/{pg['title'].replace(' ', '_')}",
                        "canonicalurl": f"https://en.wikipedia.org/wiki/{pg['title'].replace(' ', '_')}", "pageprops": props, "categories": [{"ns": 14, "title": f"Category:{c}"} for c in pg["categories"]],
                        "revisions": [{"revid": pg["revid"], "parentid": pg["revid"] - 1, "timestamp": "2026-03-04T05:06:07Z"}]})
        body: Dict[str, Any] = {"batchcomplete": True, "query": {"pages": out}}
        if redirected:
            body["query"]["redirects"] = redirected
        return body

    def links(self, p: Dict[str, str]) -> Dict[str, Any]:
        pg = self.find(p.get("titles", ""))
        links = []
        if pg and pg["disambiguation"]:
            links = [{"ns": 0, "title": t} for sec in pg["sections"] for t in sec[3]]
        return {"batchcomplete": True, "query": {"pages": [{"pageid": pg["pageid"] if pg else 0, "title": p.get("titles", ""), "links": links}]}}

    def parse(self, p: Dict[str, str]) -> Dict[str, Any]:
        pg = self.by_id.get(int(p["pageid"])) if p.get("pageid") else self.find(p.get("page", ""))
        if pg is None:
            return {"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}}
        return {"parse": {"title": pg["title"], "pageid": pg["pageid"], "revid": pg["revid"], "displaytitle": pg["title"], "text": pg["html"],
                          "categories": [{"sortkey": "", "category": c.replace(" ", "_"), "hidden": False} for c in pg["categories"]], "properties": {"wikibase-shortdesc": pg["shortdesc"]}}}

    def revisions(self, p: Dict[str, str]) -> Dict[str, Any]:
        rid = int(p.get("revids", 0) or 0)
        for pg in self.pages:
            if pg["revid"] == rid:
                return {"query": {"pages": [{"pageid": pg["pageid"], "title": pg["title"], "revisions": [{"revid": rid, "timestamp": "2026-03-04T05:06:07Z"}]}]}}
        return {"query": {"pages": []}}

    def route(self):
        return api({"search": self.search, "extracts": self.details, "links": self.links, "parse": self.parse, "revisions": self.revisions,
                    "siteinfo": lambda p: {"query": {"general": {"sitename": "Wikipedia"}}}})


# ---------------------------------------------------------------------------------------------------------------------------------------------- arXiv
TOKEN = re.compile(r'\(|\)|[A-Za-z]+:"[^"]*"|"[^"]*"|[A-Za-z]+:[^\s()]+|ANDNOT|AND|OR|[^\s()]+')


def _field_text(e: Dict[str, Any], field: str) -> str:
    title, summary = e["title"].lower(), e["summary"].lower()
    people = " ".join(e["authors"]).lower() + " " + (e.get("affil") or "").lower()
    return {"ti": title, "abs": summary, "au": people, "cat": " ".join(e["cats"]).lower(), "all": f"{title} {summary} {people}", "co": "", "jr": "", "id": e["id"].lower()}.get(field, f"{title} {summary} {people}")


def _atom(e: Dict[str, Any], field: str, value: str) -> bool:
    text = _field_text(e, field)
    v = value.strip('"').lower()
    if field == "cat":
        return v in text.split()
    return v in text if " " in v else re.search(rf"(?<![a-z0-9]){re.escape(v)}(?![a-z0-9])", text) is not None


def eval_query(query: str, e: Dict[str, Any]) -> bool:
    """A small evaluator for arXiv's query language (fields, quoted phrases, AND / OR / ANDNOT, brackets), enough to answer the query shapes PredictAI sends."""
    toks = TOKEN.findall(query)
    pos = [0]

    def term() -> bool:
        t = toks[pos[0]]
        pos[0] += 1
        if t == "(":
            v = expr()
            pos[0] += 1                                                                      # the closing bracket
            return v
        m = re.match(r"([A-Za-z]+):(.*)$", t)
        return _atom(e, m.group(1).lower(), m.group(2)) if m else _atom(e, "all", t)

    def expr() -> bool:
        v = term()
        while pos[0] < len(toks) and toks[pos[0]] in ("AND", "OR", "ANDNOT"):
            op = toks[pos[0]]
            pos[0] += 1
            r = term()
            v = (v and r) if op == "AND" else (v or r) if op == "OR" else (v and not r)
        return v
    try:
        return expr()
    except IndexError:
        return False


class FakeArxiv:
    def __init__(self, entries: Optional[List[Dict[str, Any]]] = None) -> None:
        self.entries = list(entries if entries is not None else C.ARXIV_ENTRIES)
        self.queries: List[str] = []
        self.id_lists: List[str] = []

    @staticmethod
    def xml(e: Dict[str, Any], ident: Optional[str] = None) -> str:
        ident = ident or e["id"]
        authors = "".join(f"<author><name>{a}</name>" + (f"<arxiv:affiliation>{e['affil']}</arxiv:affiliation>" if e.get("affil") and i == 0 else "") + "</author>" for i, a in enumerate(e["authors"]))
        cats = "".join(f'<category term="{c}"/>' for c in e["cats"])
        return (f"<entry><id>http://arxiv.org/abs/{ident}</id><updated>2023-03-02T00:00:00Z</updated><published>2023-01-01T00:00:00Z</published><title>{e['title']}</title>"
                f"<summary>{e['summary']}</summary>{authors}<link href=\"http://arxiv.org/abs/{ident}\" rel=\"alternate\" type=\"text/html\"/>"
                f"<link title=\"pdf\" href=\"http://arxiv.org/pdf/{ident}\" rel=\"related\" type=\"application/pdf\"/>"
                f'<arxiv:primary_category term="{e["cat"]}" scheme="http://arxiv.org/schemas/atom"/>{cats}</entry>')

    def route(self, h):
        p = _params(h)
        if p.get("id_list"):
            self.id_lists.append(p["id_list"])
            out = []
            for want in p["id_list"].split(","):
                base = re.sub(r"v\d+$", "", want)
                for e in self.entries:
                    if re.sub(r"v\d+$", "", e["id"]) == base:
                        out.append(self.xml(e, want if re.search(r"v\d+$", want) else e["id"]))
            return atom(feed(*out))
        q = p.get("search_query", "")
        self.queries.append(q)
        hits = [e for e in self.entries if eval_query(q, e)]
        qw = set(words(re.sub(r"\b(ti|abs|au|all|cat|AND|OR|ANDNOT)\b:?", " ", q)))
        hits.sort(key=lambda e: -len(qw & set(words(e["title"] + " " + e["summary"]))))
        return atom(feed(*[self.xml(e) for e in hits[: int(p.get("max_results", 10))]], total=len(hits)))


# ---------------------------------------------------------------------------------------------------------------------------------------------- SearXNG
class FakeSearx:
    def __init__(self, pages: Optional[List[Dict[str, Any]]] = None, unresponsive: Optional[List[List[str]]] = None) -> None:
        self.pages = list(pages if pages is not None else C.WEB_PAGES)
        self.unresponsive = unresponsive or []
        self.queries: List[str] = []

    def route(self, h):
        q = _params(h).get("q", "")
        self.queries.append(q)
        qw = {w for w in words(q) if len(w) > 2}
        scored = []
        for pg in self.pages:
            overlap = len(qw & set(words(pg["title"] + " " + pg["content"])))
            if overlap:
                scored.append((overlap, pg))
        scored.sort(key=lambda x: -x[0])
        results = [{"title": pg["title"], "url": f"https://history.test{pg['path']}", "content": pg["content"], "engine": "stand-in", "engines": ["stand-in"], "score": float(s),
                    "publishedDate": pg["published"], "category": "general"} for s, pg in scored[:10]]
        return reply({"query": q, "results": results, "unresponsive_engines": self.unresponsive})


# ---------------------------------------------------------------------------------------------------------------------------------------------- the test base
class HistoryBase(CombinedBase):
    """Alice's project, a stand-in Wikipedia / arXiv / SearXNG, the history web pages, and (on request) the French Revolution PDF."""

    def setUp(self):
        super().setUp()
        self.mw, self.ax, self.sx = FakeMediaWiki(), FakeArxiv(), FakeSearx()
        self.install_providers()
        for pg in C.WEB_PAGES:
            self.web.html(pg["path"], page(pg["title"], pg["paras"], published=pg["published"]))
        self.web.routes["/html/2101.00001v2"] = (200, {"Content-Type": "text/html; charset=utf-8"}, (FIXTURES / "arxiv_html_paper.html").read_bytes())
        self.web.routes["/html/2101.00001v1"] = self.web.routes["/html/2101.00001v2"]
        self.allow()

    def install_providers(self) -> None:
        self.web.routes["/w/api.php"] = self.mw.route()
        self.web.routes["/api/query"] = self.ax.route
        self.web.routes["/search"] = self.sx.route

    def french_pdf(self, name: str = "french rev.pdf"):
        return upload(self.ws, self.alice, self.pid, name, pdf(C.pdf_pages()))["asset"]

    # ---- reading a finished bundle
    @staticmethod
    def titles(bundle, *, status: str = "ok", origin: str = "external_research") -> List[str]:
        return [s["title"] for s in bundle["sources"] if s.get("origin") == origin and s.get("status") == status]

    @staticmethod
    def cited_sources(bundle) -> List[Dict[str, Any]]:
        ids = {e["source"] for e in bundle["evidence"]}
        return [s for s in bundle["sources"] if s["id"] in ids]

    def digest(self, b) -> str:
        lines = [f"status={b['status']} verdict={b['quality']['verdict']}"]
        by = {s["id"]: s for s in b["sources"]}
        for s in b["sources"]:
            lines.append(f"  source {s['id']} [{s.get('provider') or s.get('origin')}] {s['title']!r} status={s.get('status')}")
        for e in b["evidence"]:
            s = by[e["source"]]
            lines.append(f"  evidence <{s['title'][:40]}> {e['quote'][:150]!r}")
        return "\n".join(lines)
