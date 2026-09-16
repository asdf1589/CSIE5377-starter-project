"""
Prometheus-style metrics.

These map onto the four "golden signals" (Google SRE book) so the mapping
is worth stating explicitly, since that's the actual transferable lesson
here (not this specific crawler):

    Latency    -> FETCH_LATENCY (histogram)
    Traffic    -> REQUESTS_TOTAL (counter, rate() in PromQL)
    Errors     -> ERRORS_TOTAL, REQUESTS_TOTAL{status="error"|"4xx"|"5xx"}
    Saturation -> INFLIGHT vs max_concurrency, QUEUE_DEPTH vs queue_maxsize

Scrape with Prometheus (see prometheus.yml) or just curl
http://localhost:9090/metrics while the crawler runs.
"""
from prometheus_client import Counter, Gauge, Histogram, start_http_server

REQUESTS_TOTAL = Counter(
    "crawler_requests_total", "HTTP requests attempted, by outcome bucket", ["status"]
)
FETCH_LATENCY = Histogram(
    "crawler_fetch_latency_seconds",
    "Latency of a single HTTP fetch attempt (including failed attempts)",
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30),
)
RETRIES_TOTAL = Counter("crawler_retries_total", "Retry attempts issued")
ERRORS_TOTAL = Counter(
    "crawler_errors_total", "Fetch errors by exception type", ["error_type"]
)
ROBOTS_SKIPPED_TOTAL = Counter(
    "crawler_robots_skipped_total", "URLs skipped due to robots.txt disallow"
)

QUEUE_DEPTH = Gauge("crawler_queue_depth", "URLs currently waiting in the frontier")
INFLIGHT = Gauge("crawler_inflight_requests", "Requests currently in flight")
URLS_SEEN_TOTAL = Gauge("crawler_urls_seen_total", "Distinct URLs seen so far (post-dedup)")


def status_bucket(status: int) -> str:
    if status is None:
        return "error"
    return f"{status // 100}xx"


def start_metrics_server(port: int) -> None:
    start_http_server(port)
