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
