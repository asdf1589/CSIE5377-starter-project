"""
URL frontier: a bounded queue + a dedup set.

Two architecture decisions worth calling out:

1. Bounded queue (`maxsize=config.queue_maxsize`). An unbounded queue means
   that if producers (seed loading, or link extraction if you add crawling-
   by-following-links later) ever outpace consumers (fetch workers), memory
   grows without limit. A bounded queue converts that failure mode into
   *backpressure*: `put()` simply blocks until a worker drains an item.
   That's usually what you want in a single-process system.

2. Dedup happens at "add" time based on a normalized URL, not at "fetch"
   time. This keeps the invariant "a URL is in the seen-set iff it has been
   enqueued at most once" easy to reason about, and it's what lets
   `seen_count` double as a monotonically increasing progress metric even
   before those URLs have actually been fetched.
"""
import asyncio
from urllib.parse import urlparse, urlunparse

from . import metrics


def normalize_url(url: str) -> str:
    """Best-effort canonicalization so trivially-equivalent URLs collide.

    - lowercases scheme/host
    - drops the fragment (#section) -- irrelevant to an HTTP GET
    - defaults empty path to "/"
    Does NOT sort/strip query params: query strings can be semantically
    significant (e.g. `?page=2`), so we leave that judgment call to the
    caller rather than silently dropping data.
    """
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "http").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def domain_of(url: str) -> str:
    """Netloc of a (normalized) URL. Duplicates
    ratelimiter.DomainRateLimiter.domain_of() by design: crawler-spec.md's
    module list says not to touch ratelimiter.py for this change, and
    it's one line, so a shared helper isn't worth the cross-module
    coupling.
    """
    return urlparse(url).netloc


class Frontier:
    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._seen: set[str] = set()
        self._lock = asyncio.Lock()
        self._domain_page_counts: dict[str, int] = {}
        self._referrers: dict[str, str | None] = {}

    async def add(self, url: str, referrer: str | None = None) -> bool:
        """Returns True if the (normalized) URL was newly added."""
        norm = normalize_url(url)
        async with self._lock:
            if norm in self._seen:
                return False
            self._seen.add(norm)
            self._referrers[norm] = referrer
            domain = domain_of(norm)
            is_new_domain = domain not in self._domain_page_counts
            self._domain_page_counts[domain] = self._domain_page_counts.get(domain, 0) + 1
        if is_new_domain:
            metrics.DISTINCT_DOMAINS_TOTAL.set(len(self._domain_page_counts))
        await self._queue.put(norm)  # may block: that's the backpressure
        return True

    async def add_many(self, urls) -> int:
        added = 0
        for u in urls:
            if u and await self.add(u):
                added += 1
        return added

    def domain_page_count(self, domain: str) -> int:
        return self._domain_page_counts.get(domain, 0)

    def referrer_of(self, url: str) -> str | None:
        """`url` must already be normalized (i.e. exactly what `get()`
        returned) -- this only looks up entries written by `add()`,
        which stores under the normalized form.
        """
        return self._referrers.get(url)

    async def get(self) -> str:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    async def join(self) -> None:
        """Blocks until every enqueued item has had task_done() called."""
        await self._queue.join()

    @property
    def seen_count(self) -> int:
        return len(self._seen)
