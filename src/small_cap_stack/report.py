"""End-of-day report (issue #19): per-opportunity stats computed on read from raw data.

Joins the raw datasets (opportunities + scanner_hits + news + fundamentals + bars) for a trading
day and, per opportunity, computes float/news signals, bull-flag setups, and R-metrics
(would-trigger? Max R, MAE, stop-out). Everything is derived on read, so changing the
methodology and re-running reproduces history (store-raw / compute-on-read).

#36 (exhaustion / re-entry) is honoured lightly here via `setup_count` — how many distinct
bull-flag setups formed in the day (each is a potential separate entry); full segmentation into
distinct opportunity ids remains future work on #36.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
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


@dataclass(frozen=True)
class EodReport:
    trading_date: date
    analyses: list[OpportunityAnalysis]
    aggregates: dict[str, Any]
    markdown: str


def _bars_for(bars: pl.DataFrame, oid: str) -> list[Bar]:
    if bars.is_empty():
        return []
    # The raw `bars` dataset is append-only and may hold duplicate (opportunity_id, bar_start_utc)
    # rows (re-fetched across EOD runs / restarts) — dedup on read so the flag/R logic sees each
    # 5-min bar once (store-raw / compute-on-read). Duplicate rows are identical, so keep any.
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


def _count_in(df: pl.DataFrame, oid: str) -> int:
    if df.is_empty():
        return 0
    return int(df.filter(pl.col("opportunity_id") == oid).height)


def _count_setups(bars: list[Bar], settings: Settings) -> int:
    """Distinct (non-overlapping) bull-flag setups across the day — re-entry potential (#36)."""
    count = 0
    last_end = -1
    for i in range(1, len(bars)):
        if i <= last_end:
            continue
        if detect_with_settings(bars[: i + 1], settings) is not None:
            count += 1
            last_end = i
    return count


def _analyze(
    row: dict[str, Any], bars: pl.DataFrame, news: pl.DataFrame, funds: pl.DataFrame, s: Settings
) -> OpportunityAnalysis:
    oid = row["opportunity_id"]
    obars = _bars_for(bars, oid)
    news_count = _count_in(news, oid)

    float_shares: int | None = None
    short_percent: float | None = None
    if not funds.is_empty():
        fsub = funds.filter(pl.col("opportunity_id") == oid)
        if not fsub.is_empty():
            r0 = fsub.row(0, named=True)
            float_shares = r0["float_shares"]
            short_percent = r0["short_percent"]

    rm = compute_r_metrics(obars, s)
    setup_count = _count_setups(obars, s)
    # Single source of truth for the threshold predicates: reuse the gate engine rather than
    # re-deriving them here (a None datum stays None to distinguish "no data" from "fails gate").
    gi = GateInputs(
        ts_utc=row["first_seen_utc"],
        float_shares=float_shares,
        has_recent_news=news_count > 0,
    )
    return OpportunityAnalysis(
        opportunity_id=oid,
        symbol=row["symbol"],
        scanner_hits=0,  # filled from the scanner_hits dataset in build_eod_report
        bars=len(obars),
        news_count=news_count,
        float_shares=float_shares,
        short_percent=short_percent,
        float_ok=float_gate(gi, s).passed if float_shares is not None else None,
        has_news=news_gate(gi, s).passed,
        bull_flag=setup_count > 0,  # a flag formed at some point in the day
        setup_count=setup_count,
        triggered=rm.triggered,
        entry=rm.entry_price,
        stop=rm.stop,
        max_r=rm.max_r,
        mae_r=rm.mae_r,
        stopped_out=rm.stopped_out,
    )


def build_eod_report(store: Store, settings: Settings, trading_date: date) -> EodReport:
    opps = store.read("opportunities")
    if not opps.is_empty():
        # One analysis per opportunity: the raw dataset may hold duplicate rows (a mid-day restart
        # re-opening an already-known name), so dedup by id on read (store-raw / compute-on-read).
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
        a = _analyze(row, bars, news, funds, settings)
        # scanner_hits is its own dataset; override the placeholder above
        a = OpportunityAnalysis(**{**asdict(a), "scanner_hits": _count_in(scans, a.opportunity_id)})
        analyses.append(a)

    def reached(r: float) -> int:
        return sum(1 for a in analyses if a.max_r is not None and a.max_r >= r)

    aggregates = {
        "opportunities": len(analyses),
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
        "| symbol | bars | news | float | flag | setups | trig | MaxR | MAE_R | stop |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in sorted(analyses, key=lambda x: x.max_r or -999, reverse=True):
        lines.append(
            f"| {a.symbol} | {a.bars} | {a.news_count} | {a.float_shares or '-'} | "
            f"{'Y' if a.bull_flag else '-'} | {a.setup_count} | {'Y' if a.triggered else '-'} | "
            f"{a.max_r if a.max_r is not None else '-'} | "
            f"{a.mae_r if a.mae_r is not None else '-'} | {'Y' if a.stopped_out else '-'} |"
        )
    return "\n".join(lines)


def analysis_records(report: EodReport) -> list[dict[str, Any]]:
    """Flatten analyses into rows for persistence in the `analysis` dataset."""
    return [{**asdict(a), "trading_date": report.trading_date} for a in report.analyses]
