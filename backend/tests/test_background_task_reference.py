"""A fire-and-forget task that nobody holds can vanish mid-run (found 2026-11-09).

`routers/upload.py` scheduled the post-upload enrichment of a just-imported library
with a bare `asyncio.create_task(...)`. The event loop keeps only weak references to
its tasks, so a task suspended on I/O that nothing else references is garbage: one
garbage collection destroys it, the enrichment never finishes, and the only trace is
a "Task was destroyed but it is pending" line in the log. Listed since the review of
2026-11-03 (BACKLOG, item 3) and fixed in the base sweep of 2026-11-09.

The first test is the control: it shows the hazard is real on this interpreter, so the
second one cannot pass for the wrong reason.
"""
import asyncio
import gc
import weakref

import pytest

from routers.upload import spawn_background


async def _parked(started: asyncio.Event) -> None:
    started.set()
    # A future nobody else holds: the task's only link to the world while it waits.
    await asyncio.get_running_loop().create_future()


async def _spawn_and_drop(spawn) -> weakref.ref:
    started = asyncio.Event()
    ref = weakref.ref(spawn(_parked(started)))
    await started.wait()
    gc.collect()
    return ref


@pytest.mark.asyncio
async def test_a_bare_create_task_is_collected_while_it_waits():
    ref = await _spawn_and_drop(asyncio.create_task)
    assert ref() is None


@pytest.mark.asyncio
async def test_spawn_background_keeps_the_task_alive_until_it_is_done():
    ref = await _spawn_and_drop(spawn_background)
    task = ref()
    assert task is not None and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
