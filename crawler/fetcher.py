"""
The actual HTTP fetch, with retry + exponential backoff + jitter.

Jitter matters at scale: if N requests all fail at the same moment (e.g. a
target site has a brief blip) and all retry with the same deterministic
backoff, they all retry in lockstep and can re-cause the failure they were
backing off from ("thundering herd"). Adding `random.uniform(0, backoff)`
spreads retries out in time.
"""
import asyncio
import logging
import random
import time
from dataclasses import dataclass

import aiohttp

from . import metrics, stats

logger = logging.getLogger("crawler.fetcher")


@dataclass
class FetchResult:
    url: str
    status: int | None = None
    body: bytes | None = None
    error: str | None = None
    elapsed: float = 0.0
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and self.status < 400


async def fetch(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: float,
    max_retries: int,
    backoff_base: float,
) -> FetchResult:
    attempt = 0
    last_error: Exception | None = None

    while attempt <= max_retries:
        attempt += 1
        start = time.monotonic()
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                body = await resp.read()
                elapsed = time.monotonic() - start

                metrics.FETCH_LATENCY.observe(elapsed)
                metrics.REQUESTS_TOTAL.labels(status=metrics.status_bucket(resp.status)).inc()
                stats.STATS.fetched += 1
                stats.STATS.bytes_received += len(body)
                if resp.status < 400:
                    stats.STATS.ok += 1
                else:
                    stats.STATS.failed += 1

                logger.info(
                    "fetched",
                    extra={
                        "url": url,
                        "status": resp.status,
                        "elapsed_ms": round(elapsed * 1000, 1),
                        "attempt": attempt,
                    },
                )
                return FetchResult(url, status=resp.status, body=body, elapsed=elapsed, attempts=attempt)

        except asyncio.CancelledError:
            raise  # never swallow cancellation (e.g. Ctrl-C shutdown)

        except Exception as e:  # noqa: BLE001 - deliberately broad: any network error is a "failed fetch"
            elapsed = time.monotonic() - start
            last_error = e
            metrics.ERRORS_TOTAL.labels(error_type=type(e).__name__).inc()
            logger.warning(
                "fetch_attempt_failed",
                extra={"url": url, "attempt": attempt, "elapsed_ms": round(elapsed * 1000, 1)},
                exc_info=True,
            )
            if attempt <= max_retries:
                metrics.RETRIES_TOTAL.inc()
                stats.STATS.retried += 1
                sleep_for = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, backoff_base)
                await asyncio.sleep(sleep_for)

    metrics.REQUESTS_TOTAL.labels(status="error").inc()
    stats.STATS.fetched += 1
    stats.STATS.failed += 1
    return FetchResult(url, error=str(last_error), attempts=attempt)
