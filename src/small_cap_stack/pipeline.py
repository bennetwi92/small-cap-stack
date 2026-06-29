"""In-process task DAG: 'processes spawn managed tasks, tasks can have dependencies'.

Tasks declare dependencies by name. ``run_dag`` runs them in topological order, executing
independent tasks concurrently. A task that raises is recorded as failed and its dependents are
skipped (fault isolation) — the rest of the DAG still runs. This is why we use
``asyncio.gather(return_exceptions=True)`` rather than a ``TaskGroup`` (which would cancel
siblings on the first error).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from graphlib import TopologicalSorter
from typing import Any

from .logging import get_logger

log = get_logger(__name__)

TaskFn = Callable[[], Coroutine[Any, Any, object]]


@dataclass(frozen=True)
class Task:
    """A unit of work in the pipeline."""

    name: str
    run: TaskFn
    deps: tuple[str, ...] = field(default=())


@dataclass
class DagResult:
    """Outcome of a DAG run."""

    results: dict[str, object]
    failed: dict[str, BaseException]
    skipped: set[str]

    @property
    def ok(self) -> bool:
        return not self.failed and not self.skipped


def _validate(tasks: list[Task]) -> dict[str, Task]:
    by_name: dict[str, Task] = {}
    for t in tasks:
        if t.name in by_name:
            raise ValueError(f"duplicate task name: {t.name!r}")
        by_name[t.name] = t
    for t in tasks:
        for dep in t.deps:
            if dep not in by_name:
                raise ValueError(f"task {t.name!r} depends on unknown task {dep!r}")
    return by_name


async def run_dag(tasks: list[Task]) -> DagResult:
    """Execute tasks honouring dependencies; independent tasks run concurrently."""
    by_name = _validate(tasks)
    sorter: TopologicalSorter[str] = TopologicalSorter({t.name: set(t.deps) for t in tasks})
    sorter.prepare()

    results: dict[str, object] = {}
    failed: dict[str, BaseException] = {}
    skipped: set[str] = set()

    while sorter.is_active():
        ready = list(sorter.get_ready())
        runnable: dict[str, asyncio.Task[object]] = {}
        for name in ready:
            task = by_name[name]
            if any(dep in failed or dep in skipped for dep in task.deps):
                skipped.add(name)
                log.warning("task.skipped", task=name, reason="dependency_failed")
                sorter.done(name)
                continue
            log.info("task.start", task=name)
            runnable[name] = asyncio.create_task(task.run(), name=name)

        if not runnable:
            continue

        outcomes = await asyncio.gather(*runnable.values(), return_exceptions=True)
        for name, outcome in zip(runnable, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                failed[name] = outcome
                log.error("task.failed", task=name, error=str(outcome))
            else:
                results[name] = outcome
                log.info("task.done", task=name)
            sorter.done(name)

    return DagResult(results=results, failed=failed, skipped=skipped)
