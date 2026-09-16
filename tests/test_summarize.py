import json

from scripts.summarize import summarize, write_csv, write_json


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_summarize_buckets_by_domain_and_outcome(tmp_path):
    results_path = str(tmp_path / "results.jsonl")
    _write_jsonl(results_path, [
        {"domain": "a.example", "ts": 100, "ok": True, "robots_blocked": False},
        {"domain": "a.example", "ts": 90, "ok": False, "robots_blocked": False},
        {"domain": "a.example", "ts": 110, "ok": False, "robots_blocked": True},
        {"domain": "b.example", "ts": 50, "ok": True, "robots_blocked": False},
    ])

    domains = summarize(results_path)

    assert domains["a.example"]["first_seen_ts"] == 90
    assert domains["a.example"]["success_count"] == 1
    assert domains["a.example"]["fail_count"] == 1
    assert domains["a.example"]["robots_blocked"] == 1
    assert domains["b.example"]["success_count"] == 1


def test_write_csv_and_json_produce_expected_files(tmp_path):
    domains = {"a.example": {"first_seen_ts": 1, "success_count": 2, "fail_count": 0, "robots_blocked": 0}}
    csv_path = str(tmp_path / "out.csv")
    json_path = str(tmp_path / "out.json")

    write_csv(domains, csv_path)
    write_json(domains, json_path)

    with open(csv_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines[0] == "domain,first_seen_ts,success_count,fail_count,robots_blocked"
    assert lines[1] == "a.example,1,2,0,0"

    with open(json_path, encoding="utf-8") as f:
        assert json.load(f) == domains
