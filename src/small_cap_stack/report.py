"""End-of-day report (issue #19): per-opportunity stats computed on read from raw data.

Joins the raw datasets (opportunities + scanner_hits + news + fundamentals + bars) for a trading
day and, per opportunity, computes float/news signals, bull-flag setups, and R-metrics
(would-trigger? Max R, MAE, stop-out). Everything is derived on read, so changing the
methodology and re-running reproduces history (store-raw / compute-on-read).

**Re-entry segmentation (#36):** a symbol can form more than one opportunity in a day — it pops,
fades, then pops again later (e.g. pre-market open, then market open). Segmentation happens here at
analysis time from the raw `scanner_hits`: a gap of >= `reentry_gap_min` with no hits starts a new
*run*. Each run is analysed independently over its own bar window (extended back
`reentry_lookback_min` so the run's pole is captured) and reported as `<date>:<symbol>#<run>`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from .bullflag import detect_with_settings
from .capture import Bar
from .config import Settings
from .gates import GateInputs, float_gate, news_gate
from .rmetrics import compute_r_metrics
from .storage import Store


@dataclass(frozen=True)
class OpportunityAnalysis:
    opportunity_id: str
    symbol: str
    scanner_hits: int
    bars: int
    news_count: int
    float_shares: int | None
    short_percent: float | None
    float_ok: bool | None
    has_news: bool
    bull_flag: bool
    setup_count: int
    triggered: bool
    entry: float | None
    stop: float | None
    max_r: float | None
    mae_r: float | None
    stopped_out: bool
    run: int = 1  # 1-based run index within the symbol's day (#36 re-entry segmentation)
    run_count: int = 1  # total runs the symbol formed that day


@dataclass(frozen=True)
class EodReport:
    trading_date: date
    analyses: list[OpportunityAnalysis]
    aggregates: dict[str, Any]
    markdown: str


def _all_bars(bars: pl.DataFrame, oid: str) -> list[Bar]:
    """All of a symbol's day bars, deduped + sorted (raw store may hold duplicate rows)."""
    if bars.is_empty():
        return []
    sub = (
        bars.filter(pl.col("opportunity_id") == oid)
        .unique(subset="bar_start_utc", keep="first")
        .sort("bar_start_utc")
    )
    return [
        Bar(
            start=r["bar_start_utc"],
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["volume"]),
        )
        for r in sub.iter_rows(named=True)
    ]


def _hit_times(scans: pl.DataFrame, oid: str) -> list[datetime]:
    if scans.is_empty():
        return []
    sub = scans.filter(pl.col("opportunity_id") == oid)
    return sorted(sub["ts_utc"].to_list()) if not sub.is_empty() else []


def _funds_for(funds: pl.DataFrame, oid: str) -> tuple[int | None, float | None]:
    if funds.is_empty():
        return None, None
    fsub = funds.filter(pl.col("opportunity_id") == oid)
    if fsub.is_empty():
        return None, None
    r0 = fsub.row(0, named=True)
    return r0["float_shares"], r0["short_percent"]


def _count_in(df: pl.DataFrame, oid: str) -> int:
    if df.is_empty():
        return 0
    return int(df.filter(pl.col("opportunity_id") == oid).height)


def _segment_runs(hit_times: list[datetime], gap_min: int) -> list[datetime]:
    """Run start times: a gap of >= gap_min with no scanner hits begins a new run (#36)."""
    if not hit_times:
        return []
    times = sorted(hit_times)
    gap = timedelta(minutes=gap_min)
    starts = [times[0]]
    for prev, cur in zip(times, times[1:], strict=False):
        if cur - prev >= gap:
            starts.append(cur)
    return starts


def _run_windows(
    starts: list[datetime], lookback_min: int
) -> list[tuple[datetime | None, datetime | None]]:
    """Half-open [start, end) bar window per run, extended back lookback so the pole is included.

    The lookback zone sits inside the (>= gap_min) quiet period before a pop, so it never overlaps
    the previous run's hits. Empty starts -> a single unbounded window (defensive)."""
    if not starts:
        return [(None, None)]
    lb = timedelta(minutes=lookback_min)
    bounds = [s - lb for s in starts]
    return [(bounds[i], bounds[i + 1] if i + 1 < len(bounds) else None) for i in range(len(bounds))]


def _count_setups(bars: list[Bar], settings: Settings) -> int:
    """Distinct (non-overlapping) bull-flag setups within the run's bars."""
    count = 0
    last_end = -1
    for i in range(1, len(bars)):
        if i <= last_end:
            continue
        if detect_with_settings(bars[: i + 1], settings) is not None:
            count += 1
            last_end = i
    return count


def _analyze_run(
    seg_id: str,
    symbol: str,
    obars: list[Bar],
    *,
    first_seen: datetime,
    news_count: int,
    float_shares: int | None,
    short_percent: float | None,
    scanner_hits: int,
    run: int,
    run_count: int,
    s: Settings,
) -> OpportunityAnalysis:
    rm = compute_r_metrics(obars, s)
    setup_count = _count_setups(obars, s)
    # Single source of truth for the threshold predicates: reuse the gate engine rather than
    # re-deriving them here (a None datum stays None to distinguish "no data" from "fails gate").
    gi = GateInputs(ts_utc=first_seen, float_shares=float_shares, has_recent_news=news_count > 0)
    return OpportunityAnalysis(
        opportunity_id=seg_id,
        symbol=symbol,
        scanner_hits=scanner_hits,
        bars=len(obars),
        news_count=news_count,
        float_shares=float_shares,
        short_percent=short_percent,
        float_ok=float_gate(gi, s).passed if float_shares is not None else None,
        has_news=news_gate(gi, s).passed,
        bull_flag=setup_count > 0,
        setup_count=setup_count,
        triggered=rm.triggered,
        entry=rm.entry_price,
        stop=rm.stop,
        max_r=rm.max_r,
        mae_r=rm.mae_r,
        stopped_out=rm.stopped_out,
        run=run,
        run_count=run_count,
    )


def _analyses_for_symbol(
    row: dict[str, Any],
    bars: pl.DataFrame,
    news: pl.DataFrame,
    funds: pl.DataFrame,
    scans: pl.DataFrame,
    s: Settings,
) -> list[OpportunityAnalysis]:
    oid = row["opportunity_id"]
    all_bars = _all_bars(bars, oid)
    times = _hit_times(scans, oid)
    windows = _run_windows(_segment_runs(times, s.reentry_gap_min), s.reentry_lookback_min)
    news_count = _count_in(
        news, oid
    )  # day-level for the symbol (news/fundamentals are static facts)
    float_shares, short_percent = _funds_for(funds, oid)
    run_count = len(windows)

    def in_win(t: datetime, start: datetime | None, end: datetime | None) -> bool:
        return (start is None or t >= start) and (end is None or t < end)

    out: list[OpportunityAnalysis] = []
    for idx, (start, end) in enumerate(windows, start=1):
        rbars = [b for b in all_bars if in_win(b.start, start, end)]
        hits = sum(1 for t in times if in_win(t, start, end))
        seg_id = oid if run_count == 1 else f"{oid}#{idx}"
        out.append(
            _analyze_run(
                seg_id,
                row["symbol"],
                rbars,
                first_seen=start or row["first_seen_utc"],
                news_count=news_count,
                float_shares=float_shares,
                short_percent=short_percent,
                scanner_hits=hits,
                run=idx,
                run_count=run_count,
                s=s,
            )
        )
    return out


def build_eod_report(store: Store, settings: Settings, trading_date: date) -> EodReport:
    opps = store.read("opportunities")
    if not opps.is_empty():
        # One base opportunity per symbol/day: the raw dataset may hold duplicate rows (a mid-day
        # restart re-opening a name), so dedup by id on read (store-raw / compute-on-read).
        opps = opps.filter(pl.col("trading_date") == trading_date).unique(
            subset="opportunity_id", keep="first"
        )
    if opps.is_empty():
        empty = {
            "opportunities": 0,
            "with_news": 0,
            "float_ok": 0,
            "bull_flag": 0,
            "triggered": 0,
            "reached_1r": 0,
            "reached_2r": 0,
            "reached_3r": 0,
        }
        return EodReport(trading_date, [], empty, f"# EOD {trading_date}\n\nNo opportunities.")

    bars = store.read("bars")
    news = store.read("news")
    if not news.is_empty():  # dedup re-fetched news (same article) so news_count isn't inflated
        news = news.unique(subset=["opportunity_id", "article_id"], keep="first")
    funds = store.read("fundamentals")
    scans = store.read("scanner_hits")  # NOT deduped: each row is a distinct scanner appearance

    analyses: list[OpportunityAnalysis] = []
    for row in opps.iter_rows(named=True):
        analyses.extend(_analyses_for_symbol(row, bars, news, funds, scans, settings))

    def reached(r: float) -> int:
        return sum(1 for a in analyses if a.max_r is not None and a.max_r >= r)

    aggregates = {
        "opportunities": len(analyses),  # segmented — a 2-run symbol counts as 2 (#36)
        "with_news": sum(1 for a in analyses if a.has_news),
        "float_ok": sum(1 for a in analyses if a.float_ok),
        "bull_flag": sum(1 for a in analyses if a.bull_flag),
        "triggered": sum(1 for a in analyses if a.triggered),
        "reached_1r": reached(1.0),
        "reached_2r": reached(2.0),
        "reached_3r": reached(3.0),
    }
    return EodReport(
        trading_date, analyses, aggregates, _to_markdown(trading_date, analyses, aggregates)
    )


def _to_markdown(d: date, analyses: list[OpportunityAnalysis], agg: dict[str, Any]) -> str:
    lines = [
        f"# EOD report — {d}",
        "",
        f"- opportunities: **{agg['opportunities']}** | with news: {agg['with_news']} | "
        f"float<20M: {agg['float_ok']} | bull-flag: {agg['bull_flag']}",
        f"- would-trigger: **{agg['triggered']}** | reached ≥1R: {agg['reached_1r']} | "
        f"≥2R: {agg['reached_2r']} | ≥3R: {agg['reached_3r']}",
        "",
        "| name | bars | news | float | flag | setups | trig | MaxR | MAE_R | stop |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    # Sort by Max R desc; untriggered (max_r None) sink to the bottom. Use an explicit None check
    # so a triggered max_r of exactly 0.0 (same-bar stop-out) isn't mistaken for missing (`0.0 or`).
    for a in sorted(analyses, key=lambda x: x.max_r if x.max_r is not None else -1.0, reverse=True):
        name = a.symbol if a.run_count == 1 else f"{a.symbol}#{a.run}"
        lines.append(
            f"| {name} | {a.bars} | {a.news_count} | {a.float_shares or '-'} | "
            f"{'Y' if a.bull_flag else '-'} | {a.setup_count} | {'Y' if a.triggered else '-'} | "
            f"{a.max_r if a.max_r is not None else '-'} | "
            f"{a.mae_r if a.mae_r is not None else '-'} | {'Y' if a.stopped_out else '-'} |"
        )
    return "\n".join(lines)


def analysis_records(report: EodReport) -> list[dict[str, Any]]:
    """Flatten analyses into rows for persistence in the `analysis` dataset."""
    return [{**asdict(a), "trading_date": report.trading_date} for a in report.analyses]
