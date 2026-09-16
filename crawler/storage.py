"""
Result persistence.

Append-only JSON-lines is chosen deliberately over, say, a SQLite table:
it is crash-safe (a partially written last line is the only possible
corruption, and it's trivially detectable/discardable), requires no schema
migration story for a starter project, and is trivial to tail (`tail -f
output/results.jsonl`) or load into pandas/duckdb later. Swap to SQLite
or Postgres once you need concurrent readers, updates, or indexed queries
-- that's a good "next" exercise (see README).
"""
import asyncio
import hashlib
import json
import os
import time

from .fetcher import FetchResult


class ResultStore:
    def __init__(self, output_dir: str, save_body: bool = False):
        self.output_dir = output_dir
        self.save_body = save_body
        os.makedirs(output_dir, exist_ok=True)
        self._log_path = os.path.join(output_dir, "results.jsonl")
        self._lock = asyncio.Lock()
        if self.save_body:
            os.makedirs(os.path.join(output_dir, "pages"), exist_ok=True)

    async def record(self, result: FetchResult) -> None:
        record = {
            "url": result.url,
            "status": result.status,
            "ok": result.ok,
            "elapsed_ms": round(result.elapsed * 1000, 1),
            "attempts": result.attempts,
            "error": result.error,
            "ts": time.time(),
        }
        if self.save_body and result.body:
            digest = hashlib.sha1(result.url.encode()).hexdigest()
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
