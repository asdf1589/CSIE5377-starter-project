"""
Extract one run, and one bounded time window, from results.jsonl.

storage.py appends and never truncates, so a results.jsonl that has
survived a few aborted starts and a `--resume` contains the records of
*every* run stacked head-to-tail. Nothing in the file marks where one
run stops and the next begins, and the runs overlap in content: a run
started from seeds.txt (rather than `--resume`) re-fetches every seed,
so the same URL legitimately appears once per run. Any per-run or
per-window statistic computed over the raw file is therefore wrong --
it double-counts the seeds and mixes runs with different settings.

The boundary is recovered from summary.json rather than guessed from
the data: `elapsed_s` is measured by the final run alone (main.py's
`time.monotonic()` starts after the checkpoint load), so
`last_record_ts - elapsed_s` lands inside the dead time between the
previous run's last write and the final run's first one. Snapping
forward to the first record at/after that instant gives the final run's
first record exactly. A gap-threshold heuristic was rejected: a
saturated crawler's own stalls reach ~50s, which is longer than the
~29s restart gap in practice, so no threshold separates them.

`bytes_received` is deliberately reported as null. It only ever existed
as a process counter -- individual records carry no response size
unless --save-body was on -- so it cannot be attributed to a window.

Usage: python3 scripts/clean_results.py [--results PATH] [--summary PATH]
                                        [--window-hours N] [--output-dir DIR]
"""
import argparse
import csv
import datetime as dt
import json
import os
from collections.abc import Iterator


def find_run_start_ts(timestamps, elapsed_s: float) -> float:
    """Timestamp of the first record written by the *final* run.

    `elapsed_s` is that run's own wall-clock duration, so anything
    written earlier than `last_ts - elapsed_s` belongs to a previous
    run. Snapping forward (rather than using the bare instant) keeps
    the returned value an actual record timestamp.
    """
    timestamps = list(timestamps)
    if not timestamps:
        raise ValueError("no timestamps: results file is empty")
    target = timestamps[-1] - elapsed_s
    for ts in timestamps:
        if ts >= target:
            return ts
    raise ValueError("no record at or after the computed run start")


def summarize_window(records, start_ts: float, end_ts: float) -> dict:
    """Recompute crawl statistics over an already-windowed record stream.

    Counts mirror reporter.write_summary()'s vocabulary with two
    deliberate differences. `robots_skipped` records are excluded from
    `fetch_attempts` (choosing not to fetch is not a fetch), and
    `records_with_retries` counts *requests that needed at least one
    retry* -- the record schema stores a per-request `attempts` count,
    which cannot reconstruct STATS.retried's total-retries figure.

    Throughput divides by the requested window span, not by the spread
    of the records themselves, so a crawler that died early is reported
    at the low rate it actually sustained.
    """
    span = end_ts - start_ts
    fetch_attempts = robots_skipped = ok = failed = retries = 0
    urls, domains, fetched_domains = set(), set(), set()
    for r in records:
        urls.add(r.get("url"))
        domains.add(r.get("domain"))
        if r.get("robots_blocked"):
            robots_skipped += 1
            continue
        fetched_domains.add(r.get("domain"))
        fetch_attempts += 1
        if r.get("ok"):
            ok += 1
        else:
            failed += 1
        if (r.get("attempts") or 0) > 1:
            retries += 1
    return {
        "span_s": round(span, 2),
        "records": fetch_attempts + robots_skipped,
        "fetch_attempts": fetch_attempts,
        "robots_skipped": robots_skipped,
        "ok": ok,
        "failed": failed,
        "records_with_retries": retries,
        "distinct_urls": len(urls),
        "distinct_domains": len(domains),
        "distinct_domains_fetched": len(fetched_domains),
        "throughput_per_s": round(fetch_attempts / span, 4) if span > 0 else 0,
        "bytes_received": None,
    }


def filter_metrics_csv(src_path: str, dst_path: str, start_ts: float,
                       end_ts: float) -> dict:
    """Keep only the metrics rows scraped inside the window.

    Rows from an earlier run are not merely early: the Prometheus
    counters they carry (requests_total, retries,
    links_dropped_queue_full) live in the crawler process and restart at
    zero, so a rate computed by differencing across the restart reads as
    a large negative spike. Dropping those rows is what makes the
    remaining series safe to difference.
    """
    total = kept = before = after = 0
    with open(src_path, "r", newline="", encoding="utf-8") as src:
        reader = csv.DictReader(src)
        with open(dst_path, "w", newline="", encoding="utf-8") as dst:
            writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                total += 1
                ts = dt.datetime.fromisoformat(row["ts"]).timestamp()
                if ts < start_ts:
                    before += 1
                elif ts >= end_ts:
                    after += 1
                else:
                    kept += 1
                    writer.writerow(row)
    return {"rows_total": total, "rows_kept": kept,
            "rows_before": before, "rows_after": after}


def _iter_records(path) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            yield json.loads(line)


def clean_file(results_path: str, out_path: str, elapsed_s: float,
               window_hours: float) -> dict:
    """Write the final run's first `window_hours` of records to `out_path`.

    Three passes, none of which hold the whole file in memory: collect
    timestamps to locate the run boundary, copy the in-window lines
    through, then re-read the (smaller) output to recompute statistics
    with the same code path the tests cover.
    """
    timestamps = [r["ts"] for r in _iter_records(results_path)]
    start_ts = find_run_start_ts(timestamps, elapsed_s)
    end_ts = start_ts + window_hours * 3600

    total = kept = before = after = 0
    seen_urls = set()
    duplicates = 0
    with open(results_path, "r", encoding="utf-8") as src, \
            open(out_path, "w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            total += 1
            ts = json.loads(line)["ts"]
            if ts < start_ts:
                before += 1
            elif ts >= end_ts:
                after += 1
            else:
                kept += 1
                dst.write(line if line.endswith("\n") else line + "\n")

    for r in _iter_records(out_path):
        url = r.get("url")
        if url in seen_urls:
            duplicates += 1
        else:
            seen_urls.add(url)

    return {
        "source": results_path,
        "output": out_path,
        "run_start_ts": start_ts,
        "window_end_ts": end_ts,
        "window_hours": window_hours,
        "records_total": total,
        "records_dropped_before_run": before,
        "records_dropped_after_window": after,
        "records_kept": kept,
        "duplicate_urls_kept": duplicates,
        "summary": summarize_window(_iter_records(out_path), start_ts, end_ts),
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="Extract one run + one time window from results.jsonl")
    p.add_argument("--results", default="output/results.jsonl")
    p.add_argument("--summary", default="output/summary.json",
                   help="run summary supplying elapsed_s (the final run's duration)")
    p.add_argument("--elapsed-s", type=float, default=None,
                   help="override elapsed_s instead of reading --summary")
    p.add_argument("--window-hours", type=float, default=48.0)
    p.add_argument("--metrics", default="output/metrics_log.csv",
                   help="scraped metrics to window alongside the results (if present)")
    p.add_argument("--output-dir", default="output")
    args = p.parse_args()

    elapsed_s = args.elapsed_s
    if elapsed_s is None:
        with open(args.summary, "r", encoding="utf-8") as f:
            elapsed_s = json.load(f)["elapsed_s"]

    out_path = os.path.join(args.output_dir, "results_clean.jsonl")
    report = clean_file(args.results, out_path, elapsed_s, args.window_hours)

    if os.path.exists(args.metrics):
        metrics_path = os.path.join(args.output_dir, "metrics_log_clean.csv")
        report["metrics"] = filter_metrics_csv(
            args.metrics, metrics_path,
            report["run_start_ts"], report["window_end_ts"])
        report["metrics"]["output"] = metrics_path

    report_path = os.path.join(args.output_dir, "clean_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    summary_path = os.path.join(args.output_dir, "summary_clean.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(report["summary"], f, ensure_ascii=False, indent=2)

    r = report
    print(f"kept {r['records_kept']} of {r['records_total']} records "
          f"({r['window_hours']}h window)")
    print(f"  dropped before final run: {r['records_dropped_before_run']}")
    print(f"  dropped after window:     {r['records_dropped_after_window']}")
    print(f"  duplicate urls kept:      {r['duplicate_urls_kept']}")
    if "metrics" in r:
        m = r["metrics"]
        print(f"metrics rows kept {m['rows_kept']} of {m['rows_total']} "
              f"(dropped {m['rows_before']} pre-run, {m['rows_after']} post-window)")
    print(f"-> {out_path}, {summary_path}, {report_path}")


if __name__ == "__main__":
    main()
