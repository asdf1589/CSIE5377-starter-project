"""
Post-run domain rollup (crawler-spec.md #5). Run manually after a
crawl finishes -- deliberately NOT run during the live crawl, since
keeping the hot write path (storage.py's append-only JSONL) as simple
as possible was itself a design goal of the original crawler; this
only reads the finished file.

Usage: python3 scripts/summarize.py [--results PATH] [--output-dir DIR]

Every record is bucketed into exactly one of success/fail/robots_blocked
so the three counts always sum to that domain's total record count:
robots_blocked skips are counted separately from fetch failures because
choosing not to fetch (politeness) is a different thing from trying and
failing (reliability) -- conflating them would make robots_blocked
records look like errors in the summary.
"""
import argparse
import csv
import json
import os


def summarize(results_path: str) -> dict:
    domains: dict[str, dict] = {}
    with open(results_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            domain = record.get("domain")
            if not domain:
                continue
            entry = domains.setdefault(
                domain,
                {"first_seen_ts": record["ts"], "success_count": 0, "fail_count": 0, "robots_blocked": 0},
            )
            entry["first_seen_ts"] = min(entry["first_seen_ts"], record["ts"])
            if record.get("robots_blocked"):
                entry["robots_blocked"] += 1
            elif record.get("ok"):
                entry["success_count"] += 1
            else:
                entry["fail_count"] += 1
    return domains


def write_csv(domains: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["domain", "first_seen_ts", "success_count", "fail_count", "robots_blocked"])
        for domain, entry in sorted(domains.items()):
            writer.writerow([
                domain, entry["first_seen_ts"], entry["success_count"],
                entry["fail_count"], entry["robots_blocked"],
            ])


def write_json(domains: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(domains, f, ensure_ascii=False, indent=2)


def main() -> None:
    p = argparse.ArgumentParser(description="Summarize crawler output by domain")
    p.add_argument("--results", default="output/results.jsonl")
    p.add_argument("--output-dir", default="output")
    args = p.parse_args()

    domains = summarize(args.results)
    csv_path = os.path.join(args.output_dir, "domain_summary.csv")
    json_path = os.path.join(args.output_dir, "domain_summary.json")
    write_csv(domains, csv_path)
    write_json(domains, json_path)
    print(f"Summarized {len(domains)} domains -> {csv_path}, {json_path}")


if __name__ == "__main__":
    main()
