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
