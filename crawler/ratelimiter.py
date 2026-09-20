"""
Per-domain politeness control.

A single global concurrency cap (e.g. asyncio semaphore of 50) is not
enough: with 1000 seed URLs spread across, say, 40 domains, an unlucky
scheduling order could still send 20 simultaneous requests at one small
site and get you rate-limited or IP-banned. Two independent controls fix
this, and it's worth understanding why both are needed:

  - `per_domain_concurrency`: caps how many requests to one domain can be
    in flight *at once* (a semaphore per domain).
  - `per_domain_delay`: enforces a minimum spacing between request
    *starts* to one domain, even if concurrency=1. Without this, a domain
    with concurrency=1 could still be hit once every few milliseconds as
    soon as each response comes back.

This is a simplified, in-memory version of what a real politeness layer
(e.g. Scrapy's AutoThrottle) does; it does not adapt to observed server
latency the way AutoThrottle does. See README "Extension ideas".
"""
import asyncio
import time
from collections import defaultdict
from urllib.parse import urlparse


class DomainRateLimiter:
    def __init__(self, per_domain_concurrency: int, per_domain_delay: float) -> None:
        self._per_domain_concurrency = per_domain_concurrency
        self._per_domain_delay = per_domain_delay
        self._semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(per_domain_concurrency)
        )
        self._last_start: dict[str, float] = defaultdict(float)
        self._domain_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @staticmethod
    def domain_of(url: str) -> str:
        return urlparse(url).netloc

    async def acquire(self, url: str) -> str:
        """Blocks until it is this domain's turn; returns the domain name
        (caller must pass it back to `release`)."""
        domain = self.domain_of(url)
        await self._semaphores[domain].acquire()
        async with self._domain_locks[domain]:
            now = time.monotonic()
            wait = self._last_start[domain] + self._per_domain_delay - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start[domain] = time.monotonic()
        return domain

    def release(self, domain: str) -> None:
        self._semaphores[domain].release()
