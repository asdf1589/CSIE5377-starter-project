import asyncio
import json
import os

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler.config import CrawlerConfig
from crawler.frontier import Frontier, domain_of
from crawler.ratelimiter import DomainRateLimiter
from crawler.robots import RobotsCache
from crawler.storage import ResultStore
from crawler.worker import worker


async def _run_single_url_through_worker(tmp_path, url, config, session, robots):
    frontier = Frontier(maxsize=10)
    await frontier.add(url)
    rate_limiter = DomainRateLimiter(config.per_domain_concurrency, config.per_domain_delay)
    store = ResultStore(str(tmp_path))
    task = asyncio.create_task(worker(0, frontier, session, rate_limiter, robots, store, config))
    await asyncio.wait_for(frontier.join(), timeout=5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    with open(os.path.join(str(tmp_path), "results.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


async def test_robots_disallowed_url_is_recorded_and_never_fetched(tmp_path):
    hits = {"count": 0}

    async def robots_txt(request):
        return web.Response(text="User-agent: *\nDisallow: /\n")

    async def page(request):
        hits["count"] += 1
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/robots.txt", robots_txt)
    app.router.add_get("/page", page)
    server = TestServer(app)
    await server.start_server()
    try:
        config = CrawlerConfig(request_timeout=5.0, max_retries=0, per_domain_delay=0.0)
        async with aiohttp.ClientSession() as session:
            robots = RobotsCache(session, config.user_agent)
            records = await _run_single_url_through_worker(
                tmp_path, str(server.make_url("/page")), config, session, robots,
            )
        assert hits["count"] == 0
        assert len(records) == 1
        assert records[0]["robots_blocked"] is True
        assert records[0]["status"] is None
    finally:
        await server.close()


async def test_follow_links_caps_per_domain_at_max_pages(tmp_path):
    async def hub(request):
        links = "".join(f'<a href="/same/{i}">l{i}</a>' for i in range(5))
        return web.Response(text=f"<html>{links}</html>", content_type="text/html")

    async def leaf(request):
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/hub", hub)
    for i in range(5):
        app.router.add_get(f"/same/{i}", leaf)
    server = TestServer(app)
    await server.start_server()
    try:
        config = CrawlerConfig(
            request_timeout=5.0, max_retries=0, per_domain_delay=0.0,
            follow_links=True, max_pages_per_domain=3,
        )
        frontier = Frontier(maxsize=20)
        hub_url = str(server.make_url("/hub"))
        domain = domain_of(hub_url)
        await frontier.add(hub_url)
        rate_limiter = DomainRateLimiter(config.per_domain_concurrency, config.per_domain_delay)
        store = ResultStore(str(tmp_path))
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(worker(0, frontier, session, rate_limiter, None, store, config))
            await asyncio.wait_for(frontier.join(), timeout=5)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        # 1 seed + at most 2 followed links == cap of 3; the rest are dropped
        assert frontier.domain_page_count(domain) == 3
    finally:
        await server.close()
