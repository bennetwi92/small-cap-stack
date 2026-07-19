"""Data-quality canary (#346): positive-confirmation assertions over today's raw captures.

The Healthchecks heartbeat catches *liveness*; this catches *correctness* — the failure mode of a
store-raw/compute-on-read system where a dead float source, a dead news feed, or glitched bars
produce confident wrong opportunities while every liveness signal stays green. Each assertion
here is something that must be TRUE on a healthy day.

The CI watchdog that used to assert these verdicts was rolled back with the rest of the
automation layer (#377), so nothing checks this payload automatically today — it is written for
the dashboard and for manual review. Read it when you want a second opinion on a day's capture.

Cost model: every read is ``dt=``-scoped to today's partition (the Parquet store prices reads by
FILE count — CLAUDE.md), and the app throttles rebuilds to every few minutes, so the canary adds
a small fraction of the existing status-export work.

Published values are counts/percentages plus offender symbols (already public in status.json's
symbol list at P1) — nothing that needs the #344 scrub.

Deliberately deferred: the "no opportunity on a halted symbol" assertion. Volatility halts are
routine for the gappers this strategy trades, so inferring halts from bar gaps would
false-positive near-daily; it needs real halt data (P2 execution observability, #350).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from .config import Settings
from .storage import Store

MAX_OFFENDERS = 5  # symbols listed on a bar-sanity failure — enough to start, never a dump


def _day_frame(store: Store, dataset: str, trading_date: date) -> pl.DataFrame:
    """Today's rows, empty-safe: a missing partition reads as a zero-column frame."""
    frame = store.read(dataset, dt=trading_date)
    if frame.is_empty():
        return pl.DataFrame()
    if "trading_date" in frame.columns:
        frame = frame.filter(pl.col("trading_date") == trading_date)
    return frame


def _float_coverage(
    opportunities: pl.DataFrame, fundamentals: pl.DataFrame, min_coverage: float
) -> dict[str, Any]:
    total = opportunities["opportunity_id"].n_unique() if not opportunities.is_empty() else 0
    covered = 0
    if total and not fundamentals.is_empty():
        covered = fundamentals.filter(pl.col("float_shares").is_not_null())[
            "opportunity_id"
        ].n_unique()
        covered = min(covered, total)
    pct = covered / total if total else None
    return {
        "ok": True if total == 0 else pct is not None and pct >= min_coverage,
        "covered": covered,
        "total": total,
        "pct": round(pct, 3) if pct is not None else None,
    }


def _news_recent(
    opportunities: pl.DataFrame, news: pl.DataFrame, now: datetime, max_age_h: float
) -> dict[str, Any]:
    total = opportunities["opportunity_id"].n_unique() if not opportunities.is_empty() else 0
    rows = 0 if news.is_empty() else news.height
    newest_raw = news["ts_utc"].max() if rows and "ts_utc" in news.columns else None
    age_h: float | None = None
    if isinstance(newest_raw, datetime):  # nulls are ignored by max(); all-null yields None
        newest = newest_raw if newest_raw.tzinfo else newest_raw.replace(tzinfo=UTC)
        age_h = (now - newest).total_seconds() / 3600.0
    # On a breaking-news strategy, a day WITH opportunities but NO fresh story across all of
    # them means the news feed is dead, not that the market was quiet.
    return {
        "ok": True if total == 0 else age_h is not None and age_h <= max_age_h,
        "rows": rows,
        "newest_age_h": round(age_h, 2) if age_h is not None else None,
    }


def _bars_sane(bars: pl.DataFrame, min_bars: int) -> dict[str, Any]:
    if bars.is_empty():
        # Pre-EOD (bars land in the 16:20 ET batch): no verdict yet, and the monitor treats
        # None as indeterminate rather than a pass.
        return {"ok": None, "symbols": 0, "offenders": []}
    day = bars.unique(subset=["symbol", "bar_start_utc"])
    bad = day.filter(
        (pl.col("high") < pl.col("low"))
        | (pl.col("low") <= 0)
        | (pl.col("open") < pl.col("low"))
        | (pl.col("open") > pl.col("high"))
        | (pl.col("close") < pl.col("low"))
        | (pl.col("close") > pl.col("high"))
        | (pl.col("volume") < 0)
    )
    offenders = set(bad["symbol"].to_list())
    counts = day.group_by("symbol").len()
    offenders |= set(counts.filter(pl.col("len") < min_bars)["symbol"].to_list())
    return {
        "ok": not offenders,
        "symbols": day["symbol"].n_unique(),
        "offenders": sorted(offenders)[:MAX_OFFENDERS],
    }


def build_canary(
    store: Store, settings: Settings, now: datetime, trading_date: date
) -> dict[str, Any]:
    """Compute the canary payload — pure over the store, cheap (today's partitions only)."""
    opportunities = _day_frame(store, "opportunities", trading_date)
    return {
        "generated_utc": now.astimezone(UTC).isoformat(),
        "trading_date": trading_date.isoformat(),
        "assertions": {
            "float_coverage": _float_coverage(
                opportunities,
                _day_frame(store, "fundamentals", trading_date),
                settings.canary_min_float_coverage,
            ),
            "news_recent": _news_recent(
                opportunities,
                _day_frame(store, "news", trading_date),
                now,
                settings.canary_news_max_age_h,
            ),
            "bars_sane": _bars_sane(
                _day_frame(store, "bars", trading_date), settings.canary_min_bars
            ),
        },
    }
