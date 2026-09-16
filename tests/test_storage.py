import json
import os

from crawler.fetcher import FetchResult
from crawler.storage import ResultStore


def _read_last_line(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return json.loads(lines[-1])


async def test_record_success_includes_domain_and_no_referrer_by_default(tmp_path):
    store = ResultStore(str(tmp_path))
    result = FetchResult(
        "https://a.example/", status=200, body=b"hi",
        elapsed=0.1, attempts=1, content_type="text/html",
    )
    await store.record("https://a.example/", "a.example", result)

    rec = _read_last_line(os.path.join(str(tmp_path), "results.jsonl"))
    assert rec["domain"] == "a.example"
    assert rec["robots_blocked"] is False
    assert "referrer" not in rec


async def test_record_robots_blocked_writes_null_result_fields(tmp_path):
    store = ResultStore(str(tmp_path))
    await store.record("https://a.example/", "a.example", None, robots_blocked=True)

    rec = _read_last_line(os.path.join(str(tmp_path), "results.jsonl"))
    assert rec["robots_blocked"] is True
    assert rec["status"] is None
    assert rec["ok"] is False
    assert rec["attempts"] == 0


async def test_record_includes_referrer_when_follow_links_on(tmp_path):
    store = ResultStore(str(tmp_path))
    result = FetchResult(
        "https://a.example/child", status=200, body=b"",
        elapsed=0.05, attempts=1, content_type="text/html",
    )
    await store.record(
        "https://a.example/child", "a.example", result,
        referrer="https://a.example/", include_referrer=True,
    )

    rec = _read_last_line(os.path.join(str(tmp_path), "results.jsonl"))
    assert rec["referrer"] == "https://a.example/"


async def test_record_omits_referrer_key_when_follow_links_off(tmp_path):
    store = ResultStore(str(tmp_path))
    result = FetchResult(
        "https://a.example/", status=200, body=b"",
        elapsed=0.05, attempts=1,
    )
    await store.record("https://a.example/", "a.example", result, include_referrer=False)

    rec = _read_last_line(os.path.join(str(tmp_path), "results.jsonl"))
    assert "referrer" not in rec
