import asyncio
import json
import os
import subprocess
import sys

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler.config import CrawlerConfig
from crawler.main import _runtime_limit, run

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def test_runtime_limit_sets_stop_event_after_duration():
    stop_event = asyncio.Event()
    await _runtime_limit(hours=1 / 3600000, stop_event=stop_event)  # ~1ms
    assert stop_event.is_set()


async def test_run_end_to_end_writes_results_and_summary(tmp_path):
    async def page(request):
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/page", page)
    server = TestServer(app)
    await server.start_server()
    try:
        seeds_path = str(tmp_path / "seeds.txt")
        with open(seeds_path, "w", encoding="utf-8") as f:
            f.write(str(server.make_url("/page")) + "\n")

        output_dir = str(tmp_path / "output")
        config = CrawlerConfig(
            seeds_file=seeds_path,
            output_dir=output_dir,
            max_concurrency=2,
            metrics_port=0,  # ephemeral port: avoids clashing with other tests/real runs
            checkpoint_interval_seconds=1000,  # won't fire during this short test
            checkpoint_path=os.path.join(output_dir, "checkpoint.json"),
            respect_robots_txt=False,
        )
        await run(config)

        assert os.path.exists(os.path.join(output_dir, "summary.json"))
        with open(os.path.join(output_dir, "results.jsonl"), encoding="utf-8") as f:
            records = [json.loads(line) for line in f]
        assert len(records) == 1
        assert records[0]["ok"] is True
        assert records[0]["domain"]
        # a final checkpoint should be written on shutdown regardless of the
        # periodic interval (set to 1000s above so it can't fire during this
        # short test) -- a clean exit must not discard progress
        assert os.path.exists(os.path.join(output_dir, "checkpoint.json"))
    finally:
        await server.close()


async def test_run_writes_summary_even_if_final_checkpoint_write_fails(tmp_path):
    async def page(request):
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/page", page)
    server = TestServer(app)
    await server.start_server()
    try:
        seeds_path = str(tmp_path / "seeds.txt")
        with open(seeds_path, "w", encoding="utf-8") as f:
            f.write(str(server.make_url("/page")) + "\n")

        output_dir = str(tmp_path / "output")
        config = CrawlerConfig(
            seeds_file=seeds_path,
            output_dir=output_dir,
            max_concurrency=2,
            metrics_port=0,
            checkpoint_interval_seconds=1000,  # periodic loop won't fire during this short test
            checkpoint_path=str(tmp_path / "nonexistent_dir" / "checkpoint.json"),  # final write will fail
            respect_robots_txt=False,
        )
        await run(config)  # must not raise

        assert os.path.exists(os.path.join(output_dir, "summary.json"))
    finally:
        await server.close()


async def test_kill_and_resume_does_not_refetch_completed_urls(tmp_path):
    fetched_paths = []

    async def slow_page(request):
        fetched_paths.append(request.path)
        await asyncio.sleep(0.3)
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    for i in range(6):
        app.router.add_get(f"/p{i}", slow_page)
    server = TestServer(app)
    await server.start_server()
    try:
        seeds_path = str(tmp_path / "seeds.txt")
        with open(seeds_path, "w", encoding="utf-8") as f:
            for i in range(6):
                f.write(str(server.make_url(f"/p{i}")) + "\n")

        output_dir = str(tmp_path / "output")
        ckpt_path = os.path.join(output_dir, "checkpoint.json")
        python = sys.executable  # the same interpreter running this test (the project venv)

        proc = subprocess.Popen(
            [
                python, "-m", "crawler.main",
                "--seeds", seeds_path,
                "--output-dir", output_dir,
                "--concurrency", "1",          # sequential, so timing below is predictable
                "--per-domain-delay", "0",
                "--metrics-port", "0",         # ephemeral: avoids clashing with a real crawler run
                "--checkpoint-interval", "0.2",
                "--checkpoint-path", ckpt_path,
                "--no-robots",
            ],
            cwd=PROJECT_ROOT,
        )
        await asyncio.sleep(1.3)  # ~3 of the 0.3s-per-page fetches complete, plus several checkpoints
        proc.kill()  # SIGKILL on POSIX -- no graceful shutdown runs at all
        proc.wait(timeout=5)

        assert os.path.exists(ckpt_path)
        first_pass_count = len(fetched_paths)
        assert 0 < first_pass_count < 6  # confirms the process was actually killed mid-crawl

        # subprocess.run() blocks synchronously, which would freeze *this*
        # test's event loop -- the same loop the in-process TestServer runs
        # on -- for the whole resumed crawl. The resumed subprocess's HTTP
        # requests would then never get a response (the server can't run
        # its handlers while this loop is blocked), deadlocking until the
        # `timeout` below kills it. asyncio.to_thread() runs the blocking
        # call on a worker thread so this loop (and the TestServer) keeps
        # running while we wait.
        resume_proc = await asyncio.to_thread(
            subprocess.run,
            [
                python, "-m", "crawler.main",
                "--resume", ckpt_path,
                "--output-dir", output_dir,
                "--concurrency", "2",
                "--per-domain-delay", "0",
                "--metrics-port", "0",
                "--checkpoint-path", ckpt_path,
                "--no-robots",
            ],
            cwd=PROJECT_ROOT,
            timeout=15,
        )
        assert resume_proc.returncode == 0

        first_pass_paths = set(fetched_paths[:first_pass_count])
        second_pass_paths = set(fetched_paths[first_pass_count:])
        assert first_pass_paths.isdisjoint(second_pass_paths)
        assert len(set(fetched_paths)) == 6  # all 6 eventually covered across both passes
    finally:
        await server.close()
