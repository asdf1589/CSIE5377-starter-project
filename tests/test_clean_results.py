import datetime as dt
import json

import pytest

from scripts.clean_results import (
    clean_file,
    filter_metrics_csv,
    find_run_start_ts,
    summarize_window,
)


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _write_csv(path, epochs):
    with open(path, "w", encoding="utf-8") as f:
        f.write("ts,requests_total\n")
        for i, e in enumerate(epochs):
            f.write(f"{dt.datetime.fromtimestamp(e).isoformat()},{i}\n")


def _rec(ts, url="https://a.example/", domain="a.example", ok=True, status=200,
         attempts=1, robots_blocked=False, error=None):
    return {
        "url": url, "domain": domain, "status": status, "ok": ok,
        "elapsed_ms": 10.0, "attempts": attempts, "error": error,
        "robots_blocked": robots_blocked, "ts": ts,
    }


def test_find_run_start_ts_picks_first_record_at_or_after_last_ts_minus_elapsed():
    # an aborted run at t=1..3, then the real run at t=100..102
    timestamps = [1.0, 2.0, 3.0, 100.0, 101.0, 102.0]

    # the real run reported elapsed_s=2.5, so it began at 102-2.5=99.5
    assert find_run_start_ts(timestamps, elapsed_s=2.5) == 100.0


def test_find_run_start_ts_returns_first_timestamp_when_elapsed_spans_whole_file():
    timestamps = [10.0, 20.0, 30.0]

    assert find_run_start_ts(timestamps, elapsed_s=999.0) == 10.0


def test_find_run_start_ts_rejects_empty_input():
    with pytest.raises(ValueError):
        find_run_start_ts([], elapsed_s=1.0)


def test_summarize_window_counts_robots_skips_separately_from_fetch_attempts():
    records = [
        _rec(1.0, url="https://a.example/1"),
        _rec(2.0, url="https://a.example/2", ok=False, status=404),
        _rec(3.0, url="https://a.example/3", ok=False, status=None,
             robots_blocked=True, attempts=0),
    ]

    s = summarize_window(records, start_ts=1.0, end_ts=4.0)

    assert s["fetch_attempts"] == 2
    assert s["robots_skipped"] == 1
    assert s["ok"] == 1
    assert s["failed"] == 1


def test_summarize_window_throughput_uses_window_span_not_record_span():
    records = [_rec(float(t), url=f"https://a.example/{t}") for t in range(10)]

    s = summarize_window(records, start_ts=0.0, end_ts=100.0)

    assert s["span_s"] == 100.0
    assert s["throughput_per_s"] == pytest.approx(0.1)


def test_summarize_window_counts_records_that_needed_a_retry():
    records = [
        _rec(1.0, url="https://a.example/1", attempts=1),
        _rec(2.0, url="https://a.example/2", attempts=3),
    ]

    s = summarize_window(records, start_ts=1.0, end_ts=3.0)

    assert s["records_with_retries"] == 1


def test_summarize_window_reports_bytes_as_none_because_records_carry_no_size():
    s = summarize_window([_rec(1.0)], start_ts=1.0, end_ts=2.0)

    assert s["bytes_received"] is None


def test_summarize_window_separates_domains_touched_from_domains_actually_fetched():
    records = [
        _rec(1.0, url="https://a.example/1", domain="a.example"),
        _rec(2.0, url="https://b.example/1", domain="b.example",
             ok=False, status=None, attempts=0, robots_blocked=True),
    ]

    s = summarize_window(records, start_ts=1.0, end_ts=3.0)

    assert s["distinct_domains"] == 2
    assert s["distinct_domains_fetched"] == 1


def test_filter_metrics_csv_drops_rows_outside_the_window_and_keeps_the_header(tmp_path):
    src, dst = str(tmp_path / "m.csv"), str(tmp_path / "m_out.csv")
    _write_csv(src, [1000.0, 2000.0, 3000.0, 4000.0])

    report = filter_metrics_csv(src, dst, start_ts=2000.0, end_ts=3500.0)

    assert report == {"rows_total": 4, "rows_kept": 2,
                      "rows_before": 1, "rows_after": 1}
    lines = open(dst, encoding="utf-8").read().splitlines()
    assert lines[0] == "ts,requests_total"
    assert len(lines) == 3


def test_filter_metrics_csv_window_end_is_exclusive(tmp_path):
    src, dst = str(tmp_path / "m.csv"), str(tmp_path / "m_out.csv")
    _write_csv(src, [1000.0, 2000.0])

    report = filter_metrics_csv(src, dst, start_ts=1000.0, end_ts=2000.0)

    assert report["rows_kept"] == 1
    assert report["rows_after"] == 1


def test_clean_file_drops_records_written_before_the_final_run(tmp_path):
    src, dst = str(tmp_path / "in.jsonl"), str(tmp_path / "out.jsonl")
    _write_jsonl(src, [
        _rec(1.0, url="https://old.example/"),   # aborted earlier run
        _rec(100.0, url="https://new.example/1"),
        _rec(101.0, url="https://new.example/2"),
    ])

    report = clean_file(src, dst, elapsed_s=1.5, window_hours=48)

    assert report["records_dropped_before_run"] == 1
    kept = [json.loads(line) for line in open(dst, encoding="utf-8")]
    assert [r["url"] for r in kept] == [
        "https://new.example/1", "https://new.example/2",
    ]


def test_clean_file_drops_records_past_the_window(tmp_path):
    src, dst = str(tmp_path / "in.jsonl"), str(tmp_path / "out.jsonl")
    _write_jsonl(src, [
        _rec(0.0, url="https://a.example/in"),
        _rec(3600.0, url="https://a.example/edge"),   # exactly at the edge
        _rec(4000.0, url="https://a.example/out"),
    ])

    report = clean_file(src, dst, elapsed_s=4000.0, window_hours=1)

    assert report["records_kept"] == 1
    assert report["records_dropped_after_window"] == 2
    kept = [json.loads(line) for line in open(dst, encoding="utf-8")]
    assert [r["url"] for r in kept] == ["https://a.example/in"]


def test_clean_file_reports_duplicate_urls_left_in_the_cleaned_window(tmp_path):
    src, dst = str(tmp_path / "in.jsonl"), str(tmp_path / "out.jsonl")
    _write_jsonl(src, [
        _rec(100.0, url="https://a.example/dup"),
        _rec(101.0, url="https://a.example/dup"),
        _rec(102.0, url="https://a.example/other"),
    ])

    report = clean_file(src, dst, elapsed_s=999.0, window_hours=48)

    assert report["duplicate_urls_kept"] == 1


def test_clean_file_skips_blank_lines_without_counting_them(tmp_path):
    src, dst = str(tmp_path / "in.jsonl"), str(tmp_path / "out.jsonl")
    with open(src, "w", encoding="utf-8") as f:
        f.write(json.dumps(_rec(100.0)) + "\n")
        f.write("\n")

    report = clean_file(src, dst, elapsed_s=999.0, window_hours=48)

    assert report["records_total"] == 1
    assert report["records_kept"] == 1
