"""Resumable state: what the harvest has already finished (#431).

The harvest spans ~45 nights whatever #430 decided, and it is killed by design every morning at
03:00. So "where did I get to" has to survive process death, and it has to be **the same answer**
whether the previous run exited cleanly, hit the hard stop, or was OOM-killed mid-session.

Two rules make that true:

- **A session is only recorded once its whole partition is on disk.** :mod:`.runner` writes each
  dataset's file at session end and marks the checkpoint after; a kill anywhere earlier leaves the
  date unmarked, and the next run treats it as never started.
- **A date is never resumed part-way.** Partial output for an incomplete date is discarded, not
  merged (:func:`.runner.discard_partial`). Re-doing one session costs at most an hour of API
  budget; merging a half-written day into the paper book costs a wrong answer nobody would notice,
  because a half-written day extracts perfectly well — just from half the symbols.

The file itself is written atomically (tmp → fsync → rename → fsync dir), the same way
``storage.Store.append`` writes Parquet and for the same reason: the box's standard recovery from
an OOM-thrash is a hard reboot (CLAUDE.md), and a truncated checkpoint would either lose weeks of
completed sessions or, worse, re-declare them done at a different point.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

CHECKPOINT_VERSION = 1


@dataclass
class Checkpoint:
    """Completed work, keyed by trading date. Both phases track separately.

    ``daily_done`` is the grouped-daily/universe pass (~500 calls, run once up front); ``done`` is
    the minute-bar pass that costs ~218 calls a session. They are separate because the first is a
    prerequisite of the second — #428 established the previous daily close as a *required* input,
    not a prefilter nicety (without it the reconstruction fires a median 18 min early) — and
    because a night that only advances the cheap phase must still record that it did.
    """

    path: Path
    done: set[date] = field(default_factory=set)
    daily_done: set[date] = field(default_factory=set)
    calls: int = 0
    updated_at: datetime | None = None

    # -- io ---------------------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Checkpoint:
        """Read the checkpoint, or return an empty one. A corrupt file is *not* silently reset.

        Losing the checkpoint means re-harvesting up to two years of already-paid-for sessions, so
        an unreadable file is an operator problem to look at, not something to paper over.
        """
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text())
        version = int(raw.get("version") or 0)
        if version != CHECKPOINT_VERSION:
            raise ValueError(
                f"{path}: checkpoint version {version}, expected {CHECKPOINT_VERSION} — "
                "inspect it rather than deleting it; it is the record of ~45 nights of API budget"
            )
        return cls(
            path=path,
            done={date.fromisoformat(d) for d in raw.get("done") or []},
            daily_done={date.fromisoformat(d) for d in raw.get("daily_done") or []},
            calls=int(raw.get("calls") or 0),
            updated_at=(
                datetime.fromisoformat(raw["updated_at"]) if raw.get("updated_at") else None
            ),
        )

    def save(self) -> None:
        self.updated_at = datetime.now(UTC)
        body = json.dumps(
            {
                "version": CHECKPOINT_VERSION,
                "done": [d.isoformat() for d in sorted(self.done)],
                "daily_done": [d.isoformat() for d in sorted(self.daily_done)],
                "calls": self.calls,
                "updated_at": self.updated_at.isoformat(),
            },
            indent=2,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(body)
            with open(tmp, "rb") as fh:
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # -- marking ----------------------------------------------------------------------------

    def mark_daily(self, d: date, *, calls: int = 0) -> None:
        self.daily_done.add(d)
        self.calls += calls
        self.save()

    def mark_session(self, d: date, *, calls: int = 0) -> None:
        self.done.add(d)
        self.calls += calls
        self.save()

    @property
    def newest(self) -> date | None:
        return max(self.done) if self.done else None

    @property
    def oldest(self) -> date | None:
        return min(self.done) if self.done else None
