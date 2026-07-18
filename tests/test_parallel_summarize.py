"""Concurrency machinery tests for the L1/L2 parallel fan-out.

No DB or LLM: these exercise _run_items_parallel and attach_usage_log
directly — overlap, usage aggregation across worker threads, per-item error
isolation, and cooperative cancellation semantics.
"""
import threading
import time

import pytest

from pipeline.summarizer import (
    _run_items_parallel,
    _usage_local,
    attach_usage_log,
    capture_usage,
)


def _items(n):
    return [{"id": i, "item_id": f"{i}", "title": f"Item {i}"} for i in range(1, n + 1)]


def test_runs_in_parallel_and_returns_all_results():
    seen_threads = set()

    def worker(it):
        seen_threads.add(threading.current_thread().name)
        time.sleep(0.15)
        return it["id"]

    t0 = time.monotonic()
    results = _run_items_parallel(
        _items(6), worker, workers=3, level_name="Level 1", progress=lambda m: None
    )
    elapsed = time.monotonic() - t0

    assert sorted(r[1] for r in results) == [1, 2, 3, 4, 5, 6]
    assert all(r[2] is None for r in results)
    assert len(seen_threads) > 1, "never actually used more than one worker"
    # 6 × 0.15s sequential = 0.9s; 3 workers ≈ 0.3s. Generous CI margin.
    assert elapsed < 0.7, f"no speedup: {elapsed:.2f}s"


def test_single_worker_is_equivalent_sequential():
    results = _run_items_parallel(
        _items(3), lambda it: it["id"] * 10, workers=1,
        level_name="Level 2", progress=lambda m: None,
    )
    assert sorted(r[1] for r in results) == [10, 20, 30]


def test_worker_usage_lands_in_the_callers_bucket():
    def worker(it):
        # Simulate what _call_llm does: append to the thread-local log —
        # which attach_usage_log has pointed at the job's bucket.
        log = getattr(_usage_local, "log", None)
        assert log is not None, "usage bucket not attached in worker thread"
        log.append({"model": "test", "input_tokens": it["id"], "output_tokens": 0,
                    "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0})
        return True

    with capture_usage() as bucket:
        _run_items_parallel(
            _items(5), worker, workers=3, level_name="Level 1",
            progress=lambda m: None,
        )
    assert len(bucket) == 5
    assert sorted(u["input_tokens"] for u in bucket) == [1, 2, 3, 4, 5]


def test_attach_usage_log_restores_previous_binding():
    with attach_usage_log(["sentinel"]):
        assert _usage_local.log == ["sentinel"]
    assert getattr(_usage_local, "log", None) is None


def test_worker_error_is_isolated_not_fatal():
    def worker(it):
        if it["id"] == 2:
            raise RuntimeError("boom")
        return "ok"

    results = _run_items_parallel(
        _items(3), worker, workers=2, level_name="Level 1", progress=lambda m: None
    )
    by_label = {r[0]: r for r in results}
    assert isinstance(by_label["2"][2], RuntimeError)
    assert by_label["1"][2] is None and by_label["3"][2] is None


def test_cancellation_stops_pending_items():
    """When the progress callback raises (cooperative cancel), pending items
    are dropped, in-flight ones finish, and the exception propagates."""
    started: list[int] = []
    lock = threading.Lock()

    def worker(it):
        with lock:
            started.append(it["id"])
        time.sleep(0.2)
        return True

    class Cancelled(Exception):
        pass

    calls = {"n": 0}

    def progress(msg):
        if msg.endswith("worker(s)…"):
            return  # the kickoff line
        calls["n"] += 1
        if calls["n"] == 1:
            raise Cancelled()

    with pytest.raises(Cancelled):
        _run_items_parallel(
            _items(8), worker, workers=2, level_name="Level 1", progress=progress
        )
    # 8 items, 2 workers, cancelled on the first completion: the first two
    # ran, at most a couple more started before the cancel landed — but
    # nowhere near all 8.
    assert len(started) < 8, f"cancel did not stop submissions: {started}"
