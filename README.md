# edu_crawler — a starter project for systems-architecture practice

A single-node, concurrent (asyncio) web crawler seeded from a fixed URL
list (e.g. 1000 URLs). Built and tested end-to-end (see "Verified" below).
The functional goal — fetch a list of URLs — is intentionally simple. The
point of the exercise is everything *around* that: concurrency control,
politeness, failure handling, and observability, done the way a real
system would need them, not the way a 20-line script would skip them.

## Why this shape

| Concern | Design choice | File |
|---|---|---|
| Concurrency | Bounded `asyncio.Queue` frontier + N worker coroutines pulling from it | `frontier.py`, `worker.py` |
| Backpressure | Queue has `maxsize`; `put()` blocks if workers can't keep up | `frontier.py` |
| Politeness | Per-domain semaphore + minimum inter-request delay, independent of the global concurrency cap | `ratelimiter.py` |
| Legal/ethical | robots.txt fetched once per origin, cached, fail-open | `robots.py` |
| Reliability | Retry with exponential backoff + jitter; never swallow `CancelledError` | `fetcher.py` |
| Durability of results | Append-only JSON-lines, not an in-memory list | `storage.py` |
| **Observability** (this run's focus) | Structured JSON logs, Prometheus metrics, live console status line, end-of-run summary | `logging_config.py`, `metrics.py`, `stats.py`, `reporter.py` |
| Graceful shutdown | Ctrl-C races against normal completion; both paths cancel workers the same way | `main.py` |

Each module's docstring explains the *why*, not just the *what* — read
those before modifying, they're the actual "training" content.

## Observability layers (the point of this iteration)

Three layers, three audiences:

1. **Structured logs** (`logging_config.py`) — JSON-lines to stdout.
   Pipe to `jq`, or into Loki/Elasticsearch later. Every fetch, retry,
   and robots-skip logs with `url`, `status`, `elapsed_ms`, `attempt`.
2. **Console status line** (`reporter.py` + `stats.py`) — a plain,
   human-readable line every `--status-interval` seconds. What you
   actually watch while developing locally.
3. **Prometheus metrics** (`metrics.py`) — `:9090/metrics` (configurable),
   scrapeable, graphable, alertable. Mapped explicitly onto the SRE
   "four golden signals":
   - **Latency** → `crawler_fetch_latency_seconds` (histogram)
   - **Traffic** → `rate(crawler_requests_total[1m])`
   - **Errors** → `crawler_errors_total`, `crawler_requests_total{status="4xx"|"5xx"|"error"}`
   - **Saturation** → `crawler_inflight_requests` vs `--concurrency`, `crawler_queue_depth` vs `--queue-maxsize` (config, not currently a flag — see Extension ideas)

An optional local Prometheus + Grafana stack is in `monitoring/` if you
want to actually build a dashboard rather than just curl the endpoint.

## Getting your 1000 seed URLs

`seeds.example.txt` has 12 safe sample URLs so you can run something
immediately. For a real 1000-URL run, put one URL per line in `seeds.txt`
(blank lines and `#`-comments are ignored). Where the list comes from is
up to you — a domain's sitemap.xml, a category page's `<a href>`s, a
public dataset (e.g. Common Crawl's URL index), or your own generated
list. This project deliberately does **not** include a link-following
crawler (see Extension ideas #1) — it fetches exactly the URLs you give
it, which keeps scope (and the risk of accidentally crawling too
aggressively) bounded for a first exercise.

## Running it

```bash
pip install -r requirements.txt
cp seeds.example.txt seeds.txt     # or supply your own 1000-url file
python3 -m crawler.main --seeds seeds.txt --concurrency 50
```

Useful flags (`python3 -m crawler.main --help` for the full list):

```
--concurrency N              global max in-flight requests   (default 50)
--per-domain-concurrency N   max in-flight per domain         (default 2)
--per-domain-delay SEC       min gap between requests/domain  (default 1.0)
--metrics-port PORT          Prometheus /metrics port         (default 9090)
--status-interval SEC        console status line frequency    (default 5)
--save-body                  persist raw response bodies under output/pages/
--no-robots                  disable robots.txt checks (not recommended)
```

Output: `output/results.jsonl` (one record per URL) and
`output/summary.json` (final counts + throughput) after the run.

While it's running: `curl http://localhost:9090/metrics`, or point
Prometheus at it (`monitoring/`).

## Verified

Smoke-tested against a live mix of URLs (httpbin.org, example.com,
python.org, an unresolvable domain, a 404, a slow endpoint, and a
duplicate) during development: dedup, retry-with-backoff on DNS failure,
4xx handling, JSONL output, live `/metrics` scraping mid-run, and clean
shutdown via queue-drain all confirmed working.

## Known limitations / things a "real" version would need

- No link-following (see Extension ideas #1) — this crawls exactly the
  seed list, once each.
- `RobotFileParser` is fetched but not periodically refreshed for very
  long-running crawls (fine for a 1000-URL, single-run job).
- Response bodies aren't parsed (no HTML parsing, no content
  extraction) — this is a *fetcher*, not a scraper. Bolting on
  `BeautifulSoup`/`selectolax` in `storage.py` or a new `parser.py` is
  a natural next exercise.
- Single process: `--concurrency` is bounded by what one event loop and
  one machine's network stack can sustain (low thousands, roughly,
  depending on target latency) — see Extension ideas #2/#3 for scaling
  past that.

## Extension ideas (in roughly increasing order of "architect" difficulty)

1. **Follow links** — turn this into a real crawler: parse HTML, extract
   `<a href>`, normalize + `frontier.add()` new URLs, add a `--max-depth`
   and same-domain-only filter. Forces you to think about crawl traps
   (infinite calendar pages, session-id URLs) and frontier growth.
2. **Durable, resumable frontier** — replace the in-memory
   `asyncio.Queue` + `set()` with something backed by SQLite or Redis, so
   a crashed run can resume instead of restarting from scratch. Forces
   you to think about at-least-once vs exactly-once processing.
3. **Multi-process / distributed** — split the frontier out into Redis
   or a message queue (RabbitMQ/Kafka) and run multiple crawler
   processes (or machines) pulling from it. This is the natural "next
   scale" once a single event loop's throughput isn't enough, and it's
   where per-domain rate limiting gets genuinely hard (coordination
   across processes, not just within one).
4. **Adaptive rate limiting** — replace the fixed `per_domain_delay`
   with something that backs off automatically when a domain starts
   returning 429/503 (cf. Scrapy's AutoThrottle), and speeds back up
   when it doesn't.
5. **Tracing** — add OpenTelemetry spans around fetch/retry/robots-check
   and export to Jaeger/Tempo. Useful once you have >1 process and need
   to follow one URL's journey across process boundaries.
6. **Circuit breaker per domain** — after N consecutive failures for a
   domain, stop sending it requests for a cooldown period instead of
   continuing to retry into a dead host.
