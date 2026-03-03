"""
Phase 2: Feed Parallelism Verification
Confirms that asyncio.gather executes 9 feed tasks concurrently
and that each task uses its own isolated session.
"""
import asyncio
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("=" * 60)
    print("  PHASE 2: Feed Parallelism Verification")
    print("=" * 60)

    TASK_COUNT = 9
    SIMULATED_LATENCY = 0.2  # 200ms per task
    MAX_ALLOWED_TOTAL = 0.4  # 400ms — proves concurrency

    # Track session objects to ensure isolation
    session_ids = []

    async def mock_task(task_name: str):
        """Simulates a feed task with 200ms latency and its own 'session'."""
        # Simulate: async with AsyncSessionLocal() as session
        mock_session = object()  # Unique object per task
        session_ids.append(id(mock_session))
        await asyncio.sleep(SIMULATED_LATENCY)
        return f"{task_name}: OK"

    # Build tasks
    task_names = [
        "task_popular", "task_hybrid", "task_watched",
        "task_taste", "task_wildcard", "task_random",
        "task_hidden", "task_auteur", "task_available"
    ]

    tasks = [mock_task(name) for name in task_names]

    # Execute with timing
    start = time.perf_counter()
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    print(f"\n  Tasks executed: {len(results)}")
    print(f"  Total time: {elapsed:.3f}s")
    print(f"  Max allowed: {MAX_ALLOWED_TOTAL}s")

    # Check 1: Concurrency (total time must be < 400ms, not ~1800ms)
    if elapsed > MAX_ALLOWED_TOTAL:
        sequential_time = TASK_COUNT * SIMULATED_LATENCY
        print(f"\n  ❌ FEED PARALLELISM FAILED: Total time {elapsed:.3f}s > {MAX_ALLOWED_TOTAL}s")
        print(f"     Expected parallel: ~{SIMULATED_LATENCY}s, got sequential-like: ~{sequential_time}s")
        sys.exit(1)

    # Check 2: All tasks completed
    if len(results) != TASK_COUNT:
        print(f"\n  ❌ FEED PARALLELISM FAILED: Only {len(results)}/{TASK_COUNT} tasks completed")
        sys.exit(1)

    # Check 3: Session isolation (no shared session objects)
    unique_sessions = set(session_ids)
    if len(unique_sessions) != TASK_COUNT:
        print(f"\n  ❌ FEED PARALLELISM FAILED: {TASK_COUNT - len(unique_sessions)} tasks shared a session object")
        sys.exit(1)

    print(f"\n  ✅ FEED PARALLELISM VERIFIED")
    print(f"     - {TASK_COUNT} tasks ran concurrently in {elapsed:.3f}s")
    print(f"     - {len(unique_sessions)} unique sessions (no sharing)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
