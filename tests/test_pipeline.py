"""Tests for the in-process task DAG."""

from __future__ import annotations

import asyncio

import pytest

from small_cap_stack.pipeline import Task, run_dag


def test_runs_in_dependency_order() -> None:
    order: list[str] = []

    def make(name: str):  # type: ignore[no-untyped-def]
        async def _run() -> str:
            order.append(name)
            return name

        return _run

    tasks = [
        Task("a", make("a")),
        Task("b", make("b"), deps=("a",)),
        Task("c", make("c"), deps=("b",)),
    ]
    result = asyncio.run(run_dag(tasks))

    assert result.ok
    assert order == ["a", "b", "c"]
    assert result.results == {"a": "a", "b": "b", "c": "c"}


def test_independent_tasks_run_concurrently() -> None:
    started = 0
    peak = 0

    def make():  # type: ignore[no-untyped-def]
        async def _run() -> None:
            nonlocal started, peak
            started += 1
            peak = max(peak, started)
            await asyncio.sleep(0.02)
            started -= 1

        return _run

    tasks = [Task(f"t{i}", make()) for i in range(3)]
    result = asyncio.run(run_dag(tasks))

    assert result.ok
    assert peak == 3  # all three ran at the same time (no deps)


def test_failure_skips_dependents_but_not_siblings() -> None:
    async def boom() -> None:
        raise RuntimeError("kaboom")

    async def ok() -> str:
        return "ok"

    tasks = [
        Task("bad", boom),
        Task("child", ok, deps=("bad",)),
        Task("sibling", ok),
    ]
    result = asyncio.run(run_dag(tasks))

    assert not result.ok
    assert "bad" in result.failed
    assert isinstance(result.failed["bad"], RuntimeError)
    assert result.skipped == {"child"}
    assert result.results == {"sibling": "ok"}


def test_rejects_unknown_dependency() -> None:
    with pytest.raises(ValueError, match="unknown task"):
        asyncio.run(run_dag([Task("a", _noop, deps=("missing",))]))


def test_rejects_duplicate_task_name() -> None:
    with pytest.raises(ValueError, match="duplicate task name"):
        asyncio.run(run_dag([Task("a", _noop), Task("a", _noop)]))


async def _noop() -> None:
    return None
