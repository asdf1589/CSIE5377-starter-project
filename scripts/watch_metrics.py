"""
Zero-dependency, no-install live view of the crawler's /metrics endpoint.

Prometheus + Grafana (monitoring/docker-compose.yml) need Docker, which
isn't installed on this machine, and installing it needs root on a
shared lab server. This script needs nothing beyond the Python standard
library: it polls /metrics, prints a compact live dashboard, and appends
every sample to a CSV log -- so a proper growth chart (e.g. distinct
domains discovered over time) can be plotted later even if nobody was
watching the terminal the whole 48 hours.

Usage:
    python3 scripts/watch_metrics.py
    python3 scripts/watch_metrics.py --port 9090 --interval 5 --log output/metrics_log.csv

Run this in a separate tmux pane/window from the crawler itself
(Ctrl-b " to split, or Ctrl-b c for a new window) -- it only reads
/metrics over HTTP, it has no effect on the running crawl either way.
"""
import argparse
import csv
import os
import time
import urllib.error
import urllib.request


def parse_metrics(text: str) -> dict:
    """Turn a Prometheus text-exposition payload into {metric_key: value},
    where metric_key includes any label suffix verbatim (e.g.
    'crawler_requests_total{status="2xx"}') -- generic enough to handle
    both labelled and unlabelled lines without knowing every metric name
    in advance.
    """
    values = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        try:
            key, value = line.rsplit(" ", 1)
            values[key] = float(value)
        except ValueError:
            continue
    return values


def sum_prefixed(values: dict, prefix: str) -> float:
    return sum(v for k, v in values.items() if k.startswith(prefix))


def fmt(v) -> str:
    return f"{v:.0f}" if isinstance(v, (int, float)) else "?"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Poll the crawler's /metrics endpoint and log each sample to CSV"
    )
    p.add_argument("--port", type=int, default=9090, help="the crawler's --metrics-port")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    p.add_argument("--log", default="output/metrics_log.csv", help="CSV file to append samples to")
    args = p.parse_args()

    log_dir = os.path.dirname(args.log)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    is_new_log = not os.path.exists(args.log)
    log_file = open(args.log, "a", newline="", encoding="utf-8")
    writer = csv.writer(log_file)
    if is_new_log:
        writer.writerow([
            "ts", "distinct_domains", "urls_seen_at_startup", "queue_depth",
            "inflight", "requests_total", "robots_skipped", "retries",
            "links_dropped_queue_full",
        ])

    start = time.monotonic()
    try:
        while True:
            try:
                text = urllib.request.urlopen(
                    f"http://localhost:{args.port}/metrics", timeout=5
                ).read().decode()
                values = parse_metrics(text)

                distinct_domains = values.get("crawler_distinct_domains_total")
                urls_seen = values.get("crawler_urls_seen_total")
                queue_depth = values.get("crawler_queue_depth")
                inflight = values.get("crawler_inflight_requests")
                requests_total = sum_prefixed(values, "crawler_requests_total{")
                robots_skipped = values.get("crawler_robots_skipped_total")
                retries = values.get("crawler_retries_total")
                links_dropped = values.get("crawler_links_dropped_queue_full_total")

                writer.writerow([
                    time.strftime("%Y-%m-%dT%H:%M:%S"), distinct_domains, urls_seen,
                    queue_depth, inflight, requests_total, robots_skipped, retries,
                    links_dropped,
                ])
                log_file.flush()

                elapsed_min = (time.monotonic() - start) / 60
                print("\033c", end="")  # clear the terminal for a live-dashboard feel
                print(f"crawler metrics -- polling :{args.port}/metrics every {args.interval:.0f}s "
                      f"(watching {elapsed_min:.1f} min, logging to {args.log})\n")
                print(f"  distinct domains discovered      : {fmt(distinct_domains)}")
                print(f"  urls_seen_total (fixed at startup, does NOT update live -- ignore during a --follow-links run)")
                print(f"                                    : {fmt(urls_seen)}")
                print(f"  queue depth                       : {fmt(queue_depth)}")
                print(f"  in-flight requests                : {fmt(inflight)}")
                print(f"  requests attempted (all outcomes) : {fmt(requests_total)}")
                print(f"  robots-skipped                    : {fmt(robots_skipped)}")
                print(f"  retries                           : {fmt(retries)}")
                print(f"  links dropped (queue was full)    : {fmt(links_dropped)}")
                print("\n(Ctrl-C stops this dashboard only -- the crawler itself keeps running)")
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                print("\033c", end="")
                print(f"[metrics unreachable on port {args.port}: {e}"
                      f" -- crawler may still be starting up, or has stopped]")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped watching (crawler unaffected).")
    finally:
        log_file.close()


if __name__ == "__main__":
    main()
