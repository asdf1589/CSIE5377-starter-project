"""
Periodic human-readable status line + final run summary.

This is the "operator watching a terminal" layer described in stats.py --
cheap, no external dependency, and it's what you'll actually look at
while iterating locally before you've bothered to point Grafana at
:9090/metrics.
"""
import asyncio
import json
import logging
import os
import time

from .stats import STATS

logger = logging.getLogger("crawler.reporter")


async def status_reporter(frontier, interval: float) -> None:
    start = time.monotonic()
    try:
        while True:
            await asyncio.sleep(interval)
            elapsed = time.monotonic() - start
            rate = STATS.fetched / elapsed if elapsed > 0 else 0.0
            logger.info(
                "status",
                extra={
                    "queue_depth": frontier.qsize(),
                    "seen": frontier.seen_count,
                    "inflight": None,  # exact value lives in Prometheus INFLIGHT gauge
                },
            )
            print(
                f"[status] elapsed={elapsed:6.1f}s  "
                f"queue={frontier.qsize():5d}  "
                f"fetched={STATS.fetched:5d} (ok={STATS.ok} failed={STATS.failed})  "
                f"retries={STATS.retried}  robots_skipped={STATS.robots_skipped}  "
                f"rate={rate:5.1f}/s",
                flush=True,
            )
    except asyncio.CancelledError:
        pass


def write_summary(output_dir: str, elapsed_s: float, urls_seen: int) -> None:
    summary = {
        "elapsed_s": round(elapsed_s, 2),
        "urls_seen": urls_seen,
        **STATS.as_dict(),
        "throughput_per_s": round(STATS.fetched / elapsed_s, 2) if elapsed_s > 0 else 0,
    }
    path = os.path.join(output_dir, "summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\n=== crawl summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"(written to {path})")
