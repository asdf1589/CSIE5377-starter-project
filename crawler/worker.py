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
from .frontier import domain_of, normalize_url
from .linkextract import extract_links
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
        domain = domain_of(url)
        referrer = frontier.referrer_of(url)
        try:
            if robots is not None:
                allowed = await robots.is_allowed(url)
                if not allowed:
                    metrics.ROBOTS_SKIPPED_TOTAL.inc()
                    stats.STATS.robots_skipped += 1
                    logger.info("robots_disallowed", extra={"url": url, "worker_id": worker_id})
                    await store.record(
                        url, domain, None,
                        robots_blocked=True, referrer=referrer,
                        include_referrer=config.follow_links,
                    )
                    continue

            await rate_limiter.acquire(url)
            metrics.INFLIGHT.inc()
            try:
                result = await fetch(
                    session,
                    url,
                    timeout=config.request_timeout,
                    max_retries=config.max_retries,
                    backoff_base=config.retry_backoff_base,
                )
                await store.record(
                    url, domain, result,
                    robots_blocked=False, referrer=referrer,
                    include_referrer=config.follow_links,
                )
            finally:
                metrics.INFLIGHT.dec()
                rate_limiter.release(domain)
            # Enqueueing links can block on the frontier's bounded-queue
            # backpressure (frontier.add() -> await queue.put()). That must
            # happen AFTER the per-domain rate-limit slot is released above,
            # or a worker blocked here would hold domain `domain`'s slot
            # indefinitely, starving every other worker wanting to fetch
            # from that domain -- and INFLIGHT would misreport HTML
            # parsing/link enqueueing as network concurrency.
            if config.follow_links and result.ok and result.content_type == "text/html":
                await _follow_links(url, result, frontier, config)
        finally:
            metrics.QUEUE_DEPTH.set(frontier.qsize())
            frontier.task_done()


async def _follow_links(source_url: str, result, frontier, config) -> None:
    """Extract <a href> from a fetched HTML page and enqueue new ones,
    subject to the per-domain crawl-trap cap (crawler-spec.md #3).

    The cap is checked against each candidate LINK's own domain (not
    the referring page's domain): the trap this defends against is one
    domain generating unbounded same-domain links (e.g. an infinite
    calendar), so the budget that must be capped is "how many pages of
    THIS domain are already known to the frontier". Checking the
    referrer's domain instead would block legitimate cross-domain
    discovery from a single popular hub page -- the opposite of
    crawler-spec.md's breadth-first, many-distinct-domains bias.
    """
    if not result.body:
        return
    html_text = result.body.decode("utf-8", errors="ignore")
    for link in extract_links(source_url, html_text):
        norm_link = normalize_url(link)
        link_domain = domain_of(norm_link)
        if frontier.domain_page_count(link_domain) >= config.max_pages_per_domain:
            continue
        await frontier.add(link, referrer=source_url)
