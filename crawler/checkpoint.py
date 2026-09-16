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
