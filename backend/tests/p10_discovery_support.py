"""Fixtures for R2 tests: canned provider answers shaped like the services' documented responses, and a routing fake fetcher."""
import json

from app.modules.research_intelligence.security.urlguard import FetchResult, UrlRejected

CROSSREF_WORKS = {"message": {"total-results": 2, "items": [
    {"DOI": "10.1000/ai.jobs.1", "title": ["Artificial Intelligence and Employment: Evidence from Firms"], "author": [{"given": "Maria", "family": "Silva"}, {"given": "John", "family": "Okafor"}],
     "issued": {"date-parts": [[2021, 5, 3]]}, "container-title": ["Journal of Labour Economics"], "type": "journal-article", "URL": "https://doi.org/10.1000/ai.jobs.1",
     "abstract": "<jats:p>Abstract: We study how adoption of artificial intelligence changed employment in 3,000 firms.</jats:p>", "is-referenced-by-count": 120, "references-count": 40,
     "subject": ["Economics"], "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}], "link": [{"URL": "https://example.org/ai-jobs.pdf", "content-type": "application/pdf"}]},
    {"DOI": "10.1000/ai.jobs.2", "title": ["Automation and Wages"], "author": [{"name": "Research Consortium"}], "issued": {"date-parts": [[2019]]}, "container-title": ["Wage Review"],
     "type": "journal-article", "is-referenced-by-count": 15, "link": [{"URL": "https://publisher.example/closed.pdf", "content-type": "application/pdf"}]}]}}

OPENALEX_WORKS = {"meta": {"count": 2}, "results": [
    {"id": "https://openalex.org/W111", "doi": "https://doi.org/10.1000/AI.JOBS.1", "display_name": "Artificial Intelligence and Employment: Evidence from Firms", "publication_date": "2021-05-03",
     "type": "article", "cited_by_count": 150, "authorships": [{"author": {"display_name": "Maria Silva"}}, {"author": {"display_name": "John Okafor"}}],
     "primary_location": {"landing_page_url": "https://journal.example/ai-jobs", "source": {"display_name": "Journal of Labour Economics"}},
     "best_oa_location": {"pdf_url": "https://repo.example/ai-jobs.pdf", "license": "cc-by"}, "open_access": {"is_oa": True},
     "abstract_inverted_index": {"We": [0], "study": [1], "how": [2], "artificial": [3], "intelligence": [4], "changed": [5], "employment.": [6]},
     "primary_topic": {"field": {"display_name": "Economics, Econometrics and Finance"}}, "referenced_works_count": 41},
    {"id": "https://openalex.org/W222", "doi": None, "display_name": "Deep Learning for Hiring Prediction", "publication_date": "2023-01-10", "type": "preprint", "cited_by_count": 3,
     "authorships": [{"author": {"display_name": "Wei Chen"}}], "primary_location": {"source": {"display_name": "SSRN"}}, "best_oa_location": None, "open_access": {"is_oa": False},
     "abstract_inverted_index": None, "primary_topic": {"field": {"display_name": "Computer Science"}}}]}

ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry><id>http://arxiv.org/abs/2105.01234v2</id><published>2021-05-03T10:00:00Z</published><title>Artificial Intelligence and Employment: Evidence from Firms</title>
    <summary>We study how adoption of artificial intelligence changed employment in firms. A longer arXiv abstract with more detail follows here.</summary>
    <author><name>Maria Silva</name></author><author><name>John Okafor</name></author><category term="econ.GN"/></entry>
  <entry><id>http://arxiv.org/abs/2208.99999v1</id><published>2022-08-30T10:00:00Z</published><title>Neural Networks for Labour Market Forecasting</title>
    <summary>We forecast unemployment with neural networks.</summary><author><name>Wei Chen</name></author><arxiv:doi>10.1000/nn.labour.1</arxiv:doi><category term="cs.LG"/></entry>
</feed>"""

S2_SEARCH = {"total": 2, "data": [
    {"paperId": "abc123", "title": "Artificial Intelligence and Employment: Evidence from Firms", "abstract": "We study adoption of AI.", "year": 2021, "venue": "Journal of Labour Economics",
     "publicationDate": "2021-05-03", "externalIds": {"DOI": "10.1000/ai.jobs.1"}, "authors": [{"name": "Maria Silva"}, {"name": "John Okafor"}], "citationCount": 140, "referenceCount": 40,
     "openAccessPdf": {"url": "https://s2.example/ai-jobs.pdf"}, "fieldsOfStudy": ["Economics"], "publicationTypes": ["JournalArticle"], "url": "https://www.semanticscholar.org/paper/abc123"},
    {"paperId": "def456", "title": "Robots and Jobs", "abstract": None, "year": 2017, "venue": "Econ Letters", "publicationDate": None, "externalIds": {}, "authors": [{"name": "Ana Lopes"}],
     "citationCount": 900, "openAccessPdf": None, "fieldsOfStudy": ["Economics"], "publicationTypes": ["Review"], "url": "https://www.semanticscholar.org/paper/def456"}]}

SEARX_JSON = {"results": [{"url": "https://news.example/ai-and-jobs?utm_source=x", "title": "AI and jobs: what the data says", "content": "A news article on AI and employment.", "engine": "duckduckgo", "score": 1.2}]}


def ok(body, ctype="application/json", url="https://x.example/"):
    data = body if isinstance(body, (bytes, bytearray)) else (json.dumps(body) if not isinstance(body, str) else body).encode()
    return FetchResult(200, url, ctype, bytes(data), {})


class Routed:
    """Fake SafeFetcher: the first route whose substring is in the URL answers. Records every call with its headers (to check keys go in headers, never into messages)."""

    def __init__(self, *routes):
        self.routes = list(routes)
        self.calls = []

    def fetch(self, url, accept=(), extra_headers=None):
        self.calls.append((url, dict(extra_headers or {})))
        for needle, answer in self.routes:
            if needle in url:
                a = answer.pop(0) if isinstance(answer, list) and len(answer) > 1 else (answer[0] if isinstance(answer, list) else answer)
                if isinstance(a, Exception):
                    raise a
                return a
        raise UrlRejected("URL_HTTP_ERROR", "The site answered with an error (404).", status=404)

    def count(self, needle):
        return sum(1 for u, _ in self.calls if needle in u)


class SearxFake:
    """Phase 6 style fetcher for the SearXNG provider (same fetch signature and result attributes)."""

    def __init__(self, payload=SEARX_JSON):
        self.payload, self.calls = payload, []

    def fetch(self, url, accept=(), robots=True, max_bytes=0, retries=0, **kw):
        self.calls.append(url)
        class R:
            content_type = "application/json"
            body = json.dumps(self.payload).encode()
            status = 200
        return R()
