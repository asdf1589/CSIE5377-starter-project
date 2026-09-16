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
--max-runtime-hours HOURS    wall-clock auto-stop (e.g. 48)
--checkpoint-interval SEC    seconds between checkpoint writes (default 300)
--checkpoint-path PATH       checkpoint file location (default output/checkpoint.json)
--resume PATH                resume from a checkpoint instead of loading --seeds
--follow-links               enable link extraction / frontier growth
--max-pages-per-domain N     crawl-trap cap; only matters with --follow-links (default 20)
```

Output: `output/results.jsonl` (one record per URL, now including
`domain`, `robots_blocked`, and — when `--follow-links` is on —
`referrer`), `output/summary.json` (final counts + throughput), and
`output/checkpoint.json` (periodic, atomic; used by `--resume`) after
the run. Run `python3 scripts/summarize.py` afterward for a
per-domain `output/domain_summary.csv` / `.json` rollup.

While it's running: `curl http://localhost:9090/metrics`, or point
Prometheus at it (`monitoring/`).

## Verified

Smoke-tested against a live mix of URLs (httpbin.org, example.com,
python.org, an unresolvable domain, a 404, a slow endpoint, and a
duplicate) during development: dedup, retry-with-backoff on DNS failure,
4xx handling, JSONL output, live `/metrics` scraping mid-run, and clean
shutdown via queue-drain all confirmed working.

## Known limitations / things a "real" version would need

- Link-following is opt-in (`--follow-links`, default off) and capped
  at `--max-pages-per-domain` per domain to defend against crawl traps
  — no `--max-depth`, breadth-first via the existing FIFO frontier.
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

1. ~~Follow links~~ — done (`--follow-links`, `linkextract.py`,
   per-domain crawl-trap cap). Next step up: content-aware filtering
   (same-domain-only mode, URL patterns to skip).
2. ~~Durable, resumable frontier~~ — partially done: JSON checkpoint +
   `--resume` survives a crash, but it's still in-memory between
   checkpoints (a `kill -9` between two checkpoint writes loses that
   window's progress) and single-process. Swapping the checkpoint file
   for SQLite/Redis would remove both limits.
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
