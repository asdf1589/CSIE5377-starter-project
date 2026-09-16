"""
Orchestrator: wires every component together and owns the run's lifecycle.

Shutdown design: we race `frontier.join()` (normal completion -- every
seed URL has been processed) against a `stop_event` set by SIGINT/SIGTERM.
Whichever finishes first triggers cancellation of all worker tasks. This
means Ctrl-C during a long crawl stops promptly instead of waiting for the
whole queue to drain, while a clean run still exits as soon as work is
done -- both paths go through the same teardown code.
"""
import argparse
import asyncio
import logging
import signal
import sys
import time

import aiohttp

from .config import CrawlerConfig
from .logging_config import setup_logging
from . import metrics
from . import stats
from .checkpoint import checkpoint_loop, load_checkpoint, write_checkpoint
from .frontier import Frontier
from .ratelimiter import DomainRateLimiter
from .robots import RobotsCache
from .storage import ResultStore
from .worker import worker
from .reporter import status_reporter, write_summary

logger = logging.getLogger("crawler.main")


def load_seeds(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


async def _runtime_limit(hours: float, stop_event: asyncio.Event) -> None:
    await asyncio.sleep(hours * 3600)
    logger.warning(f"max_runtime_reached hours={hours}; stopping")
    stop_event.set()


async def run(config: CrawlerConfig) -> None:
    setup_logging()
    metrics.start_metrics_server(config.metrics_port)
    logger.info(f"metrics_server_started port={config.metrics_port}")

    if config.resume_from:
        checkpoint = load_checkpoint(config.resume_from)
        frontier = await Frontier.from_snapshot(checkpoint["frontier"], maxsize=config.queue_maxsize)
        stats.STATS.load_dict(checkpoint["stats"])
        logger.info(
            f"resumed_from_checkpoint path={config.resume_from} "
            f"seen={frontier.seen_count} pending={frontier.qsize()}"
        )
    else:
        seeds = load_seeds(config.seeds_file)
        frontier = Frontier(maxsize=config.queue_maxsize)
        added = await frontier.add_many(seeds)
        logger.info(f"seeds_loaded added={added} in_file={len(seeds)} duplicates={len(seeds) - added}")

    metrics.URLS_SEEN_TOTAL.set(frontier.seen_count)
    metrics.QUEUE_DEPTH.set(frontier.qsize())

    rate_limiter = DomainRateLimiter(config.per_domain_concurrency, config.per_domain_delay)
    store = ResultStore(config.output_dir, save_body=config.save_body)

    connector = aiohttp.TCPConnector(limit=config.max_concurrency)
    headers = {"User-Agent": config.user_agent}

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # not available on some platforms (e.g. Windows event loop)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        robots = RobotsCache(session, config.user_agent) if config.respect_robots_txt else None

        worker_tasks = [
            asyncio.create_task(
                worker(i, frontier, session, rate_limiter, robots, store, config),
                name=f"worker-{i}",
            )
            for i in range(config.max_concurrency)
        ]
        reporter_task = asyncio.create_task(status_reporter(frontier, config.status_interval))
        checkpoint_task = asyncio.create_task(
            checkpoint_loop(config.checkpoint_path, config.checkpoint_interval_seconds, frontier)
        )
        runtime_task = (
            asyncio.create_task(_runtime_limit(config.max_runtime_hours, stop_event))
            if config.max_runtime_hours is not None
            else None
        )

        start = time.monotonic()
        join_task = asyncio.create_task(frontier.join())
        stop_task = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            [join_task, stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done:
            logger.warning("shutdown_signal_received; cancelling workers")
        for t in pending:
            t.cancel()

        reporter_task.cancel()
        checkpoint_task.cancel()
        if runtime_task is not None:
            runtime_task.cancel()
        for t in worker_tasks:
            t.cancel()
        background_tasks = [reporter_task, checkpoint_task]
        if runtime_task is not None:
            background_tasks.append(runtime_task)
        await asyncio.gather(*worker_tasks, *background_tasks, join_task, stop_task, return_exceptions=True)

        elapsed = time.monotonic() - start
        logger.info(f"crawl_complete elapsed_s={elapsed:.1f} urls_seen={frontier.seen_count}")
        write_checkpoint(config.checkpoint_path, frontier)
        write_summary(config.output_dir, elapsed, frontier.seen_count)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Educational single-node concurrent crawler")
    p.add_argument("--seeds", default="seeds.txt", help="path to newline-delimited seed URL file")
    p.add_argument("--output-dir", default="output")
    p.add_argument("--concurrency", type=int, default=50, help="global max in-flight requests")
    p.add_argument("--per-domain-concurrency", type=int, default=2)
    p.add_argument("--per-domain-delay", type=float, default=1.0, help="seconds between requests to same domain")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--metrics-port", type=int, default=9090)
    p.add_argument("--status-interval", type=float, default=5.0)
    p.add_argument("--save-body", action="store_true", help="persist raw response bodies under output/pages/")
    p.add_argument("--no-robots", action="store_true", help="disable robots.txt checking (not recommended)")
    p.add_argument("--max-runtime-hours", type=float, default=None, help="wall-clock auto-stop (e.g. 48)")
    p.add_argument("--checkpoint-interval", type=float, default=300, help="seconds between checkpoint writes")
    p.add_argument("--checkpoint-path", default="output/checkpoint.json")
    p.add_argument("--resume", default=None, help="resume from a checkpoint file instead of loading --seeds")
    p.add_argument("--follow-links", action="store_true", help="enable link extraction / frontier growth")
    p.add_argument(
        "--max-pages-per-domain", type=int, default=20,
        help="crawl-trap cap; only matters with --follow-links",
    )
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    config = CrawlerConfig(
        seeds_file=args.seeds,
        output_dir=args.output_dir,
        max_concurrency=args.concurrency,
        per_domain_concurrency=args.per_domain_concurrency,
        per_domain_delay=args.per_domain_delay,
        request_timeout=args.timeout,
        max_retries=args.max_retries,
        metrics_port=args.metrics_port,
        status_interval=args.status_interval,
        save_body=args.save_body,
        respect_robots_txt=not args.no_robots,
        max_runtime_hours=args.max_runtime_hours,
        checkpoint_interval_seconds=args.checkpoint_interval,
        checkpoint_path=args.checkpoint_path,
        resume_from=args.resume,
        follow_links=args.follow_links,
        max_pages_per_domain=args.max_pages_per_domain,
    )
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
