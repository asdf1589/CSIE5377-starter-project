import asyncio

from crawler import metrics
from crawler.frontier import Frontier, domain_of


def test_domain_of_extracts_netloc():
    assert domain_of("https://example.com/path") == "example.com"


async def test_add_increments_domain_page_count():
    f = Frontier()
    await f.add("https://a.example/1")
    await f.add("https://a.example/2")
    await f.add("https://b.example/1")
    assert f.domain_page_count("a.example") == 2
    assert f.domain_page_count("b.example") == 1
    assert f.domain_page_count("c.example") == 0


async def test_add_bumps_distinct_domains_metric_only_on_new_domain():
    metrics.DISTINCT_DOMAINS_TOTAL.set(0)
    f = Frontier()
    await f.add("https://a.example/1")
    assert metrics.DISTINCT_DOMAINS_TOTAL._value.get() == 1
    await f.add("https://a.example/2")  # same domain: not a new key
    assert metrics.DISTINCT_DOMAINS_TOTAL._value.get() == 1
    await f.add("https://b.example/1")
    assert metrics.DISTINCT_DOMAINS_TOTAL._value.get() == 2


async def test_duplicate_url_does_not_double_count_domain():
    f = Frontier()
    await f.add("https://a.example/1")
    added_again = await f.add("https://a.example/1")
    assert added_again is False
    assert f.domain_page_count("a.example") == 1


async def test_referrer_of_is_none_for_seed():
    f = Frontier()
    await f.add("https://a.example/")
    assert f.referrer_of("https://a.example/") is None


async def test_referrer_of_returns_source_url_for_followed_link():
    f = Frontier()
    await f.add("https://a.example/child", referrer="https://a.example/")
    assert f.referrer_of("https://a.example/child") == "https://a.example/"


async def test_snapshot_and_restore_round_trip():
    f = Frontier(maxsize=10)
    await f.add("https://a.example/1")
    await f.add("https://a.example/2")
    await f.get()  # pop one item so it's no longer "pending"

    snapshot = f.snapshot_state()
    assert set(snapshot["seen"]) == {"https://a.example/1", "https://a.example/2"}
    assert snapshot["pending"] == ["https://a.example/2"]
    assert snapshot["domain_page_counts"] == {"a.example": 2}

    restored = await Frontier.from_snapshot(snapshot, maxsize=10)
    assert restored.seen_count == 2
    assert restored.qsize() == 1
    assert restored.domain_page_count("a.example") == 2
    # a URL already fetched before the crash must not be re-enqueued
    assert await restored.add("https://a.example/1") is False


async def test_snapshot_includes_url_blocked_on_full_queue():
    f = Frontier(maxsize=1)
    await f.add("https://a.example/1")  # fills the queue (maxsize=1)
    blocked_add = asyncio.create_task(f.add("https://a.example/2"))
    await asyncio.sleep(0.05)  # let the task actually reach the blocking put()
    assert not blocked_add.done()  # confirm it's genuinely blocked, not raced past
    snapshot = f.snapshot_state()
    assert "https://a.example/2" in snapshot["pending"]
    await f.get()  # drain one slot so the blocked add() can complete
    await blocked_add
