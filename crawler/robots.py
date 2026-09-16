"""
robots.txt compliance, cached per origin.

Fetching a fresh robots.txt for every URL would double your request count
and hammer the very politeness mechanism this is meant to protect, so
results are cached per (scheme, host) "origin" for the lifetime of the run.

Fails open: if robots.txt is missing or errors out fetching it, we allow
the crawl. This mirrors what most well-behaved crawlers do (RFC-adjacent
convention, not an RFC), but is a judgment call you should revisit for a
production system operating at larger scale or in more sensitive contexts.
"""
import asyncio
import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp

logger = logging.getLogger("crawler.robots")


class RobotsCache:
    def __init__(self, session: aiohttp.ClientSession, user_agent: str):
        self._session = session
        self._user_agent = user_agent
        self._cache: dict[str, RobotFileParser | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._cache:
                self._cache[origin] = await self._fetch(origin)
        rp = self._cache[origin]
        if rp is None:
            return True  # fail open
        return rp.can_fetch(self._user_agent, url)

    async def _fetch(self, origin: str) -> RobotFileParser | None:
        robots_url = origin + "/robots.txt"
        try:
            async with self._session.get(
                robots_url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text(errors="ignore")
        except Exception:
            return None
        rp = RobotFileParser()
        rp.parse(text.splitlines())
        return rp
