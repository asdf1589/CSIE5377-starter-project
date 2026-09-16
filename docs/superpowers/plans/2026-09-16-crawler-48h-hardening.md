# 48-Hour Unattended Crawl Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing `edu_crawler` (asyncio, single-node) with wall-clock auto-stop, crash-safe checkpoint/resume, optional link-following with a per-domain crawl-trap cap, richer JSONL metadata, and a post-run domain summary script — so a single unattended 48-hour run on a lab server survives a crash and produces trustworthy, resumable output.

**Architecture:** Every change is additive to the existing module boundaries (frontier owns dedup + per-domain counts + referrer + checkpoint snapshot; a new `checkpoint.py` owns atomic persistence; a new `linkextract.py` owns `<a href>` discovery; `worker.py` wires robots/fetch/record/link-follow together; `main.py` owns process lifecycle). No module not named in `crawler-spec.md` is touched, and no existing behavior changes when the new flags are left at their defaults.

**Tech Stack:** Python 3.13, `asyncio`, `aiohttp` (already a dependency — its `aiohttp.test_utils` gives us a real local HTTP test server for integration tests with zero new runtime dependencies), `prometheus_client`. New dev-only dependencies: `pytest`, `pytest-asyncio`.

**Spec:** [crawler-spec.md](/home/r14922144/projects/crawler/crawler-spec.md)

## Global Constraints

- Do not restructure or rewrite any file not named in a required-change section of the spec (`config.py`, `logging_config.py`, `metrics.py`, `stats.py`, `frontier.py`, `ratelimiter.py`, `robots.py`, `fetcher.py`, `storage.py`, `worker.py`, `reporter.py`, `main.py` are all pre-existing; only the ones the spec's §1–§4 name get non-trivial edits).
- `ratelimiter.py` and `logging_config.py` and `reporter.py` and `robots.py` are **not modified anywhere in this plan** — no required change touches them.
- Single-node only; no distributed/multi-process changes (spec "Explicitly out of scope").
- No HTML content parsing beyond `<a href>` link discovery — this stays a fetcher, not a scraper (spec "Explicitly out of scope").
- No adaptive/auto-tuning rate limiting (spec "Explicitly out of scope").
- Do **not** add a `--max-depth` parameter (spec §3, explicit).
- `follow_links` config/CLI default stays `False` until the user flips it at launch time (spec TODO #3) — no code in this plan changes that default.
- Do not create or hardcode a real 1000-URL `seeds.txt` (spec TODO #2) — `seeds.example.txt` and the placeholder `seeds.txt` convention are untouched.
- Do not hardcode a specific "metrics to report" format beyond `domain_summary.csv`/`.json` + the existing `summary.json` (spec TODO #1).
- Every new CLI flag name and default must match the spec's "Config / CLI summary" table exactly.

---

## Task 0: Test harness (venv, pytest, pytest-asyncio) + git init

**Files:**
- Create: `/home/r14922144/projects/crawler/.venv/` (via `python3 -m venv`)
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Produces: a working `pytest` invocation (`.venv/bin/pytest -q`) that every later task's tests run under; `pythonpath = .` so `import crawler...` and `import scripts...` both resolve from the repo root without extra sys.path hacks.

- [ ] **Step 1: Create the venv and dev requirements file**

```bash
cd /home/r14922144/projects/crawler
python3 -m venv .venv
```

Create `requirements-dev.txt`:

```
pytest>=8.0
pytest-asyncio>=0.24
```

- [ ] **Step 2: Install everything into the venv**

```bash
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

- [ ] **Step 3: Configure pytest**

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
pythonpath = .
asyncio_mode = auto
```

`asyncio_mode = auto` means `async def test_...` functions run directly without a `@pytest.mark.asyncio` decorator on every single one — every test file in this plan relies on that.

- [ ] **Step 4: Write and run a trivial smoke test**

Create `tests/test_smoke.py`:

```python
import asyncio


def test_sync_smoke():
    assert 1 + 1 == 2


async def test_async_smoke():
    await asyncio.sleep(0)
    assert True
```

Run: `.venv/bin/pytest -q`
Expected: `2 passed`

- [ ] **Step 5: git init this project and make a baseline commit**

The project has no git history yet. Initialize one so every later task's changes are reviewable as a diff.

```bash
cd /home/r14922144/projects/crawler
git init
git config user.name "pwhuang"
git config user.email "jaosnjcky@gmail.com"
```

Create `.gitignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
output/
```

```bash
git add -A
git status   # confirm only expected pre-existing files + .gitignore are staged
git commit -m "$(cat <<'EOF'
Initial commit: existing edu_crawler baseline

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Commit the test harness**

```bash
git add requirements-dev.txt pytest.ini tests/test_smoke.py
git commit -m "$(cat <<'EOF'
test: add pytest + pytest-asyncio harness

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1: Frontier — per-domain page counts + DISTINCT_DOMAINS_TOTAL metric

**Files:**
- Modify: `crawler/frontier.py`
- Modify: `crawler/metrics.py`
- Test: `tests/test_frontier.py` (new)

**Interfaces:**
- Consumes: `metrics.DISTINCT_DOMAINS_TOTAL` (new Gauge, added in this task).
- Produces: `domain_of(url: str) -> str` (module-level function in `frontier.py`); `Frontier.domain_page_count(domain: str) -> int`. Later tasks (3, 10) depend on both.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_frontier.py`:

```python
from crawler import metrics
from crawler.frontier import Frontier, domain_of


def test_domain_of_extracts_netloc():
    assert domain_of("https://example.com/path") == "example.com"


async def test_add_increments_domain_page_count():
    f = Frontier()
    await f.add("https://a.example/1")
    await f.add("https://a.example/2")
    await f.add("https://b.example/1")
    assert f.domain_page_count("a.example") == 2
    assert f.domain_page_count("b.example") == 1
    assert f.domain_page_count("c.example") == 0


async def test_add_bumps_distinct_domains_metric_only_on_new_domain():
    metrics.DISTINCT_DOMAINS_TOTAL.set(0)
    f = Frontier()
    await f.add("https://a.example/1")
    assert metrics.DISTINCT_DOMAINS_TOTAL._value.get() == 1
    await f.add("https://a.example/2")  # same domain: not a new key
    assert metrics.DISTINCT_DOMAINS_TOTAL._value.get() == 1
    await f.add("https://b.example/1")
    assert metrics.DISTINCT_DOMAINS_TOTAL._value.get() == 2


async def test_duplicate_url_does_not_double_count_domain():
    f = Frontier()
    await f.add("https://a.example/1")
    added_again = await f.add("https://a.example/1")
    assert added_again is False
    assert f.domain_page_count("a.example") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_frontier.py`
Expected: FAIL — `ImportError: cannot import name 'domain_of'` (and `metrics.DISTINCT_DOMAINS_TOTAL` doesn't exist yet).

- [ ] **Step 3: Add the metric**

In `crawler/metrics.py`, after the `URLS_SEEN_TOTAL` gauge definition, add:

```python
DISTINCT_DOMAINS_TOTAL = Gauge(
    "crawler_distinct_domains_total",
    "Distinct domains ever added to the frontier (seeds + followed links)",
)
```

- [ ] **Step 4: Implement domain tracking in Frontier**

In `crawler/frontier.py`, add the import and module-level helper right after `normalize_url`:

```python
from . import metrics
```

(add this import near the top, alongside the existing `import asyncio` / `from urllib.parse import ...`)

```python
def domain_of(url: str) -> str:
    """Netloc of a (normalized) URL. Duplicates
    ratelimiter.DomainRateLimiter.domain_of() by design: crawler-spec.md's
    module list says not to touch ratelimiter.py for this change, and
    it's one line, so a shared helper isn't worth the cross-module
    coupling.
    """
    return urlparse(url).netloc
```

In `Frontier.__init__`, add:

```python
        self._domain_page_counts: dict[str, int] = {}
```

Change `add()` from:

```python
    async def add(self, url: str) -> bool:
        """Returns True if the (normalized) URL was newly added."""
        norm = normalize_url(url)
        async with self._lock:
            if norm in self._seen:
                return False
            self._seen.add(norm)
        await self._queue.put(norm)  # may block: that's the backpressure
        return True
```

to:

```python
    async def add(self, url: str) -> bool:
        """Returns True if the (normalized) URL was newly added."""
        norm = normalize_url(url)
        async with self._lock:
            if norm in self._seen:
                return False
            self._seen.add(norm)
            domain = domain_of(norm)
            is_new_domain = domain not in self._domain_page_counts
            self._domain_page_counts[domain] = self._domain_page_counts.get(domain, 0) + 1
        if is_new_domain:
            metrics.DISTINCT_DOMAINS_TOTAL.set(len(self._domain_page_counts))
        await self._queue.put(norm)  # may block: that's the backpressure
        return True

    def domain_page_count(self, domain: str) -> int:
        return self._domain_page_counts.get(domain, 0)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_frontier.py`
Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add crawler/frontier.py crawler/metrics.py tests/test_frontier.py
git commit -m "$(cat <<'EOF'
feat: track per-domain page counts and distinct-domain metric in Frontier

Implements crawler-spec.md #4's DISTINCT_DOMAINS_TOTAL gauge and the
per-domain counter #3's crawl-trap cap will read from.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Frontier — referrer tracking

**Files:**
- Modify: `crawler/frontier.py`
- Test: `tests/test_frontier.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Frontier.add(url, referrer=None)` (referrer param added); `Frontier.referrer_of(url: str) -> str | None`. Task 10 (worker.py) depends on `referrer_of`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_frontier.py`:

```python
async def test_referrer_of_is_none_for_seed():
    f = Frontier()
    await f.add("https://a.example/")
    assert f.referrer_of("https://a.example/") is None


async def test_referrer_of_returns_source_url_for_followed_link():
    f = Frontier()
    await f.add("https://a.example/child", referrer="https://a.example/")
    assert f.referrer_of("https://a.example/child") == "https://a.example/"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_frontier.py`
Expected: FAIL — `TypeError: Frontier.add() got an unexpected keyword argument 'referrer'`

- [ ] **Step 3: Implement referrer tracking**

In `Frontier.__init__`, add:

```python
        self._referrers: dict[str, str | None] = {}
```

Change `add()`'s signature and body to record the referrer:

```python
    async def add(self, url: str, referrer: str | None = None) -> bool:
        """Returns True if the (normalized) URL was newly added."""
        norm = normalize_url(url)
        async with self._lock:
            if norm in self._seen:
                return False
            self._seen.add(norm)
            self._referrers[norm] = referrer
            domain = domain_of(norm)
            is_new_domain = domain not in self._domain_page_counts
            self._domain_page_counts[domain] = self._domain_page_counts.get(domain, 0) + 1
        if is_new_domain:
            metrics.DISTINCT_DOMAINS_TOTAL.set(len(self._domain_page_counts))
        await self._queue.put(norm)  # may block: that's the backpressure
        return True
```

Add a new accessor method (place it next to `domain_page_count`):

```python
    def referrer_of(self, url: str) -> str | None:
        """`url` must already be normalized (i.e. exactly what `get()`
        returned) -- this only looks up entries written by `add()`,
        which stores under the normalized form.
        """
        return self._referrers.get(url)
```

`add_many()` is unchanged: its only caller (seed loading in `main.py`) never has a referrer, so it keeps calling `self.add(u)` with the default `referrer=None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_frontier.py`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/frontier.py tests/test_frontier.py
git commit -m "$(cat <<'EOF'
feat: track per-URL referrer in Frontier for the nullable JSONL field

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Frontier — checkpoint snapshot / restore

**Files:**
- Modify: `crawler/frontier.py`
- Test: `tests/test_frontier.py`

**Interfaces:**
- Consumes: `self._queue._queue` (asyncio.Queue's internal deque — confirmed stable across CPython 3.x, verified against the running interpreter's `asyncio.queues.Queue._init`).
- Produces: `Frontier.snapshot_state() -> dict` with keys `seen`, `pending`, `domain_page_counts`; `Frontier.from_snapshot(snapshot, maxsize=0) -> Frontier` (async classmethod). Task 5 (`checkpoint.py`) and Task 11 (`main.py` resume path) depend on both.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_frontier.py`:

```python
async def test_snapshot_and_restore_round_trip():
    f = Frontier(maxsize=10)
    await f.add("https://a.example/1")
    await f.add("https://a.example/2")
    await f.get()  # pop one item so it's no longer "pending"

    snapshot = f.snapshot_state()
    assert set(snapshot["seen"]) == {"https://a.example/1", "https://a.example/2"}
    assert snapshot["pending"] == ["https://a.example/2"]
    assert snapshot["domain_page_counts"] == {"a.example": 2}

    restored = await Frontier.from_snapshot(snapshot, maxsize=10)
    assert restored.seen_count == 2
    assert restored.qsize() == 1
    assert restored.domain_page_count("a.example") == 2
    # a URL already fetched before the crash must not be re-enqueued
    assert await restored.add("https://a.example/1") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_frontier.py::test_snapshot_and_restore_round_trip`
Expected: FAIL — `AttributeError: 'Frontier' object has no attribute 'snapshot_state'`

- [ ] **Step 3: Implement snapshot / restore**

Add to the `Frontier` class (after `referrer_of`):

```python
    def snapshot_state(self) -> dict:
        """Point-in-time state sufficient to resume (crawler-spec.md #2):
        every URL ever enqueued (`seen`), URLs still waiting for a
        worker (`pending`), and per-domain counts for the crawl-trap
        cap (#3).

        `pending` is only what's still sitting in the queue -- a URL a
        worker already popped via `get()` but hasn't finished
        processing yet is NOT included, so it's lost on a `kill -9`.
        That's an accepted gap: the spec's checkpoint state list is
        exactly {seen, pending queue contents, stats, domain counts},
        and tracking "in-flight, not yet task_done()" URLs separately
        isn't in that list.

        Referrers are also deliberately NOT included, for the same
        reason -- the spec's list omits them, so a resumed run loses
        referrer metadata for not-yet-fetched URLs. That only affects
        an optional JSONL field, never correctness.
        """
        return {
            "seen": list(self._seen),
            "pending": list(self._queue._queue),  # asyncio.Queue's internal deque
            "domain_page_counts": dict(self._domain_page_counts),
        }

    @classmethod
    async def from_snapshot(cls, snapshot: dict, maxsize: int = 0) -> "Frontier":
        frontier = cls(maxsize=maxsize)
        frontier._seen = set(snapshot["seen"])
        frontier._domain_page_counts = dict(snapshot["domain_page_counts"])
        for url in snapshot["pending"]:
            await frontier._queue.put(url)
        metrics.DISTINCT_DOMAINS_TOTAL.set(len(frontier._domain_page_counts))
        return frontier
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest -q tests/test_frontier.py`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/frontier.py tests/test_frontier.py
git commit -m "$(cat <<'EOF'
feat: add Frontier.snapshot_state/from_snapshot for checkpointing

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Stats — restore counters from a checkpoint

**Files:**
- Modify: `crawler/stats.py`
- Test: `tests/test_stats.py` (new)

**Interfaces:**
- Produces: `Stats.load_dict(d: dict) -> None`. Task 11 (`main.py` resume path) depends on this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stats.py`:

```python
from crawler.stats import Stats


def test_load_dict_restores_all_fields():
    s = Stats()
    s.load_dict({
        "fetched": 10, "ok": 8, "failed": 2,
        "retried": 3, "robots_skipped": 1, "bytes_received": 4096,
    })
    assert s.as_dict() == {
        "fetched": 10, "ok": 8, "failed": 2,
        "retried": 3, "robots_skipped": 1, "bytes_received": 4096,
    }


def test_load_dict_defaults_missing_fields_to_zero():
    s = Stats(fetched=99)
    s.load_dict({})
    assert s.as_dict() == {
        "fetched": 0, "ok": 0, "failed": 0,
        "retried": 0, "robots_skipped": 0, "bytes_received": 0,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_stats.py`
Expected: FAIL — `AttributeError: 'Stats' object has no attribute 'load_dict'`

- [ ] **Step 3: Implement `load_dict`**

Add to the `Stats` dataclass in `crawler/stats.py` (after `as_dict`):

```python
    def load_dict(self, d: dict) -> None:
        """Restore counters from a checkpoint (crawler-spec.md #2). Uses
        .get(..., 0) per field so a checkpoint written by an older
        schema doesn't crash a resume -- it just resumes that counter
        at 0.
        """
        self.fetched = d.get("fetched", 0)
        self.ok = d.get("ok", 0)
        self.failed = d.get("failed", 0)
        self.retried = d.get("retried", 0)
        self.robots_skipped = d.get("robots_skipped", 0)
        self.bytes_received = d.get("bytes_received", 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_stats.py`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/stats.py tests/test_stats.py
git commit -m "$(cat <<'EOF'
feat: add Stats.load_dict to restore counters from a checkpoint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: New module `checkpoint.py` — atomic write, load, periodic loop

**Files:**
- Create: `crawler/checkpoint.py`
- Test: `tests/test_checkpoint.py` (new)

**Interfaces:**
- Consumes: `Frontier.snapshot_state()` (Task 3), `stats.STATS.as_dict()` (existing).
- Produces: `write_checkpoint(path, frontier) -> None`, `load_checkpoint(path) -> dict`, `checkpoint_loop(path, interval, frontier) -> None` (coroutine). Task 11 (`main.py`) depends on all three.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_checkpoint.py`:

```python
import asyncio
import os

from crawler import stats
from crawler.checkpoint import checkpoint_loop, load_checkpoint, write_checkpoint
from crawler.frontier import Frontier


async def test_write_checkpoint_then_load_round_trips(tmp_path):
    f = Frontier(maxsize=10)
    await f.add("https://a.example/1")
    await f.get()
    stats.STATS.fetched = 5
    stats.STATS.ok = 4
    try:
        ckpt_path = str(tmp_path / "checkpoint.json")
        write_checkpoint(ckpt_path, f)

        loaded = load_checkpoint(ckpt_path)
        assert loaded["frontier"]["seen"] == ["https://a.example/1"]
        assert loaded["frontier"]["pending"] == []
        assert loaded["stats"]["fetched"] == 5
        assert loaded["stats"]["ok"] == 4
    finally:
        stats.STATS.fetched = 0
        stats.STATS.ok = 0


def test_write_checkpoint_is_atomic_no_leftover_tmp_file(tmp_path):
    f = Frontier(maxsize=10)
    ckpt_path = str(tmp_path / "checkpoint.json")
    write_checkpoint(ckpt_path, f)
    assert os.path.exists(ckpt_path)
    assert not os.path.exists(ckpt_path + ".tmp")


async def test_checkpoint_loop_writes_periodically(tmp_path):
    f = Frontier(maxsize=10)
    await f.add("https://a.example/1")
    ckpt_path = str(tmp_path / "checkpoint.json")

    task = asyncio.create_task(checkpoint_loop(ckpt_path, 0.05, f))
    await asyncio.sleep(0.12)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert os.path.exists(ckpt_path)
    loaded = load_checkpoint(ckpt_path)
    assert loaded["frontier"]["seen"] == ["https://a.example/1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_checkpoint.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'crawler.checkpoint'`

- [ ] **Step 3: Implement `crawler/checkpoint.py`**

```python
"""
Periodic, crash-safe checkpointing so a killed run can resume roughly
where it left off (crawler-spec.md #2).

Atomic write: write to a temp file in the same directory, then
os.replace() over the previous checkpoint. os.replace() is atomic on
POSIX and Windows, so a crash mid-write can never leave a torn/partial
checkpoint file -- the last completed os.replace() is always intact,
even if the process is killed between the write() and the replace().
"""
import asyncio
import json
import logging
import os

from . import stats

logger = logging.getLogger("crawler.checkpoint")


def write_checkpoint(path: str, frontier) -> None:
    state = {
        "frontier": frontier.snapshot_state(),
        "stats": stats.STATS.as_dict(),
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp_path, path)


def load_checkpoint(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def checkpoint_loop(path: str, interval: float, frontier) -> None:
    try:
        while True:
            await asyncio.sleep(interval)
            write_checkpoint(path, frontier)
            logger.info(f"checkpoint_written path={path}")
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_checkpoint.py`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/checkpoint.py tests/test_checkpoint.py
git commit -m "$(cat <<'EOF'
feat: add checkpoint.py for atomic, periodic crawl-state persistence

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Fetcher — capture response content-type

**Files:**
- Modify: `crawler/fetcher.py`
- Test: `tests/test_fetcher.py` (new)

**Interfaces:**
- Produces: `FetchResult.content_type: str | None`. Task 10 (`worker.py`'s link-following gate) depends on this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetcher.py`:

```python
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler.fetcher import fetch


async def test_fetch_captures_html_content_type():
    async def handler(request):
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/page", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            result = await fetch(
                session, str(server.make_url("/page")),
                timeout=5.0, max_retries=0, backoff_base=0.1,
            )
        assert result.ok
        assert result.content_type == "text/html"
    finally:
        await server.close()


async def test_fetch_captures_non_html_content_type():
    async def handler(request):
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            result = await fetch(
                session, str(server.make_url("/api")),
                timeout=5.0, max_retries=0, backoff_base=0.1,
            )
        assert result.content_type == "application/json"
    finally:
        await server.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_fetcher.py`
Expected: FAIL — `AttributeError: 'FetchResult' object has no attribute 'content_type'`

- [ ] **Step 3: Implement**

In `crawler/fetcher.py`, add a field to `FetchResult`:

```python
@dataclass
class FetchResult:
    url: str
    status: int | None = None
    body: bytes | None = None
    error: str | None = None
    elapsed: float = 0.0
    attempts: int = 0
    content_type: str | None = None
```

Change the success-path return in `fetch()` from:

```python
                return FetchResult(url, status=resp.status, body=body, elapsed=elapsed, attempts=attempt)
```

to:

```python
                return FetchResult(
                    url, status=resp.status, body=body, elapsed=elapsed,
                    attempts=attempt, content_type=resp.content_type,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_fetcher.py`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/fetcher.py tests/test_fetcher.py
git commit -m "$(cat <<'EOF'
feat: capture response content-type in FetchResult

Needed by crawler-spec.md #3 to gate link-following on HTML responses.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Storage — domain / robots_blocked / referrer fields

**Files:**
- Modify: `crawler/storage.py`
- Test: `tests/test_storage.py` (new)

**Interfaces:**
- Consumes: `FetchResult` (Task 6, for `content_type` indirectly via `result` — storage itself doesn't branch on content_type, but `result` may now carry it).
- Produces: new `ResultStore.record(url, domain, result, *, robots_blocked=False, referrer=None, include_referrer=False)` signature. Task 10 (`worker.py`) depends on this exact signature.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage.py`:

```python
import json
import os

from crawler.fetcher import FetchResult
from crawler.storage import ResultStore


def _read_last_line(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return json.loads(lines[-1])


async def test_record_success_includes_domain_and_no_referrer_by_default(tmp_path):
    store = ResultStore(str(tmp_path))
    result = FetchResult(
        "https://a.example/", status=200, body=b"hi",
        elapsed=0.1, attempts=1, content_type="text/html",
    )
    await store.record("https://a.example/", "a.example", result)

    rec = _read_last_line(os.path.join(str(tmp_path), "results.jsonl"))
    assert rec["domain"] == "a.example"
    assert rec["robots_blocked"] is False
    assert "referrer" not in rec


async def test_record_robots_blocked_writes_null_result_fields(tmp_path):
    store = ResultStore(str(tmp_path))
    await store.record("https://a.example/", "a.example", None, robots_blocked=True)

    rec = _read_last_line(os.path.join(str(tmp_path), "results.jsonl"))
    assert rec["robots_blocked"] is True
    assert rec["status"] is None
    assert rec["ok"] is False
    assert rec["attempts"] == 0


async def test_record_includes_referrer_when_follow_links_on(tmp_path):
    store = ResultStore(str(tmp_path))
    result = FetchResult(
        "https://a.example/child", status=200, body=b"",
        elapsed=0.05, attempts=1, content_type="text/html",
    )
    await store.record(
        "https://a.example/child", "a.example", result,
        referrer="https://a.example/", include_referrer=True,
    )

    rec = _read_last_line(os.path.join(str(tmp_path), "results.jsonl"))
    assert rec["referrer"] == "https://a.example/"


async def test_record_omits_referrer_key_when_follow_links_off(tmp_path):
    store = ResultStore(str(tmp_path))
    result = FetchResult(
        "https://a.example/", status=200, body=b"",
        elapsed=0.05, attempts=1,
    )
    await store.record("https://a.example/", "a.example", result, include_referrer=False)

    rec = _read_last_line(os.path.join(str(tmp_path), "results.jsonl"))
    assert "referrer" not in rec
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_storage.py`
Expected: FAIL — `TypeError: ResultStore.record() takes 2 positional arguments but 3 were given` (current signature is `record(self, result)`)

- [ ] **Step 3: Implement the new `record()`**

Replace `ResultStore.record()` in `crawler/storage.py` with:

```python
    async def record(
        self,
        url: str,
        domain: str,
        result: FetchResult | None,
        *,
        robots_blocked: bool = False,
        referrer: str | None = None,
        include_referrer: bool = False,
    ) -> None:
        """Append one JSONL record.

        `result` is None for a robots-blocked skip (crawler-spec.md
        #4) -- no fetch was attempted, so there's no status/elapsed/
        attempts to report. `include_referrer` controls whether the
        (nullable) `referrer` key is written AT ALL: omitted entirely
        when link-following is off, so a fixed-list run's schema
        doesn't grow a column nobody ever populates.
        """
        record = {
            "url": url,
            "domain": domain,
            "status": result.status if result is not None else None,
            "ok": result.ok if result is not None else False,
            "elapsed_ms": round(result.elapsed * 1000, 1) if result is not None else 0.0,
            "attempts": result.attempts if result is not None else 0,
            "error": result.error if result is not None else None,
            "robots_blocked": robots_blocked,
            "ts": time.time(),
        }
        if include_referrer:
            record["referrer"] = referrer
        if self.save_body and result is not None and result.body:
            digest = hashlib.sha1(url.encode()).hexdigest()
            path = os.path.join(self.output_dir, "pages", f"{digest}.bin")
            with open(path, "wb") as f:
                f.write(result.body)
            record["body_path"] = path
            record["body_bytes"] = len(result.body)

        line = json.dumps(record, ensure_ascii=False)
        # Single-writer, append-only: the lock only matters because multiple
        # worker coroutines call record() concurrently and file writes
        # aren't atomic across `await` points if this weren't serialized.
        async with self._lock:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_storage.py`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/storage.py tests/test_storage.py
git commit -m "$(cat <<'EOF'
feat: add domain/robots_blocked/referrer fields to JSONL records

Implements crawler-spec.md #4. robots-blocked skips are now recorded
(previously silently dropped); referrer is omitted entirely when
follow_links is off.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: New module `linkextract.py` — `<a href>` discovery

**Files:**
- Create: `crawler/linkextract.py`
- Test: `tests/test_linkextract.py` (new)

**Interfaces:**
- Produces: `extract_links(base_url: str, html_text: str) -> list[str]` (absolute URLs). Task 10 (`worker.py`) depends on this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_linkextract.py`:

```python
from crawler.linkextract import extract_links


def test_extracts_absolute_and_relative_links():
    html = '<a href="https://other.example/x">x</a><a href="/relative">rel</a>'
    links = extract_links("https://a.example/page", html)
    assert links == ["https://other.example/x", "https://a.example/relative"]


def test_ignores_non_anchor_tags_and_empty_href():
    html = '<link href="/style.css"><a href="">empty</a><a>no href</a>'
    links = extract_links("https://a.example/", html)
    assert links == []


def test_tolerates_malformed_html_without_raising():
    html = '<a href="/ok">ok</a><div><a href="/also-ok"'
    links = extract_links("https://a.example/", html)
    assert "https://a.example/ok" in links
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_linkextract.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'crawler.linkextract'`

- [ ] **Step 3: Implement `crawler/linkextract.py`**

```python
"""
Cheap `<a href>` extraction for optional frontier growth (crawler-
spec.md #3). Deliberately not a general HTML parser: no DOM, no
attribute validation beyond what's needed to grab an href value.
`html.parser.HTMLParser` is the stdlib's SAX-style tokenizer -- exactly
enough for "give me every href on this page" without pulling in
BeautifulSoup/lxml for a fetcher, not a scraper (see crawler-spec.md's
"Explicitly out of scope").
"""
from html.parser import HTMLParser
from urllib.parse import urljoin


class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def extract_links(base_url: str, html_text: str) -> list[str]:
    """Returns absolute URLs for every <a href> found, resolved against
    base_url. Malformed HTML is tolerated (HTMLParser is lenient by
    design); a hard parse error just yields whatever was found before
    the error, never raises -- link discovery is a bonus, not
    something that should crash a worker fetching legitimate content.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    return [urljoin(base_url, href) for href in parser.hrefs]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_linkextract.py`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add crawler/linkextract.py tests/test_linkextract.py
git commit -m "$(cat <<'EOF'
feat: add linkextract.py for stdlib-only <a href> discovery

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Config + CLI plumbing for all new flags

**Files:**
- Modify: `crawler/config.py`
- Modify: `crawler/main.py` (`parse_args`, `main`)
- Test: `tests/test_main_cli.py` (new)

**Interfaces:**
- Produces: `CrawlerConfig` fields `max_runtime_hours`, `checkpoint_interval_seconds`, `checkpoint_path`, `resume_from`, `follow_links`, `max_pages_per_domain`; `parse_args(argv: list[str] | None = None)` (now testable without touching `sys.argv`). Task 10 and Task 11 consume `config.follow_links`, `config.max_pages_per_domain`; Task 11 consumes the rest.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main_cli.py`:

```python
from crawler.main import parse_args


def test_new_flags_default_to_spec_values():
    args = parse_args([])
    assert args.max_runtime_hours is None
    assert args.checkpoint_interval == 300
    assert args.checkpoint_path == "output/checkpoint.json"
    assert args.resume is None
    assert args.follow_links is False
    assert args.max_pages_per_domain == 20


def test_new_flags_parse_when_provided():
    args = parse_args([
        "--max-runtime-hours", "48",
        "--checkpoint-interval", "10",
        "--checkpoint-path", "ckpt.json",
        "--resume", "ckpt.json",
        "--follow-links",
        "--max-pages-per-domain", "5",
    ])
    assert args.max_runtime_hours == 48.0
    assert args.checkpoint_interval == 10.0
    assert args.checkpoint_path == "ckpt.json"
    assert args.resume == "ckpt.json"
    assert args.follow_links is True
    assert args.max_pages_per_domain == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_main_cli.py`
Expected: FAIL — `TypeError: parse_args() takes 0 positional arguments but 1 was given`

- [ ] **Step 3: Add the new config fields**

Append to the `CrawlerConfig` dataclass in `crawler/config.py` (after the `save_body` field):

```python
    # --- reliability: wall-clock bound for unattended runs (spec #1) ---
    max_runtime_hours: float | None = None

    # --- checkpoint / resume (spec #2) ---
    checkpoint_interval_seconds: float = 300
    checkpoint_path: str = "output/checkpoint.json"
    resume_from: str | None = None

    # --- link following / frontier growth (spec #3) ---
    follow_links: bool = False
    max_pages_per_domain: int = 20
```

- [ ] **Step 4: Refactor `parse_args` to accept an argv override, and add the new flags**

Replace `parse_args` in `crawler/main.py` with:

```python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Educational single-node concurrent crawler")
    p.add_argument("--seeds", default="seeds.txt", help="path to newline-delimited seed URL file")
    p.add_argument("--output-dir", default="output")
    p.add_argument("--concurrency", type=int, default=50, help="global max in-flight requests")
    p.add_argument("--per-domain-concurrency", type=int, default=2)
    p.add_argument("--per-domain-delay", type=float, default=1.0, help="seconds between requests to same domain")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--metrics-port", type=int, default=9090)
    p.add_argument("--status-interval", type=float, default=5.0)
    p.add_argument("--save-body", action="store_true", help="persist raw response bodies under output/pages/")
    p.add_argument("--no-robots", action="store_true", help="disable robots.txt checking (not recommended)")
    p.add_argument("--max-runtime-hours", type=float, default=None, help="wall-clock auto-stop (e.g. 48)")
    p.add_argument("--checkpoint-interval", type=float, default=300, help="seconds between checkpoint writes")
    p.add_argument("--checkpoint-path", default="output/checkpoint.json")
    p.add_argument("--resume", default=None, help="resume from a checkpoint file instead of loading --seeds")
    p.add_argument("--follow-links", action="store_true", help="enable link extraction / frontier growth")
    p.add_argument(
        "--max-pages-per-domain", type=int, default=20,
        help="crawl-trap cap; only matters with --follow-links",
    )
    return p.parse_args(argv)
```

- [ ] **Step 5: Wire the new args into `main()`**

Replace the `CrawlerConfig(...)` construction in `main()` with:

```python
    config = CrawlerConfig(
        seeds_file=args.seeds,
        output_dir=args.output_dir,
        max_concurrency=args.concurrency,
        per_domain_concurrency=args.per_domain_concurrency,
        per_domain_delay=args.per_domain_delay,
        request_timeout=args.timeout,
        max_retries=args.max_retries,
        metrics_port=args.metrics_port,
        status_interval=args.status_interval,
        save_body=args.save_body,
        respect_robots_txt=not args.no_robots,
        max_runtime_hours=args.max_runtime_hours,
        checkpoint_interval_seconds=args.checkpoint_interval,
        checkpoint_path=args.checkpoint_path,
        resume_from=args.resume,
        follow_links=args.follow_links,
        max_pages_per_domain=args.max_pages_per_domain,
    )
```

(`main()` still calls `parse_args()` with no arguments, which now just forwards `None` to `argparse`, i.e. "read `sys.argv`" — identical behavior to before.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_main_cli.py`
Expected: `2 passed`

- [ ] **Step 7: Commit**

```bash
git add crawler/config.py crawler/main.py tests/test_main_cli.py
git commit -m "$(cat <<'EOF'
feat: add CLI flags and config fields for runtime limit, checkpoint/
resume, and link-following

Wires crawler-spec.md's full "Config / CLI summary" table. Behavior is
unchanged when none of the new flags are passed. run()'s own use of
these fields lands in a later commit.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Worker — domain calc, robots_blocked recording, link-following + crawl-trap cap

**Files:**
- Modify: `crawler/worker.py`
- Test: `tests/test_worker.py` (new)

**Interfaces:**
- Consumes: `frontier.domain_of`/`normalize_url` (Tasks 1, 3 — via `crawler.frontier` module), `frontier.referrer_of`/`domain_page_count` (Tasks 1, 2), `extract_links` (Task 8), `result.content_type` (Task 6), `store.record(url, domain, result, ...)` (Task 7), `config.follow_links`/`config.max_pages_per_domain` (Task 9).
- Produces: no new public interface — this task's output is the wired *behavior* Task 11's integration tests will exercise end-to-end.

- [ ] **Step 1: Write the failing test for robots-blocked recording**

Create `tests/test_worker.py`:

```python
import asyncio
import json
import os

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler.config import CrawlerConfig
from crawler.frontier import Frontier, domain_of
from crawler.ratelimiter import DomainRateLimiter
from crawler.robots import RobotsCache
from crawler.storage import ResultStore
from crawler.worker import worker


async def _run_single_url_through_worker(tmp_path, url, config, session, robots):
    frontier = Frontier(maxsize=10)
    await frontier.add(url)
    rate_limiter = DomainRateLimiter(config.per_domain_concurrency, config.per_domain_delay)
    store = ResultStore(str(tmp_path))
    task = asyncio.create_task(worker(0, frontier, session, rate_limiter, robots, store, config))
    await asyncio.wait_for(frontier.join(), timeout=5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    with open(os.path.join(str(tmp_path), "results.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


async def test_robots_disallowed_url_is_recorded_and_never_fetched(tmp_path):
    hits = {"count": 0}

    async def robots_txt(request):
        return web.Response(text="User-agent: *\nDisallow: /\n")

    async def page(request):
        hits["count"] += 1
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/robots.txt", robots_txt)
    app.router.add_get("/page", page)
    server = TestServer(app)
    await server.start_server()
    try:
        config = CrawlerConfig(request_timeout=5.0, max_retries=0, per_domain_delay=0.0)
        async with aiohttp.ClientSession() as session:
            robots = RobotsCache(session, config.user_agent)
            records = await _run_single_url_through_worker(
                tmp_path, str(server.make_url("/page")), config, session, robots,
            )
        assert hits["count"] == 0
        assert len(records) == 1
        assert records[0]["robots_blocked"] is True
        assert records[0]["status"] is None
    finally:
        await server.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_worker.py`
Expected: FAIL — assertion `len(records) == 1` fails with `0` (the robots-blocked path currently does `continue` without calling `store.record`).

- [ ] **Step 3: Write the failing test for link-following + crawl-trap cap**

Append to `tests/test_worker.py`:

```python
async def test_follow_links_caps_per_domain_at_max_pages(tmp_path):
    async def hub(request):
        links = "".join(f'<a href="/same/{i}">l{i}</a>' for i in range(5))
        return web.Response(text=f"<html>{links}</html>", content_type="text/html")

    async def leaf(request):
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/hub", hub)
    for i in range(5):
        app.router.add_get(f"/same/{i}", leaf)
    server = TestServer(app)
    await server.start_server()
    try:
        config = CrawlerConfig(
            request_timeout=5.0, max_retries=0, per_domain_delay=0.0,
            follow_links=True, max_pages_per_domain=3,
        )
        frontier = Frontier(maxsize=20)
        hub_url = str(server.make_url("/hub"))
        domain = domain_of(hub_url)
        await frontier.add(hub_url)
        rate_limiter = DomainRateLimiter(config.per_domain_concurrency, config.per_domain_delay)
        store = ResultStore(str(tmp_path))
        async with aiohttp.ClientSession() as session:
            task = asyncio.create_task(worker(0, frontier, session, rate_limiter, None, store, config))
            await asyncio.wait_for(frontier.join(), timeout=5)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        # 1 seed + at most 2 followed links == cap of 3; the rest are dropped
        assert frontier.domain_page_count(domain) == 3
    finally:
        await server.close()
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_worker.py`
Expected: FAIL — `frontier.domain_page_count(domain) == 1` (no link-following happens yet), and the robots test from Step 1 still fails too.

- [ ] **Step 5: Implement the worker changes**

Replace `crawler/worker.py` with:

```python
"""
A single worker: pull one URL, respect politeness, fetch, record, repeat.

Cancellation contract: the caller (main.py) cancels these tasks after the
frontier drains. When cancelled mid-fetch, `fetch()` re-raises
CancelledError immediately (see fetcher.py) and this function's `finally`
block still runs, so in-flight/semaphore bookkeeping stays correct even on
an abrupt shutdown (e.g. Ctrl-C).
"""
import logging

from . import metrics, stats
from .fetcher import fetch
from .frontier import domain_of, normalize_url
from .linkextract import extract_links
from .robots import RobotsCache

logger = logging.getLogger("crawler.worker")


async def worker(
    worker_id: int,
    frontier,
    session,
    rate_limiter,
    robots: RobotsCache | None,
    store,
    config,
) -> None:
    while True:
        url = await frontier.get()
        domain = domain_of(url)
        referrer = frontier.referrer_of(url)
        try:
            if robots is not None:
                allowed = await robots.is_allowed(url)
                if not allowed:
                    metrics.ROBOTS_SKIPPED_TOTAL.inc()
                    stats.STATS.robots_skipped += 1
                    logger.info("robots_disallowed", extra={"url": url, "worker_id": worker_id})
                    await store.record(
                        url, domain, None,
                        robots_blocked=True, referrer=referrer,
                        include_referrer=config.follow_links,
                    )
                    continue

            await rate_limiter.acquire(url)
            metrics.INFLIGHT.inc()
            try:
                result = await fetch(
                    session,
                    url,
                    timeout=config.request_timeout,
                    max_retries=config.max_retries,
                    backoff_base=config.retry_backoff_base,
                )
                await store.record(
                    url, domain, result,
                    robots_blocked=False, referrer=referrer,
                    include_referrer=config.follow_links,
                )
                if config.follow_links and result.ok and result.content_type == "text/html":
                    await _follow_links(url, result, frontier, config)
            finally:
                metrics.INFLIGHT.dec()
                rate_limiter.release(domain)
        finally:
            metrics.QUEUE_DEPTH.set(frontier.qsize())
            frontier.task_done()


async def _follow_links(source_url: str, result, frontier, config) -> None:
    """Extract <a href> from a fetched HTML page and enqueue new ones,
    subject to the per-domain crawl-trap cap (crawler-spec.md #3).

    The cap is checked against each candidate LINK's own domain (not
    the referring page's domain): the trap this defends against is one
    domain generating unbounded same-domain links (e.g. an infinite
    calendar), so the budget that must be capped is "how many pages of
    THIS domain are already known to the frontier". Checking the
    referrer's domain instead would block legitimate cross-domain
    discovery from a single popular hub page -- the opposite of
    crawler-spec.md's breadth-first, many-distinct-domains bias.
    """
    if not result.body:
        return
    html_text = result.body.decode("utf-8", errors="ignore")
    for link in extract_links(source_url, html_text):
        norm_link = normalize_url(link)
        link_domain = domain_of(norm_link)
        if frontier.domain_page_count(link_domain) >= config.max_pages_per_domain:
            continue
        await frontier.add(link, referrer=source_url)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_worker.py`
Expected: `2 passed`

- [ ] **Step 7: Run the full suite so far**

Run: `.venv/bin/pytest -q`
Expected: all previously-added tests still pass (no regressions from the signature/behavior changes).

- [ ] **Step 8: Commit**

```bash
git add crawler/worker.py tests/test_worker.py
git commit -m "$(cat <<'EOF'
feat: record robots-blocked skips and add link-following with a
per-domain crawl-trap cap

Implements crawler-spec.md #3 and the robots_blocked half of #4. The
cap is checked against each candidate link's own domain, not the
referring page's -- see the docstring on _follow_links for why.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Main — wall-clock auto-stop, checkpoint loop, resume-from-checkpoint

**Files:**
- Modify: `crawler/main.py` (`run`, plus a new `_runtime_limit` helper)
- Test: `tests/test_main_integration.py` (new)

**Interfaces:**
- Consumes: `Frontier.from_snapshot` (Task 3), `stats.STATS.load_dict` (Task 4), `checkpoint.checkpoint_loop`/`load_checkpoint` (Task 5), `config.max_runtime_hours`/`checkpoint_interval_seconds`/`checkpoint_path`/`resume_from` (Task 9).
- Produces: the fully wired `run()` this whole spec is about. Nothing downstream depends on new interfaces from this task.

- [ ] **Step 1: Write the failing unit test for the runtime-limit helper**

Create `tests/test_main_integration.py`:

```python
import asyncio
import json
import os

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler.config import CrawlerConfig
from crawler.main import _runtime_limit, run


async def test_runtime_limit_sets_stop_event_after_duration():
    stop_event = asyncio.Event()
    await _runtime_limit(hours=1 / 3600000, stop_event=stop_event)  # ~1ms
    assert stop_event.is_set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_main_integration.py`
Expected: FAIL — `ImportError: cannot import name '_runtime_limit'`

- [ ] **Step 3: Write the failing end-to-end test**

Append to `tests/test_main_integration.py`:

```python
async def test_run_end_to_end_writes_results_and_summary(tmp_path):
    async def page(request):
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/page", page)
    server = TestServer(app)
    await server.start_server()
    try:
        seeds_path = str(tmp_path / "seeds.txt")
        with open(seeds_path, "w", encoding="utf-8") as f:
            f.write(str(server.make_url("/page")) + "\n")

        output_dir = str(tmp_path / "output")
        config = CrawlerConfig(
            seeds_file=seeds_path,
            output_dir=output_dir,
            max_concurrency=2,
            metrics_port=0,  # ephemeral port: avoids clashing with other tests/real runs
            checkpoint_interval_seconds=1000,  # won't fire during this short test
            checkpoint_path=os.path.join(output_dir, "checkpoint.json"),
            respect_robots_txt=False,
        )
        await run(config)

        assert os.path.exists(os.path.join(output_dir, "summary.json"))
        with open(os.path.join(output_dir, "results.jsonl"), encoding="utf-8") as f:
            records = [json.loads(line) for line in f]
        assert len(records) == 1
        assert records[0]["ok"] is True
        assert records[0]["domain"]
    finally:
        await server.close()
```

- [ ] **Step 4: Write the failing kill-and-resume acceptance test**

This is the automated version of crawler-spec.md #2's named acceptance test ("start a run, kill it after ~1 minute, restart with `--resume`, confirm it continues from roughly where it left off"). It must use a **real subprocess** killed with `SIGKILL`, not an in-process `task.cancel()`: `run()` has no `try/finally` around its worker/checkpoint/reporter task creation, so cancelling the `run()` coroutine directly does not cancel those child tasks — they'd keep running orphaned in the *same* event loop and race with a second, resumed `run()` call in the same test process. A subprocess killed with `SIGKILL` has no such problem: the whole process (and every task in it) dies at once, exactly like a real `kill -9`.

Add these imports to the top of `tests/test_main_integration.py` (`os` is already imported from Step 1 — just add `subprocess`, `sys`, and the derived constant):

```python
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
```

Append the test:

```python
async def test_kill_and_resume_does_not_refetch_completed_urls(tmp_path):
    fetched_paths = []

    async def slow_page(request):
        fetched_paths.append(request.path)
        await asyncio.sleep(0.3)
        return web.Response(text="<html></html>", content_type="text/html")

    app = web.Application()
    for i in range(6):
        app.router.add_get(f"/p{i}", slow_page)
    server = TestServer(app)
    await server.start_server()
    try:
        seeds_path = str(tmp_path / "seeds.txt")
        with open(seeds_path, "w", encoding="utf-8") as f:
            for i in range(6):
                f.write(str(server.make_url(f"/p{i}")) + "\n")

        output_dir = str(tmp_path / "output")
        ckpt_path = os.path.join(output_dir, "checkpoint.json")
        python = sys.executable  # the same interpreter running this test (the project venv)

        proc = subprocess.Popen(
            [
                python, "-m", "crawler.main",
                "--seeds", seeds_path,
                "--output-dir", output_dir,
                "--concurrency", "1",          # sequential, so timing below is predictable
                "--per-domain-delay", "0",
                "--metrics-port", "0",         # ephemeral: avoids clashing with a real crawler run
                "--checkpoint-interval", "0.2",
                "--checkpoint-path", ckpt_path,
                "--no-robots",
            ],
            cwd=PROJECT_ROOT,
        )
        await asyncio.sleep(1.0)  # ~3 of the 0.3s-per-page fetches complete, plus several checkpoints
        proc.kill()  # SIGKILL on POSIX -- no graceful shutdown runs at all
        proc.wait(timeout=5)

        assert os.path.exists(ckpt_path)
        first_pass_count = len(fetched_paths)
        assert 0 < first_pass_count < 6  # confirms the process was actually killed mid-crawl

        resume_proc = subprocess.run(
            [
                python, "-m", "crawler.main",
                "--resume", ckpt_path,
                "--output-dir", output_dir,
                "--concurrency", "2",
                "--per-domain-delay", "0",
                "--metrics-port", "0",
                "--checkpoint-path", ckpt_path,
                "--no-robots",
            ],
            cwd=PROJECT_ROOT,
            timeout=15,
        )
        assert resume_proc.returncode == 0

        first_pass_paths = set(fetched_paths[:first_pass_count])
        second_pass_paths = set(fetched_paths[first_pass_count:])
        assert first_pass_paths.isdisjoint(second_pass_paths)
        assert len(set(fetched_paths)) == 6  # all 6 eventually covered across both passes
    finally:
        await server.close()
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_main_integration.py`
Expected: FAIL — `test_run_end_to_end...` and the resume test both error because `resume_from`/checkpoint writing isn't wired into `run()` yet (the checkpoint file never appears).

- [ ] **Step 6: Implement `_runtime_limit` and wire checkpoint/resume/runtime-limit into `run()`**

Add these imports to the top of `crawler/main.py`:

```python
from . import stats
from .checkpoint import checkpoint_loop, load_checkpoint
```

Add the helper (place it above `run()`):

```python
async def _runtime_limit(hours: float, stop_event: asyncio.Event) -> None:
    await asyncio.sleep(hours * 3600)
    logger.warning(f"max_runtime_reached hours={hours}; stopping")
    stop_event.set()
```

Replace the body of `run()` with:

```python
async def run(config: CrawlerConfig) -> None:
    setup_logging()
    metrics.start_metrics_server(config.metrics_port)
    logger.info(f"metrics_server_started port={config.metrics_port}")

    if config.resume_from:
        checkpoint = load_checkpoint(config.resume_from)
        frontier = await Frontier.from_snapshot(checkpoint["frontier"], maxsize=config.queue_maxsize)
        stats.STATS.load_dict(checkpoint["stats"])
        logger.info(
            f"resumed_from_checkpoint path={config.resume_from} "
            f"seen={frontier.seen_count} pending={frontier.qsize()}"
        )
    else:
        seeds = load_seeds(config.seeds_file)
        frontier = Frontier(maxsize=config.queue_maxsize)
        added = await frontier.add_many(seeds)
        logger.info(f"seeds_loaded added={added} in_file={len(seeds)} duplicates={len(seeds) - added}")

    metrics.URLS_SEEN_TOTAL.set(frontier.seen_count)
    metrics.QUEUE_DEPTH.set(frontier.qsize())

    rate_limiter = DomainRateLimiter(config.per_domain_concurrency, config.per_domain_delay)
    store = ResultStore(config.output_dir, save_body=config.save_body)

    connector = aiohttp.TCPConnector(limit=config.max_concurrency)
    headers = {"User-Agent": config.user_agent}

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # not available on some platforms (e.g. Windows event loop)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        robots = RobotsCache(session, config.user_agent) if config.respect_robots_txt else None

        worker_tasks = [
            asyncio.create_task(
                worker(i, frontier, session, rate_limiter, robots, store, config),
                name=f"worker-{i}",
            )
            for i in range(config.max_concurrency)
        ]
        reporter_task = asyncio.create_task(status_reporter(frontier, config.status_interval))
        checkpoint_task = asyncio.create_task(
            checkpoint_loop(config.checkpoint_path, config.checkpoint_interval_seconds, frontier)
        )
        runtime_task = (
            asyncio.create_task(_runtime_limit(config.max_runtime_hours, stop_event))
            if config.max_runtime_hours is not None
            else None
        )

        start = time.monotonic()
        join_task = asyncio.create_task(frontier.join())
        stop_task = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            [join_task, stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done:
            logger.warning("shutdown_signal_received; cancelling workers")
        for t in pending:
            t.cancel()

        reporter_task.cancel()
        checkpoint_task.cancel()
        if runtime_task is not None:
            runtime_task.cancel()
        for t in worker_tasks:
            t.cancel()
        background_tasks = [reporter_task, checkpoint_task]
        if runtime_task is not None:
            background_tasks.append(runtime_task)
        await asyncio.gather(*worker_tasks, *background_tasks, join_task, stop_task, return_exceptions=True)

        elapsed = time.monotonic() - start
        logger.info(f"crawl_complete elapsed_s={elapsed:.1f} urls_seen={frontier.seen_count}")
        write_summary(config.output_dir, elapsed, frontier.seen_count)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_main_integration.py`
Expected: `4 passed`

If `test_kill_and_resume_does_not_refetch_completed_urls` is flaky on a slower machine (timing-sensitive `asyncio.sleep(1.0)` before the kill), increase that sleep slightly rather than removing the test — the assertion `0 < first_pass_count < 6` is intentionally loose to absorb normal timing variance.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass (this is the first point where every module's changes are integrated together).

- [ ] **Step 9: Commit**

```bash
git add crawler/main.py tests/test_main_integration.py
git commit -m "$(cat <<'EOF'
feat: wire wall-clock auto-stop, periodic checkpointing, and
resume-from-checkpoint into run()

Implements crawler-spec.md #1 and #2. The runtime-limit task sets the
same stop_event Ctrl-C already sets, so write_summary() still runs on
that path, unchanged. Includes an automated, subprocess+SIGKILL version
of the spec's named kill -9 + --resume acceptance test.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `scripts/summarize.py` — post-run domain summary

**Files:**
- Create: `scripts/__init__.py` (empty, makes `scripts` importable from tests)
- Create: `scripts/summarize.py`
- Test: `tests/test_summarize.py` (new)

**Interfaces:**
- Consumes: `output/results.jsonl` records with the `domain`/`robots_blocked`/`ok`/`ts` fields Task 7 added.
- Produces: `output/domain_summary.csv` and `output/domain_summary.json`. Nothing else in this plan depends on this module.

- [ ] **Step 1: Write the failing tests**

Create `scripts/__init__.py` (empty file).

Create `tests/test_summarize.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest -q tests/test_summarize.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.summarize'`

- [ ] **Step 3: Implement `scripts/summarize.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q tests/test_summarize.py`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/summarize.py tests/test_summarize.py
git commit -m "$(cat <<'EOF'
feat: add scripts/summarize.py for post-run domain rollup

Implements crawler-spec.md #5: domain_summary.csv + domain_summary.json,
run manually after a crawl finishes.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: README updates

**Files:**
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the flags list**

In the "Useful flags" section, add the new flags (keep the existing ones as-is):

```
--max-runtime-hours HOURS    wall-clock auto-stop (e.g. 48)
--checkpoint-interval SEC    seconds between checkpoint writes (default 300)
--checkpoint-path PATH       checkpoint file location (default output/checkpoint.json)
--resume PATH                resume from a checkpoint instead of loading --seeds
--follow-links                enable link extraction / frontier growth
--max-pages-per-domain N     crawl-trap cap; only matters with --follow-links (default 20)
```

- [ ] **Step 2: Update the "Output" paragraph**

Change:

```
Output: `output/results.jsonl` (one record per URL) and
`output/summary.json` (final counts + throughput) after the run.
```

to:

```
Output: `output/results.jsonl` (one record per URL, now including
`domain`, `robots_blocked`, and — when `--follow-links` is on —
`referrer`), `output/summary.json` (final counts + throughput), and
`output/checkpoint.json` (periodic, atomic; used by `--resume`) after
the run. Run `python3 scripts/summarize.py` afterward for a
per-domain `output/domain_summary.csv` / `.json` rollup.
```

- [ ] **Step 3: Update "Known limitations"**

Change:

```
- No link-following (see Extension ideas #1) — this crawls exactly the
  seed list, once each.
```

to:

```
- Link-following is opt-in (`--follow-links`, default off) and capped
  at `--max-pages-per-domain` per domain to defend against crawl traps
  — no `--max-depth`, breadth-first via the existing FIFO frontier.
```

- [ ] **Step 4: Update "Extension ideas"**

Change item 1 (`Follow links — turn this into a real crawler...`) and item 2 (`Durable, resumable frontier...`) to:

```
1. ~~Follow links~~ — done (`--follow-links`, `linkextract.py`,
   per-domain crawl-trap cap). Next step up: content-aware filtering
   (same-domain-only mode, URL patterns to skip).
2. ~~Durable, resumable frontier~~ — partially done: JSON checkpoint +
   `--resume` survives a crash, but it's still in-memory between
   checkpoints (a `kill -9` between two checkpoint writes loses that
   window's progress) and single-process. Swapping the checkpoint file
   for SQLite/Redis would remove both limits.
```

- [ ] **Step 5: Verify flag names match `parse_args` exactly**

```bash
cd /home/r14922144/projects/crawler
grep -oP "add_argument\(\"\K--[a-z-]+" crawler/main.py | sort
```

Cross-check every name in this output appears in the README's flags list with the same spelling.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: document new flags, output files, and updated extension ideas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Manual validation pass (crawler-spec.md's own checklist)

This task cannot be executed by an automated test — it requires real elapsed time, a real terminal/tmux session, and (for item 4) real external sites. It reproduces crawler-spec.md's "Validation before the real 48h run" section using the now-implemented flags. Run these yourself before the real unattended run; do not skip any of them even though Tasks 0–13 already have automated coverage for the underlying logic — this is what proves the *process*, not just the code, behaves correctly.

- [ ] **Step 1: 20–50 seed smoke test**

```bash
cd /home/r14922144/projects/crawler
cp seeds.example.txt seeds.txt
.venv/bin/python3 -m crawler.main --seeds seeds.txt --concurrency 10
```

Confirm: no crash, `output/results.jsonl` has one line per seed URL, the console status line moves, `output/summary.json` is written at the end.

- [ ] **Step 2: 1–2 hour test at real target concurrency**

Run at the concurrency/politeness settings you intend for the real 48h run, against a real, larger seed list, for 1–2 hours. Confirm throughput (the `rate=` figure in the status line) doesn't degrade over time. If testing with `--follow-links`, confirm the distinct-domain count (`curl -s localhost:9090/metrics | grep crawler_distinct_domains_total`) grows over the run rather than flatlining.

- [ ] **Step 3: Kill-and-resume test (the actual insurance policy for the one-shot run)**

```bash
cd /home/r14922144/projects/crawler
.venv/bin/python3 -m crawler.main --seeds seeds.txt --concurrency 10 \
    --checkpoint-interval 10 --checkpoint-path output/checkpoint.json &
PID=$!
sleep 60
kill -9 $PID
```

Confirm `output/checkpoint.json` exists and is valid JSON (`python3 -m json.tool output/checkpoint.json`), then:

```bash
.venv/bin/python3 -m crawler.main --resume output/checkpoint.json --concurrency 10
```

Confirm the console log line `resumed_from_checkpoint ...` appears, `seen`/`pending` counts look like "roughly where it left off," and it does not start by re-fetching URLs from the top of `seeds.txt`.

- [ ] **Step 4: robots.txt compliance against real disallowing sites**

Add 2–3 URLs from sites known to disallow crawling (e.g. any site with `Disallow: /` in its live `robots.txt` — check first with `curl https://SITE/robots.txt`) to a small test seeds file, run the crawler against it, and confirm:

```bash
grep '"robots_blocked": true' output/results.jsonl
```

produces a line for each such URL, and that the site's own access logs (if you control one) or your own crawler's log (`grep robots_disallowed` in the JSON logs) confirm no actual page fetch was attempted.

- [ ] **Step 5: tmux / SSH-disconnect survival**

```bash
tmux new -s crawl
.venv/bin/python3 -m crawler.main --seeds seeds.txt --concurrency 10
# detach: Ctrl-b d
```

Wait a minute, close your SSH session entirely, reconnect, `tmux attach -t crawl`, confirm the crawler is still running and the status line has kept advancing.

- [ ] **Step 6: Final commit**

Once all five checks pass, note the confirmation in `crawler-spec.md`'s validation section is now satisfied (no code change needed — this is a checklist, not a code artifact) and you're clear to launch the real 48-hour run with the seed list and `--follow-links` decision from spec TODO #2/#3 resolved.

---

## Post-plan reminders (do not act on these without the user)

- `crawler-spec.md`'s three TODOs are explicitly left unresolved by this plan: the real 1000-URL seed list, the final "metrics to report" format, and whether `--follow-links` should be on for the real run. All three are deferred to the user per the spec's own instructions ("do not guess these").
- The real 48h launch command still needs to be assembled by hand from the validated flags (this plan does not write that command anywhere) once TODO #2/#3 are resolved.
