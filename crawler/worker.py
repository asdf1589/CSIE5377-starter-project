"""
A single worker: pull one URL, respect politeness, fetch, record, repeat.

Cancellation contract: the caller (main.py) cancels these tasks after the
frontier drains. When cancelled mid-fetch, `fetch()` re-raises
CancelledError immediately (see fetcher.py) and this function's `finally`
block still runs, so in-flight/semaphore bookkeeping stays correct even on
an abrupt shutdown (e.g. Ctrl-C).
"""
import logging

from . import metrics, stats
from .fetcher import fetch
from .robots import RobotsCache

logger = logging.getLogger("crawler.worker")


async def worker(
    worker_id: int,
    frontier,
    session,
    rate_limiter,
    robots: RobotsCache | None,
    store,
    config,
) -> None:
    while True:
        url = await frontier.get()
        try:
            if robots is not None:
                allowed = await robots.is_allowed(url)
                if not allowed:
                    metrics.ROBOTS_SKIPPED_TOTAL.inc()
                    stats.STATS.robots_skipped += 1
                    logger.info("robots_disallowed", extra={"url": url, "worker_id": worker_id})
                    continue

            domain = await rate_limiter.acquire(url)
            metrics.INFLIGHT.inc()
            try:
                result = await fetch(
                    session,
                    url,
                    timeout=config.request_timeout,
                    max_retries=config.max_retries,
                    backoff_base=config.retry_backoff_base,
                )
                await store.record(result)
            finally:
                metrics.INFLIGHT.dec()
                rate_limiter.release(domain)
        finally:
            metrics.QUEUE_DEPTH.set(frontier.qsize())
            frontier.task_done()
