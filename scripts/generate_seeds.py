"""
Generate seeds.txt for edu_crawler.

Two ingredients, combined for different reasons:

1. Hand-picked "hub" pages (directories, list-of-lists, aggregators) —
   these have unusually dense outbound links to many *different* domains,
   which matters once --follow-links is on: they're what actually drives
   the frontier to discover new sites quickly, rather than staying inside
   one cluster.

2. A stratified sample from the Tranco top-sites ranking (a research-
   grade, manipulation-resistant domain popularity list — see
   https://tranco-list.eu, used widely in web/security research, so it's
   citable in your report). "Stratified" means sampling evenly across
   rank tiers (top 1k / 1k-10k / 10k-100k / 100k-500k) instead of just
   taking the top 1000 outright — the global top ~1000 is dominated by a
   handful of tech giants and their own subdomains/CDNs, which would
   under-represent domain diversity and over-represent sites with the
   strongest anti-bot defenses.

Usage:
    pip install tranco --break-system-packages
    python3 generate_seeds.py --count 900 --out seeds.txt
"""
import argparse
import random

from tranco import Tranco

HUB_PAGES = [
    "https://en.wikipedia.org/wiki/Lists_of_websites",
    "https://en.wikipedia.org/wiki/Portal:Contents/Portals",
    "https://en.wikipedia.org/wiki/List_of_news_websites",
    "https://en.wikipedia.org/wiki/List_of_academic_databases_and_search_engines",
    "https://en.wikipedia.org/wiki/List_of_search_engines",
    "https://en.wikipedia.org/wiki/List_of_social_networking_services",
    "https://curlie.org/",
    "https://curlie.org/Computers/Internet",
    "https://curlie.org/Science",
    "https://curlie.org/News",
    "https://curlie.org/Society",
    "https://github.com/sindresorhus/awesome",
    "https://news.ycombinator.com/",
    "https://old.reddit.com/",
    "https://www.w3.org/",
    "https://www.iana.org/domains/root/db",
]

# (lower_rank_inclusive, upper_rank_exclusive)
RANK_TIERS = [(0, 1_000), (1_000, 10_000), (10_000, 100_000), (100_000, 500_000)]


def stratified_domains(count: int, seed: int) -> list[str]:
    random.seed(seed)
    t = Tranco(cache=True, cache_dir=".tranco_cache")
    full = t.list().top(RANK_TIERS[-1][1])
    per_tier = max(1, count // len(RANK_TIERS))
    picked: list[str] = []
    for lo, hi in RANK_TIERS:
        tier = full[lo:hi]
        picked.extend(random.sample(tier, min(per_tier, len(tier))))
    return picked[:count]


def main() -> None:
    p = argparse.ArgumentParser(
        description="Build a seed URL list from hub pages plus a stratified Tranco sample"
    )
    p.add_argument("--count", type=int, default=900, help="domains to sample (excludes hub pages)")
    p.add_argument("--out", default="seeds.txt")
    p.add_argument("--seed", type=int, default=42, help="random seed, for a reproducible sample")
    args = p.parse_args()

    domains = stratified_domains(args.count, args.seed)
    domain_urls = [f"https://{d}/" for d in domains]

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# Hub pages: dense outbound cross-domain links, drive frontier growth\n")
        for u in HUB_PAGES:
            f.write(u + "\n")
        f.write(f"\n# {len(domain_urls)} domains, stratified sample from Tranco ranking\n")
        for u in domain_urls:
            f.write(u + "\n")

    total = len(HUB_PAGES) + len(domain_urls)
    print(f"wrote {total} seed URLs to {args.out} "
          f"({len(HUB_PAGES)} hub pages + {len(domain_urls)} sampled domains)")


if __name__ == "__main__":
    main()
