import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler.fetcher import fetch


async def test_fetch_captures_html_content_type():
    async def handler(request):
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/page", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            result = await fetch(
                session, str(server.make_url("/page")),
                timeout=5.0, max_retries=0, backoff_base=0.1,
            )
        assert result.ok
        assert result.content_type == "text/html"
    finally:
        await server.close()


async def test_fetch_captures_non_html_content_type():
    async def handler(request):
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            result = await fetch(
                session, str(server.make_url("/api")),
                timeout=5.0, max_retries=0, backoff_base=0.1,
            )
        assert result.content_type == "application/json"
    finally:
        await server.close()
