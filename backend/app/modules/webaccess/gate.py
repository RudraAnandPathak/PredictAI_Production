"""A process-wide gate for hosts whose published terms ask for strictly serial, spaced requests.

arXiv's Terms of Use for its APIs ask for "no more than one request every three seconds" and "a single connection at a time", counted across all of a client's machines. A
per-job fetcher cannot honour that when two research jobs run at once, so every arXiv request in this process goes through one gate: one request at a time, at least
`min_interval_s` apart, and a cool-down after an access refusal (arXiv treats continued requests after a 403 as an attack). Several server processes would each have their
own gate: run research on one process (the default) when using arXiv, as the setup guide says.

Two gates exist: "arxiv" (the API and PDF host, export.arxiv.org: 3 seconds) and "arxiv-web" (arxiv.org pages such as the HTML version of a paper: 15 seconds, the Crawl-delay in
https://arxiv.org/robots.txt, which the fetcher's robots check does not apply by itself). The first request after a quiet period is never delayed."""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict

from .fetcher import FetchError


class HostGate:
    def __init__(self, min_interval_s: float, *, clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep, label: str = "this service") -> None:
        self.min_interval_s, self.clock, self.sleeper, self.label = min_interval_s, clock, sleeper, label
        self._lock = threading.Lock()
        self._last = float("-inf")
        self._blocked_until = 0.0

    def check(self) -> None:
        remaining = self._blocked_until - self.clock()
        if remaining > 0:
            raise FetchError("PROVIDER_COOLDOWN", f"{self.label} refused earlier requests, so no more are sent for about {int(remaining // 60) + 1} minute(s). Open the paper from its link instead.",
                             retryable=True, retry_after=remaining)

    def trip(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, self.clock() + seconds)

    def reset(self) -> None:
        self._blocked_until = 0.0
        self._last = float("-inf")

    def __enter__(self) -> "HostGate":
        self._lock.acquire()
        try:
            self.check()
            wait = self._last + self.min_interval_s - self.clock()
            if wait > 0:
                self.sleeper(wait)
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._last = self.clock()
        self._lock.release()


GATES: Dict[str, HostGate] = {"arxiv": HostGate(3.0, label="arXiv"), "arxiv-web": HostGate(15.0, label="arXiv")}
