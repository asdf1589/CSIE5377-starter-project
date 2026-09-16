"""
Plain-Python counters for cheap human-readable console status lines.

This duplicates a little of what Prometheus metrics already track. That's
deliberate: Prometheus needs a scraper/UI to look at, but during local
development you just want a number scrolling in your terminal. Two
observability layers, two audiences (operator watching a terminal vs.
a dashboard/alerting system) -- both cheap enough to keep. Safe without
locks because asyncio runs this on a single thread; no other coroutine can
interleave inside a plain `+= 1`.
"""
from dataclasses import dataclass, field


@dataclass
class Stats:
    fetched: int = 0
    ok: int = 0
    failed: int = 0
    retried: int = 0
    robots_skipped: int = 0
    bytes_received: int = 0

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "ok": self.ok,
            "failed": self.failed,
            "retried": self.retried,
            "robots_skipped": self.robots_skipped,
            "bytes_received": self.bytes_received,
        }

    def load_dict(self, d: dict) -> None:
        """Restore counters from a checkpoint (crawler-spec.md #2). Uses
        .get(..., 0) per field so a checkpoint written by an older
        schema doesn't crash a resume -- it just resumes that counter
        at 0.
        """
        self.fetched = d.get("fetched", 0)
        self.ok = d.get("ok", 0)
        self.failed = d.get("failed", 0)
        self.retried = d.get("retried", 0)
        self.robots_skipped = d.get("robots_skipped", 0)
        self.bytes_received = d.get("bytes_received", 0)


STATS = Stats()
