"""
Central configuration for the crawler.

Kept as a single dataclass so every tunable knob that affects concurrency,
politeness, or reliability is visible in one place -- this is itself an
architecture decision: config sprawl across modules is a common source of
"who set this timeout?!" incidents in real systems.
"""
from dataclasses import dataclass


@dataclass
class CrawlerConfig:
    seeds_file: str = "seeds.txt"
    output_dir: str = "output"

    # --- concurrency ---
    max_concurrency: int = 50          # global cap on in-flight requests
    per_domain_concurrency: int = 2    # cap on in-flight requests to one domain
    per_domain_delay: float = 1.0      # min seconds between request starts to one domain

    # --- reliability ---
    request_timeout: float = 10.0
    max_retries: int = 3
    retry_backoff_base: float = 0.5    # seconds; exponential backoff base

    # --- frontier ---
    queue_maxsize: int = 2000          # bounded queue => backpressure if fetch is slower than enqueue

    # --- politeness ---
    respect_robots_txt: bool = True
    user_agent: str = "EduCrawler/0.1 (+https://example.invalid/bot-info)"

    # --- observability ---
    metrics_port: int = 9090
    status_interval: float = 5.0       # seconds between human-readable status lines

    # --- storage ---
    save_body: bool = False            # if True, persist raw response bodies to disk

    # --- reliability: wall-clock bound for unattended runs (spec #1) ---
    max_runtime_hours: float | None = None

    # --- checkpoint / resume (spec #2) ---
    checkpoint_interval_seconds: float = 300
    checkpoint_path: str = "output/checkpoint.json"
    resume_from: str | None = None

    # --- link following / frontier growth (spec #3) ---
    follow_links: bool = False
    max_pages_per_domain: int = 20
