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
from .gates import GateInputs, float_gate, news_gate, volume_gate
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
    pole_len: int | None = None  # number of higher highs in the traded setup's pole (#127)
    cons_vol_reducing: bool | None = None  # consolidation volume non-increasing (#127)
    pole_has_big_green: bool | None = None  # pole holds a strong-bodied green candle (#132)
    news_recent: bool = False  # a news story dated today or yesterday (ET) for the symbol (#101)
    peak_5m_volume: float | None = None  # run's busiest 5-min bar from appearance onward (#193)
    volume_ok: bool | None = None  # peak_5m_volume clears the read-time quality bar (#193)
    first_hit: datetime | None = None  # first scanner appearance (gates entry); shown in the UI
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
    bars: list[Bar]  # the run's bars, bounded at capture_end
    first_hit: datetime | None  # the run's first scanner appearance (gates the entry, #99)
    run_count: int  # total runs the symbol formed that day


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


def day_chart_bars(bars: pl.DataFrame, oid: str, s: Settings) -> list[Bar]:
    """A symbol's full trading-day 5-min bars, bounded to the chart window (#141).

    ``[chart_start, capture_end)`` ET (04:00–16:00) — the un-clipped series the review workbench
    renders, in contrast to :func:`symbol_runs` which windows bars per run for the analysis. Reuses
    ``_all_bars`` (dedupe + sort) so it stays store-raw / compute-on-read."""
    return [
        b
        for b in _all_bars(bars, oid)
        if s.chart_start <= b.start.astimezone(ET).time() < s.capture_end
    ]


def _hit_times(scans: pl.DataFrame, oid: str) -> list[datetime]:
    if scans.is_empty():
        return []
    sub = scans.filter(pl.col("opportunity_id") == oid)
    return sorted(sub["ts_utc"].to_list()) if not sub.is_empty() else []


# Read-time source priority, per field (an opportunity may have one fundamentals row per source,
# #109). Float: FMP over yfinance. Short interest: only yfinance today (FINRA lands in #110). A
# source not listed still counts, ranked last — so we never silently drop a number.
_FLOAT_PRIORITY = ("fmp", "yfinance")
_SHORT_PRIORITY = ("yfinance",)


def _pick_by_source(
    rows: list[dict[str, object]], column: str, priority: tuple[str, ...]
) -> object:
    """Highest-priority non-null value for a column across an opportunity's source rows."""
    best: object = None
    best_rank: int | None = None
    for r in rows:
        val = r[column]
        if val is None:
            continue
        src = r["source"]
        rank = priority.index(src) if src in priority else len(priority)
        if best_rank is None or rank < best_rank:
            best, best_rank = val, rank
    return best


def _funds_for(funds: pl.DataFrame, oid: str) -> tuple[int | None, float | None]:
    if funds.is_empty():
        return None, None
    fsub = funds.filter(pl.col("opportunity_id") == oid)
    if fsub.is_empty():
        return None, None
    rows = list(fsub.iter_rows(named=True))
    float_shares = _pick_by_source(rows, "float_shares", _FLOAT_PRIORITY)
    short_percent = _pick_by_source(rows, "short_percent", _SHORT_PRIORITY)
    return (
        int(float_shares) if isinstance(float_shares, int | float) else None,
        float(short_percent) if isinstance(short_percent, int | float) else None,
    )


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


def _previous_trading_day(d: date) -> date:
    """The prior weekday (skip Sat/Sun) — the last session before ``d`` (holidays aside, #163)."""
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:  # Saturday (5) / Sunday (6): markets closed
        prev -= timedelta(days=1)
    return prev


def _news_recent(news_times: list[datetime], trading_date: date) -> bool:
    """True if any news is dated within the gap since the last close, in ET (#101).

    A tighter recency signal than the 7-day `has_news`: 'was there a fresh catalyst?' rather than
    'any story this week'. The window spans the previous *trading* day through the trade date
    inclusive, so it catches a Friday catalyst AND weekend news ahead of a Monday gap — the most
    common pre-Monday driver — which a plain today/yesterday window silently dropped (#163-C4).
    Compared in ET so a late-UTC print lands on the right market day."""
    earliest = _previous_trading_day(trading_date)
    return any(earliest <= t.astimezone(ET).date() <= trading_date for t in news_times)


def float_sources_for(funds: pl.DataFrame, oid: str) -> list[dict[str, Any]]:
    """Per-source recorded float for an opportunity, highest-priority source first (#109).

    Unlike `_funds_for` (which merges to one number via `_pick_by_source`), this keeps *every*
    source so the review workbench can show 'fmp 12.3M vs yfinance 14.1M'. Ordered by
    `_FLOAT_PRIORITY` (fmp first); an unknown source ranks last. Deduped per source (fundamentals
    may be captured more than once a day), keeping the first row; a null float is dropped."""
    if funds.is_empty():
        return []
    fsub = funds.filter(pl.col("opportunity_id") == oid)
    if fsub.is_empty():
        return []
    seen: dict[str, int] = {}
    for r in fsub.iter_rows(named=True):
        src = str(r["source"])
        val = r["float_shares"]
        if src not in seen and isinstance(val, int | float):
            seen[src] = int(val)

    def _rank(src: str) -> int:
        return _FLOAT_PRIORITY.index(src) if src in _FLOAT_PRIORITY else len(_FLOAT_PRIORITY)

    ordered = sorted(seen.items(), key=lambda kv: _rank(kv[0]))
    return [{"source": src, "float": shares} for src, shares in ordered]


def news_headlines_for(news: pl.DataFrame, oid: str) -> list[dict[str, Any]]:
    """An opportunity's news headlines, newest-first, deduped by article (#109/#97).

    Surfaces the actual headline text — the EOD report keeps only counts — so the review workbench
    can show the catalyst that was breaking at trigger. Deduped on `article_id` (news is re-fetched
    at EOD); a row whose publish time couldn't be parsed keeps `ts=None` and sorts last."""
    if news.is_empty():
        return []
    sub = news.filter(pl.col("opportunity_id") == oid)
    if sub.is_empty():
        return []
    if "article_id" in sub.columns:
        sub = sub.unique(subset=["opportunity_id", "article_id"], keep="first")
    items = [
        {
            "ts": int(r["ts_utc"].timestamp()) if r.get("ts_utc") is not None else None,
            "provider": str(r["provider"]) if r.get("provider") is not None else "",
            "headline": str(r["headline"]) if r.get("headline") is not None else "",
        }
        for r in sub.iter_rows(named=True)
    ]
    items.sort(key=lambda it: (it["ts"] is not None, it["ts"] or 0), reverse=True)
    return items


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
        runs.append(SymbolRun(idx, seg_id, row["symbol"], start, end, rbars, first_hit, run_count))
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
    # Read-time volume quality signal (#193): the run's PEAK 5-min bar volume from appearance
    # onward — its busiest window. Seen time is unchanged (appearance is still the 100k crossing);
    # this only classifies whether the name traded with real size. first_hit=None (no known
    # appearance) falls back to the whole run.
    vol_bars = [b.volume for b in obars if first_hit is None or b.start >= first_hit]
    peak_5m_volume = max(vol_bars) if vol_bars else None
    # Single source of truth for the threshold predicates: reuse the gate engine rather than
    # re-deriving them here (a None datum stays None to distinguish "no data" from "fails gate").
    gi = GateInputs(
        ts_utc=first_seen,
        volume_5m=peak_5m_volume,
        float_shares=float_shares,
        has_recent_news=news_count > 0,
    )
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
        peak_5m_volume=peak_5m_volume,
        volume_ok=volume_gate(gi, s).passed if peak_5m_volume is not None else None,
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
        pole_len=rm.pole_len,
        cons_vol_reducing=rm.cons_vol_reducing,
        pole_has_big_green=rm.pole_has_big_green,
        news_recent=news_recent,
        first_hit=first_hit,
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
            "volume_ok": 0,
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
        "volume_ok": sum(1 for a in analyses if a.volume_ok),  # cleared read-time vol bar (#193)
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
        f"float<20M: {agg['float_ok']} | vol-ok: {agg['volume_ok']} | "
        f"bull-flag: {agg['bull_flag']}",
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
