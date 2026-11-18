"""A tiny local web server for tests: routes map a path to (status, headers, body) or a callable(request_handler) -> (status, headers, body).
Tests that use it start the fetcher with allow_private=True (loopback) or with the server's authority as a trusted host."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Tuple, Union

Route = Union[Tuple[int, Dict[str, str], bytes], Callable[[BaseHTTPRequestHandler], Tuple[int, Dict[str, str], bytes]]]


class FakeWeb:
    def __init__(self) -> None:
        self.routes: Dict[str, Route] = {}
        self.hits: List[str] = []
        self.posts: List[Tuple[str, bytes]] = []
        self.headers_seen: List[Dict[str, str]] = []
        web = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a: Any) -> None:  # silence
                pass

            def _serve(self, method: str) -> None:
                path = self.path
                length = int(self.headers.get("Content-Length") or 0)
                self.body = self.rfile.read(length) if length else b""
                web.hits.append(path)
                if method == "POST":
                    web.posts.append((path, self.body))
                web.headers_seen.append({k.lower(): v for k, v in self.headers.items()})
                route = web.routes.get(path) or web.routes.get(path.split("?")[0])
                if route is None:
                    status, headers, body = 404, {"Content-Type": "text/plain"}, b"not found"
                else:
                    status, headers, body = route(self) if callable(route) else route
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                self._serve("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._serve("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def authority(self) -> str:
        return f"127.0.0.1:{self.port}"

    def __enter__(self) -> "FakeWeb":
        self.thread.start()
        return self

    def __exit__(self, *a: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def html(self, path: str, body: str, status: int = 200, extra: Dict[str, str] | None = None) -> None:
        self.routes[path] = (status, {"Content-Type": "text/html; charset=utf-8", **(extra or {})}, body.encode("utf-8"))

    def json(self, path: str, data: Any, status: int = 200) -> None:
        self.routes[path] = (status, {"Content-Type": "application/json"}, json.dumps(data).encode("utf-8"))

    def text(self, path: str, body: str, ctype: str = "text/plain; charset=utf-8", status: int = 200) -> None:
        self.routes[path] = (status, {"Content-Type": ctype}, body.encode("utf-8"))


def page(title: str, paragraphs: List[str], *, published: str | None = None, extra_head: str = "", extra_body: str = "") -> str:
    meta = f'<meta property="article:published_time" content="{published}">' if published else ""
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<!doctype html><html lang='en'><head><title>{title}</title>{meta}{extra_head}</head><body><nav>Home About Contact us today</nav>"
            f"<article><h1>{title}</h1>{body}</article>{extra_body}<footer>Copyright footer text that is long enough to count</footer></body></html>")


class RoutedTransport:
    """A fetcher Transport that sends every request, whatever its host, to one FakeWeb, keeping the Host name for assertions.
    It lets the provider adapters keep their real https://en.wikipedia.org/... URLs in tests while no network is used."""

    def __init__(self, web: "FakeWeb") -> None:
        self.web, self.requests = web, []

    def request(self, target, address, headers, timeout, max_bytes):
        import http.client

        from app.modules.webaccess.fetcher import RawResponse
        self.requests.append((target.host, target.target))
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=5)
        try:
            conn.request("GET", f"{target.target}", headers={**headers, "Host": target.host})
            resp = conn.getresponse()
            body = resp.read(max_bytes + 1)
            return RawResponse(resp.status, {k.lower(): v for k, v in resp.getheaders()}, body[:max_bytes], len(body) > max_bytes)
        finally:
            conn.close()
