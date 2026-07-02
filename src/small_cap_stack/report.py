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

from .capture import Bar
from .clock import ET
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
    triggered: bool
    entry: float | None
    stop: float | None
    max_r: float | None
    mae_r: float | None
    stopped_out: bool
    flag_len: int | None = None  # consolidation count of the traded setup (#98)
    retracement: float | None = None  # flag retracement into the pole, fraction (#98)
    news_recent: bool = False  # a news story dated today or yesterday (ET) for the symbol (#101)
    run: int = 1  # 1-based run index within the symbol's day (#36 re-entry segmentation)
    run_count: int = 1  # total runs the symbol formed that day


@dataclass(frozen=True)
class EodReport:
    trading_date: date
    analyses: list[OpportunityAnalysis]
    aggregates: dict[str, Any]
    markdown: str


@dataclass(frozen=True)
class SymbolRun:
    """One re-entry run of a symbol: its own bar window + the appearance that gates its entry.

    The reusable seam between analysis (`report.py`) and rendering (`charts.py`) so segmentation
    logic lives in exactly one place (store-raw / compute-on-read)."""

    idx: int  # 1-based run index within the symbol's day
    seg_id: str  # opportunity_id, or "<oid>#<idx>" when the symbol ran more than once
    symbol: str
    start: datetime | None  # window start (lookback-extended); None = unbounded
    end: datetime | None  # window end (next run's start); None = open-ended
    bars: list[Bar]  # the run's bars for analysis, disjoint at the next run's start (#36)
    first_hit: datetime | None  # the run's first scanner appearance (gates the entry, #99)
    run_count: int  # total runs the symbol formed that day
    chart_bars: list[Bar]  # bars to *draw*: extended past a later run while the trade's still open


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


def _news_for(news: pl.DataFrame, oid: str) -> tuple[list[datetime], int]:
    """A symbol's news split into (dated timestamps, count of undated rows).

    News is attributed to a run by comparing its publish time to the run window (#97). Rows whose
    timestamp couldn't be parsed — and legacy rows written before ts_utc existed — are 'undated'
    and fall back to the first run so they're never lost."""
    if news.is_empty():
        return [], 0
    sub = news.filter(pl.col("opportunity_id") == oid)
    if sub.is_empty():
        return [], 0
    if "ts_utc" not in sub.columns:
        return [], sub.height
    dated = [t for t in sub["ts_utc"].to_list() if t is not None]
    return dated, sub.height - len(dated)


def _news_recent(news_times: list[datetime], trading_date: date) -> bool:
    """True if any news is dated today or yesterday (ET) relative to the trading date (#101).

    A tighter recency signal than the 7-day `has_news`: 'was there a fresh catalyst?' rather than
    'any story this week'. Compared in ET so a late-UTC print lands on the right market day."""
    recent = {trading_date, trading_date - timedelta(days=1)}
    return any(t.astimezone(ET).date() in recent for t in news_times)


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


def _in_win(t: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or t >= start) and (end is None or t < end)


def _chart_bars(
    rbars: list[Bar],
    all_bars: list[Bar],
    start: datetime | None,
    end: datetime | None,
    first_hit: datetime | None,
    s: Settings,
) -> list[Bar]:
    """Bars to *draw* for a run — usually its own window, but extended to the capture day's end
    when the notional trade is still open at the window boundary (triggered, not yet stopped out).

    Re-entry segmentation (#36) truncates a run's bar window at the next run's start so each run's
    bull-flag/R-metrics stay independent — the locked analysis contract. For the human-review chart,
    though, that cut hides the tail of a trade that was still running when the *later* opportunity
    fired: a position doesn't close just because the symbol popped again. So the chart (and only the
    chart) follows an open trade past the boundary to where it actually closes — its stop or the end
    of the capture day. The measured stats keep their disjoint windows; only the drawn series grows.
    """
    if end is None:  # last run already runs to capture_end — nothing to extend
        return rbars
    rm = compute_r_metrics(rbars, s, first_hit=first_hit)
    if rm.triggered and not rm.stopped_out:  # open at the boundary → draw it to its real close
        return [b for b in all_bars if _in_win(b.start, start, None)]
    return rbars


def symbol_runs(
    row: dict[str, Any], bars: pl.DataFrame, scans: pl.DataFrame, s: Settings
) -> list[SymbolRun]:
    """Segment a symbol's day into runs, each with its own capture_end-bounded bar window (#36).

    The single source of truth for run segmentation, shared by the EOD analysis and the chart
    renderer so they never drift. A gap of >= ``reentry_gap_min`` with no scanner hits starts a new
    run; each window extends back ``reentry_lookback_min`` so the run's pole is captured. Bars at/
    after ``capture_end`` (regular close, #93) are excluded from every run's window.
    """
    oid = row["opportunity_id"]
    all_bars = _all_bars(bars, oid)
    all_bars = [b for b in all_bars if b.start.astimezone(ET).time() < s.capture_end]
    run_starts = _segment_runs(_hit_times(scans, oid), s.reentry_gap_min)
    windows = _run_windows(run_starts, s.reentry_lookback_min)
    run_count = len(windows)
    runs: list[SymbolRun] = []
    for idx, (start, end) in enumerate(windows, start=1):
        rbars = [b for b in all_bars if _in_win(b.start, start, end)]
        seg_id = oid if run_count == 1 else f"{oid}#{idx}"
        first_hit = run_starts[idx - 1] if run_starts else None
        cbars = _chart_bars(rbars, all_bars, start, end, first_hit, s)
        runs.append(
            SymbolRun(idx, seg_id, row["symbol"], start, end, rbars, first_hit, run_count, cbars)
        )
    return runs


def _analyze_run(
    seg_id: str,
    symbol: str,
    obars: list[Bar],
    *,
    first_seen: datetime,
    first_hit: datetime | None,
    news_count: int,
    news_recent: bool,
    float_shares: int | None,
    short_percent: float | None,
    scanner_hits: int,
    run: int,
    run_count: int,
    s: Settings,
) -> OpportunityAnalysis:
    # R is gated to the run's appearance: a setup may form in the pre-appearance lookback, but the
    # entry may only trigger at/after the first scanner hit (#99) — never crediting an unseen move.
    rm = compute_r_metrics(obars, s, first_hit=first_hit)
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
        # A valid bull flag always has positive risk (entry = breakout + offset > flag_low = stop),
        # so rm.setup_found (≥1 actionable setup in this run) is exactly the old `setup_count > 0` —
        # reuse the R-metrics pass rather than iterating the prefixes a second time (#112).
        bull_flag=rm.setup_found,
        triggered=rm.triggered,
        entry=rm.entry_price,
        stop=rm.stop,
        max_r=rm.max_r,
        mae_r=rm.mae_r,
        stopped_out=rm.stopped_out,
        flag_len=rm.flag_len,
        retracement=rm.retracement,
        news_recent=news_recent,
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
    times = _hit_times(scans, oid)
    # News is attributed per-run by publish time (#97): a later run gets only the stories that
    # broke in its window. Undated news (unparseable / legacy) falls back to run 1 so it's not lost.
    news_times, news_undated = _news_for(news, oid)
    news_recent = _news_recent(news_times, row["trading_date"])  # day-level recency (#101)
    float_shares, short_percent = _funds_for(funds, oid)

    out: list[OpportunityAnalysis] = []
    for run in symbol_runs(row, bars, scans, s):
        hits = sum(1 for t in times if _in_win(t, run.start, run.end))
        news_count = sum(1 for t in news_times if _in_win(t, run.start, run.end))
        if run.idx == 1:
            news_count += news_undated  # undated news attributed to the first run
        out.append(
            _analyze_run(
                run.seg_id,
                row["symbol"],
                run.bars,
                first_seen=run.start or row["first_seen_utc"],
                first_hit=run.first_hit,
                news_count=news_count,
                news_recent=news_recent,
                float_shares=float_shares,
                short_percent=short_percent,
                scanner_hits=hits,
                run=run.idx,
                run_count=run.run_count,
                s=s,
            )
        )
    return out


def day_opportunities(store: Store, trading_date: date) -> pl.DataFrame:
    """The day's base opportunities, deduped by id on read (store-raw / compute-on-read).

    The raw dataset may hold duplicate rows (a mid-day restart re-opening a name); one base
    opportunity per symbol/day. Shared by the EOD report and the chart projection."""
    opps = store.read("opportunities")
    if opps.is_empty():
        return opps
    return opps.filter(pl.col("trading_date") == trading_date).unique(
        subset="opportunity_id", keep="first"
    )


def build_eod_report(store: Store, settings: Settings, trading_date: date) -> EodReport:
    opps = day_opportunities(store, trading_date)
    if opps.is_empty():
        empty = {
            "opportunities": 0,
            "with_news": 0,
            "with_recent_news": 0,
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
        "with_recent_news": sum(
            1 for a in analyses if a.news_recent
        ),  # news today/yesterday (#101)
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
        f"news today/yest: {agg['with_recent_news']} | "
        f"float<20M: {agg['float_ok']} | bull-flag: {agg['bull_flag']}",
        f"- would-trigger: **{agg['triggered']}** | reached ≥1R: {agg['reached_1r']} | "
        f"≥2R: {agg['reached_2r']} | ≥3R: {agg['reached_3r']}",
        "",
        "| name | bars | news | recent | float | flag | cons | retr | trig | MaxR | MAE_R | stop |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    # Sort by Max R desc; untriggered (max_r None) sink to the bottom. Use an explicit None check
    # so a triggered max_r of exactly 0.0 (same-bar stop-out) isn't mistaken for missing (`0.0 or`).
    for a in sorted(analyses, key=lambda x: x.max_r if x.max_r is not None else -1.0, reverse=True):
        name = a.symbol if a.run_count == 1 else f"{a.symbol}#{a.run}"
        lines.append(
            f"| {name} | {a.bars} | {a.news_count} | {'Y' if a.news_recent else '-'} | "
            f"{a.float_shares or '-'} | "
            f"{'Y' if a.bull_flag else '-'} | "
            f"{a.flag_len if a.flag_len is not None else '-'} | "
            f"{a.retracement if a.retracement is not None else '-'} | "
            f"{'Y' if a.triggered else '-'} | "
            f"{a.max_r if a.max_r is not None else '-'} | "
            f"{a.mae_r if a.mae_r is not None else '-'} | {'Y' if a.stopped_out else '-'} |"
        )
    return "\n".join(lines)


def analysis_records(report: EodReport) -> list[dict[str, Any]]:
    """Flatten analyses into rows for persistence in the `analysis` dataset."""
    return [{**asdict(a), "trading_date": report.trading_date} for a in report.analyses]
