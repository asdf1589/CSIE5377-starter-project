import asyncio


def test_sync_smoke():
    assert 1 + 1 == 2


async def test_async_smoke():
    await asyncio.sleep(0)
    assert True
