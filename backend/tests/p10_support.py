"""Phase 10 test support: a harness, fakes for the network (fetcher) and the AI router, and generators for real PDF/DOCX/XLSX files."""
import io
import os
from dataclasses import replace
from typing import Any, Dict, List, Optional

from app.modules.providers.base import Consent, RouteOutcome
from app.modules.research_intelligence.reasoning.assistant import Assistant
from app.modules.research_intelligence.security.urlguard import FetchResult, UrlRejected
from app.modules.research_intelligence.services.jobs import JobService
from app.modules.research_intelligence.services.notes import NotesService
from app.modules.research_intelligence.services.pipeline import Pipeline
from app.modules.research_intelligence.services.search import SearchService
from app.modules.research_intelligence.services.sources import SourceService
from app.modules.researchspace.service import ResearchSpaceService
from support import GOOD_PASSWORD, Harness

PW = GOOD_PASSWORD


class RIHarness:
    def __init__(self, **overrides):
        self.h = Harness()
        self.root = os.path.join(self.h._tmp.name, "ri")
        self.h.settings = replace(self.h.settings, ri_root=self.root, **overrides)
        self.owner, _ = self.h.make_owner()

    def close(self):
        self.h.close()

    def user(self, email="alice@example.com", special=False):
        p, _ = self.h.make_user(email)
        if special:
            self.h.services().codes.grant_special(self.owner, p.user_id)
            p = self.h.login(email, PW).principal
        return p

    def ctx(self):
        return self.h.services().ctx

    def workspace(self, p, name="Study"):
        return ResearchSpaceService(self.ctx()).create(p, name)["id"]

    def sources(self):
        return SourceService(self.ctx())

    def pipeline(self, fetcher=None, router=None, **kw):
        return Pipeline(self.ctx(), fetcher=fetcher, router_factory=(lambda: router) if router else None, **kw)

    def assistant(self, router=None):
        return Assistant(self.ctx(), router_factory=(lambda: router) if router else None)

    def search(self, router=None):
        return SearchService(self.ctx(), router_factory=(lambda: router) if router else None)

    def notes(self):
        return NotesService(self.ctx())

    def run_jobs(self, fetcher=None, router=None, limit=20, **kw):
        """Process queued jobs the way the worker does. Returns the outcomes."""
        pipe = self.pipeline(fetcher, router, **kw)
        out = []
        for _ in range(limit):
            job = pipe.jobs.claim("test-worker")
            if job is None:
                break
            out.append(pipe.run(job))
        return out

    def add_text(self, p, wid, name, text, **kw):
        s = self.sources().add_file(p, wid, name, text.encode() if isinstance(text, str) else text)
        self.run_jobs(**kw)
        return self.sources().get(p, wid, s["id"])


class FakeFetcher:
    """Stands in for SafeFetcher: maps URL -> FetchResult (or an exception to raise)."""

    def __init__(self, routes: Optional[Dict[str, Any]] = None):
        self.routes, self.calls = dict(routes or {}), []

    def fetch(self, url, accept=(), extra_headers=None):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            raise UrlRejected("URL_HTTP_ERROR", "The site answered with an error (404).")
        if isinstance(r, Exception):
            raise r
        if isinstance(r, list):                                            # a sequence of outcomes, one per call
            r = r.pop(0) if len(r) > 1 else r[0]
            if isinstance(r, Exception):
                raise r
        return r


def page(body: bytes, ctype="text/html", url="https://example.org/a") -> FetchResult:
    return FetchResult(200, url, ctype, body, {})


class FakeRouter:
    """Stands in for the platform Router. `chat_text` is what the 'model' answers; `embedder` maps texts to vectors."""

    def __init__(self, chat_text: Optional[str] = None, embedder=None, provider="fake-local", model="fake-1", fail_chat=False, fail_embed=False, deterministic=False):
        self.chat_text, self.embedder, self.provider, self.model = chat_text, embedder, provider, model
        self.fail_chat, self.fail_embed, self.deterministic = fail_chat, fail_embed, deterministic
        self.chats: List[Dict[str, Any]] = []
        self.embeds: List[Dict[str, Any]] = []

    def chat(self, owner, project, task, messages, *, data_class="public", consent=None, max_tokens=400, temperature=0.0):
        self.chats.append({"task": task, "messages": messages, "data_class": data_class, "consent": consent or Consent(), "owner": owner})
        if self.fail_chat or self.deterministic or self.chat_text is None:
            return RouteOutcome(ok=False, provider="builtin", deterministic=True)
        text = self.chat_text(messages) if callable(self.chat_text) else self.chat_text
        return RouteOutcome(ok=True, provider=self.provider, model=self.model, text=text, trace=[{"provider": self.provider, "outcome": "ok"}])

    def embed(self, owner, project, texts, *, data_class="private", consent=None):
        self.embeds.append({"texts": list(texts), "data_class": data_class, "consent": consent or Consent()})
        if self.fail_embed or self.embedder is None:
            return RouteOutcome(ok=False, provider="builtin", deterministic=True)
        return RouteOutcome(ok=True, provider=self.provider, model=self.model, vectors=[self.embedder(t) for t in texts], units_in=len(texts))


def concept_embedder(groups):
    """A tiny SEMANTIC embedder for tests: words of one group (synonyms) land on the same axis."""
    index = {w: i for i, g in enumerate(groups) for w in g}

    def embed(text):
        v = [0.0] * (len(groups) + 1)
        for w in text.lower().replace(".", " ").replace(",", " ").split():
            v[index.get(w, len(groups))] += 1.0
        return v
    return embed


# ------------------------------------------------------------------ file generators
def make_pdf(pages: List[str]) -> bytes:
    objs: List[bytes] = []
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        lines = []
        for line in text.split("\n"):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append(f"({esc}) Tj T*")
        stream = ("BT /F1 11 Tf 50 750 Td 16 TL " + " ".join(lines) + " ET").encode("latin-1", "replace")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offsets:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(paragraphs, table=None, title="Heat and trees") -> bytes:
    import docx
    d = docx.Document()
    d.core_properties.title = title
    d.core_properties.author = "A. Researcher; B. Scholar"
    for kind, text in paragraphs:
        d.add_heading(text, level=1) if kind == "h" else d.add_paragraph(text)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, v in enumerate(row):
                t.cell(r, c).text = str(v)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx(sheets: Dict[str, List[List[Any]]]) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ARTICLE = ("Abstract\n\nUrban green space reduces heat. Trees lower surface temperature by 2.5 degrees in summer according to our measurements across 40 districts.\n\n"
           "1. Methods\n\nWe measured surface temperature with satellite imagery during summer months in forty districts. Samples were gathered weekly.\n\n"
           "2. Results\n\nDistricts with more tree canopy were cooler. The canopy effect was strongest in dense neighbourhoods and weakest near industrial areas.\n\n"
           "References\n\n[1] A. Smith. Cooling cities. Journal of Urban Climate. 2020.\n")
