"""Raw capture per opportunity (issue #14): the evolving longitudinal record.

When a candidate is flagged it becomes an *opportunity* (`<trading_date>:<symbol>`). The intraday
loop records only *discovery* — it writes the static-at-flag facts once (opportunities + news +
fundamentals) and logs every scanner appearance (scanner_hits). The day's 5-min **bars are pulled
once in an end-of-day batch** (`capture_day_bars`, #62), because a single historical request
returns the whole session and survives mid-day restarts (no streaming, no gaps/dups). Everything
is append-only via the Store; nothing is mutated.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

import polars as pl

from .clock import ET
from .config import Settings
from .fundamentals import MultiFundamentals, fundamentals_record
from .logging import get_logger
from .monitoring import BARS_APPENDED, OPPORTUNITIES
from .scanner import Candidate
from .storage import Store

log = get_logger(__name__)


def opportunity_id(trading_date: date, symbol: str) -> str:
    """Stable id: one opportunity per symbol per trading day."""
    return f"{trading_date.isoformat()}:{symbol.upper()}"


@dataclass(frozen=True)
class Bar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class NewsItem:
    time: str
    provider: str
    headline: str
    article_id: str


class BarSource(Protocol):
    async def fetch_day_bars(
        self, candidate: Candidate, *, trading_date: date, end: datetime | None = None
    ) -> list[Bar]: ...


class NewsSource(Protocol):
    async def fetch_news(
        self, candidate: Candidate, *, lookback_days: int, limit: int
    ) -> list[NewsItem]: ...


def opportunity_record(
    c: Candidate, oid: str, first_seen: datetime, trading_date: date
) -> dict[str, Any]:
    return {
        "opportunity_id": oid,
        "symbol": c.symbol,
        "con_id": c.con_id,
        "exchange": c.exchange,
        "currency": c.currency,
        "trading_date": trading_date,
        "first_seen_utc": first_seen.astimezone(UTC),
        "first_rank": c.rank,
    }


def scanner_hit_record(oid: str, c: Candidate, ts: datetime) -> dict[str, Any]:
    return {"opportunity_id": oid, "symbol": c.symbol, "ts_utc": ts.astimezone(UTC), "rank": c.rank}


def parse_news_ts(raw: str) -> datetime | None:
    """Parse an IBKR news timestamp string to a UTC datetime (best-effort, None if unparseable).

    IBKR historical-news times look like ``2026-07-01 13:45:00.0`` (GMT); we normalise to UTC so
    news can be attributed to a run window at analysis time (#97) and bucketed by recency (#101)."""
    s = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def news_record(oid: str, symbol: str, n: NewsItem) -> dict[str, Any]:
    return {
        "opportunity_id": oid,
        "symbol": symbol,
        "time": n.time,  # raw provider string, kept for provenance (store-raw)
        "ts_utc": parse_news_ts(n.time),  # normalised for run attribution / recency (#97)
        "provider": n.provider,
        "headline": n.headline,
        "article_id": n.article_id,
    }


def bar_record(oid: str, symbol: str, b: Bar) -> dict[str, Any]:
    return {
        "opportunity_id": oid,
        "symbol": symbol,
        "bar_start_utc": b.start.astimezone(UTC),
        "open": b.open,
        "high": b.high,
        "low": b.low,
        "close": b.close,
        "volume": b.volume,
    }


def _candidate_from_row(row: dict[str, Any]) -> Candidate:
    """Rebuild the (minimal) Candidate needed to fetch bars from a stored opportunity row.

    Tolerates rows written before exchange/currency were persisted (they fall back to defaults;
    only symbol/currency actually shape the SMART contract)."""
    return Candidate(
        rank=row.get("first_rank") or 0,
        symbol=row["symbol"],
        con_id=row["con_id"],
        exchange=row.get("exchange") or "SMART",
        currency=row.get("currency") or "USD",
    )


@dataclass
class CaptureService:
    """Persists the evolving record for each flagged opportunity into the Store."""

    store: Store
    bars: BarSource
    news: NewsSource
    settings: Settings
    fundamentals: MultiFundamentals = field(default_factory=lambda: MultiFundamentals(()))
    _open: set[str] = field(default_factory=set)  # opportunity_ids already opened today
    _hydrated_date: date | None = None

    def _ensure_hydrated(self, trading_date: date) -> None:
        """Seed the open-opportunity set from storage so a mid-day restart doesn't re-open/dup."""
        if self._hydrated_date == trading_date:
            return
        self._open = set()
        opps = self.store.read("opportunities")
        if not opps.is_empty():
            today = opps.filter(pl.col("trading_date") == trading_date)
            self._open = set(today["opportunity_id"].to_list())
        self._hydrated_date = trading_date

    async def on_scan_tick(self, candidates: Sequence[Candidate], now: datetime) -> None:
        """Discovery only: open new opportunities and log every scanner appearance.

        Each candidate is isolated (#254, matching ``_fetch_bars_for``'s per-symbol isolation): one
        symbol failing to open — a disk-full/corrupt-file append, a source blowing up — must not
        stop the candidates ranked below it from being opened *or* from getting a scanner hit. It
        used to: the loop was unguarded and ``app._on_tick`` doesn't wrap the call, so a persistent
        error on a high-rank symbol silently starved every symbol behind it, every tick.

        The hit is recorded either way — "this symbol was on the scanner at this time" is true
        regardless of whether we managed to open its record.
        """
        trading_date = now.date()
        self._ensure_hydrated(trading_date)
        hits: list[dict[str, Any]] = []
        for c in candidates:
            oid = opportunity_id(trading_date, c.symbol)
            if oid not in self._open:
                try:
                    await self._open_opportunity(oid, c, now, trading_date)
                except Exception:  # noqa: BLE001 — one candidate must not stall the rest
                    log.warning("capture.open_opportunity_failed", opportunity_id=oid)
            hits.append(scanner_hit_record(oid, c, now))
        # One append per tick, not one per candidate (#247): the old per-candidate call wrote a
        # separate single-row Parquet file, so ~50 candidates x ~480 in-window ticks produced ~24k
        # one-row files a day in a single dt= partition — and every read globs + union_by_names all
        # of them. Same raw rows, one file. (Empty candidate list -> append is a no-op.)
        await self.store.append_async("scanner_hits", hits, partition_date=trading_date)

    async def _open_opportunity(
        self, oid: str, c: Candidate, now: datetime, trading_date: date
    ) -> None:
        await self.store.append_async(
            "opportunities",
            [opportunity_record(c, oid, now, trading_date)],
            partition_date=trading_date,
        )
        try:
            items = await self.news.fetch_news(
                c, lookback_days=self.settings.news_lookback_days, limit=self.settings.news_max
            )
        except Exception:  # noqa: BLE001 — news is best-effort; never block opening the record
            log.warning("capture.news_fetch_failed", opportunity_id=oid)
            items = []
        if items:
            await self.store.append_async(
                "news", [news_record(oid, c.symbol, n) for n in items], partition_date=trading_date
            )
        funds = await self.fundamentals.fetch_all(c)
        if funds:
            await self.store.append_async(
                "fundamentals",
                [fundamentals_record(oid, f, now) for f in funds],
                partition_date=trading_date,
            )
        self._open.add(oid)
        OPPORTUNITIES.inc()
        log.info(
            "capture.opportunity_opened",
            opportunity_id=oid,
            news=len(items),
            fundamentals={f.source: f.float_shares for f in funds},
        )

    def _day_opportunities(self, trading_date: date) -> pl.DataFrame:
        """The day's opportunities, deduped by id (a mid-day restart may re-open a name)."""
        opps = self.store.read("opportunities")
        if opps.is_empty():
            return opps
        return opps.filter(pl.col("trading_date") == trading_date).unique(
            subset="opportunity_id", keep="first"
        )

    async def _fetch_bars_for(
        self, opps: pl.DataFrame, trading_date: date, *, end: datetime | None = None
    ) -> int:
        """Fetch + append the day's bars for each opportunity row; returns how many got bars.

        One symbol's data hiccup never stalls the rest. Bars dedup by bar_start_utc on read, so
        re-running (retry / back-fill) is idempotent."""
        filled = 0
        for i, row in enumerate(opps.iter_rows(named=True)):
            if i > 0 and self.settings.ibkr_hist_pacing_sec > 0:
                await asyncio.sleep(
                    self.settings.ibkr_hist_pacing_sec
                )  # stay under the pacing limit
            oid = row["opportunity_id"]
            cand = _candidate_from_row(row)
            try:
                bars = await self.bars.fetch_day_bars(cand, trading_date=trading_date, end=end)
            except Exception:  # noqa: BLE001 — one symbol's failure must not stall the batch
                log.warning("capture.day_bars_failed", opportunity_id=oid)
                continue
            if not bars:
                continue
            await self.store.append_async(
                "bars",
                [bar_record(oid, cand.symbol, b) for b in bars],
                partition_date=trading_date,
            )
            BARS_APPENDED.inc(len(bars))
            log.info("capture.day_bars_appended", opportunity_id=oid, count=len(bars))
            filled += 1
        return filled

    async def capture_day_bars(self, trading_date: date) -> None:
        """End-of-day batch: one historical request per flagged opportunity → append its bars.

        Reads the day's opportunities from storage (not in-memory state), so it is unaffected by
        any restart during the session. Opportunities that already have bars are skipped, so a retry
        after a partial failure only re-fetches the gaps rather than the whole batch (#163-C2).
        """
        opps = self._day_opportunities(trading_date)
        if opps.is_empty():
            return
        have = self._opportunities_with_bars(trading_date)
        todo = opps.filter(~pl.col("opportunity_id").is_in(list(have)))
        if not todo.is_empty():
            await self._fetch_bars_for(todo, trading_date)

    def _opportunities_with_bars(self, trading_date: date) -> set[str]:
        """opportunity_ids that already have at least one stored bar (ids encode the date).

        Only ``trading_date``'s opportunities are ever tested against this set (bar ids embed the
        date), so scope the read to that day's partition instead of loading the whole ``bars``
        history (~1.4 GB) into memory and OOMing the box (#246, mirrors report.py/portfolio.py)."""
        bars = self.store.read("bars", dt=trading_date)
        if bars.is_empty():
            return set()
        return set(bars["opportunity_id"].unique().to_list())

    async def capture_missing_bars(self, trading_date: date) -> bool:
        """Back-fill bars for the day's opportunities that have none stored; True if any were added.

        Recovers a missed/failed EOD batch (e.g. the Gateway was down at 16:20). Fetches only the
        opportunities still lacking bars — no redundant IBKR requests — and requests up to that
        day's extended-session close so a historical window for a *past* day is bounded correctly.
        Idempotent: bars dedup on read.
        """
        opps = self._day_opportunities(trading_date)
        if opps.is_empty():
            return False
        have = self._opportunities_with_bars(trading_date)
        missing = opps.filter(~pl.col("opportunity_id").is_in(list(have)))
        if missing.is_empty():
            return False
        end = datetime.combine(trading_date, time(20, 0), tzinfo=ET)  # cover the extended session
        return await self._fetch_bars_for(missing, trading_date, end=end) > 0

    async def backfill_recent(self, today: date, *, days: int) -> list[date]:
        """Fill missing bars across the last ``days`` calendar days; returns the dates it filled.

        A no-op for days with no opportunities (weekends / already-complete days)."""
        filled: list[date] = []
        for i in range(days):
            d = today - timedelta(days=i)
            try:
                if await self.capture_missing_bars(d):
                    filled.append(d)
            except Exception:  # noqa: BLE001 — one day's failure must not stall the rest
                log.warning("capture.backfill_failed", date=d.isoformat())
        return filled

    async def capture_day_news(self, trading_date: date) -> None:
        """End-of-day batch: re-fetch each opportunity's news so *late-breaking* stories are kept.

        The intraday fetch happens once at first sighting, so a catalyst that breaks later in the
        day — or for a second run of the same symbol — is otherwise never captured (#97). The EOD
        re-fetch closes that gap; duplicates (same article) are deduped on read by article_id. Reads
        opportunities from storage, so a mid-day restart doesn't matter.
        """
        opps = self.store.read("opportunities")
        if opps.is_empty():
            return
        opps = opps.filter(pl.col("trading_date") == trading_date).unique(
            subset="opportunity_id", keep="first"
        )
        for row in opps.iter_rows(named=True):
            oid = row["opportunity_id"]
            cand = _candidate_from_row(row)
            try:
                items = await self.news.fetch_news(
                    cand,
                    lookback_days=self.settings.news_lookback_days,
                    limit=self.settings.news_max,
                )
            except Exception:  # noqa: BLE001 — one symbol's failure must not stall the batch
                log.warning("capture.day_news_failed", opportunity_id=oid)
                continue
            if items:
                await self.store.append_async(
                    "news",
                    [news_record(oid, cand.symbol, n) for n in items],
                    partition_date=trading_date,
                )
                log.info("capture.day_news_appended", opportunity_id=oid, count=len(items))

    def _opportunities_with_fundamentals(self, trading_date: date) -> set[str]:
        """opportunity_ids that already have at least one stored fundamentals row.

        Scoped to the day's partition — fundamentals are written under the opportunity's
        ``trading_date``, so a full-history read would be pure waste (and the #246 OOM lesson)."""
        funds = self.store.read("fundamentals", dt=trading_date)
        if funds.is_empty():
            return set()
        return set(funds["opportunity_id"].unique().to_list())

    async def capture_missing_fundamentals(self, trading_date: date) -> int:
        """End-of-day: re-fetch fundamentals for opportunities that have none. Returns how many.

        Without this a missing fundamentals row is **permanent** (#255), and it costs a real runner:
        the float gate reads ``float_shares=None`` and fails conservatively (``gates.py``), so a
        genuine low-float mover is disqualified from analysis for good.

        It does not take a crash to get there. ``MultiFundamentals.fetch_all`` gathers with
        ``return_exceptions=True`` and keeps only the successes, so *any* fetch failure or timeout
        at open time yields no row — and ``_open_opportunity`` still marks the oid open, while a
        restart re-seeds ``_open`` from the persisted opportunities row. Either way the symbol
        looks already-done and is never retried. Mirrors ``capture_missing_bars``; idempotent
        (only opportunities with zero rows are fetched, and reads dedup by source).
        """
        opps = self._day_opportunities(trading_date)
        if opps.is_empty():
            return 0
        have = self._opportunities_with_fundamentals(trading_date)
        missing = opps.filter(~pl.col("opportunity_id").is_in(list(have)))
        if missing.is_empty():
            return 0
        filled = 0
        for row in missing.iter_rows(named=True):
            oid = row["opportunity_id"]
            cand = _candidate_from_row(row)
            try:
                funds = await self.fundamentals.fetch_all(cand)
            except Exception:  # noqa: BLE001 — one symbol's failure must not stall the batch
                log.warning("capture.day_fundamentals_failed", opportunity_id=oid)
                continue
            if not funds:
                continue  # still unavailable; the next EOD run tries again
            await self.store.append_async(
                "fundamentals",
                [fundamentals_record(oid, f, datetime.now(UTC)) for f in funds],
                partition_date=trading_date,
            )
            filled += 1
            log.info(
                "capture.day_fundamentals_appended",
                opportunity_id=oid,
                sources={f.source: f.float_shares for f in funds},
            )
        return filled

    def reset(self) -> None:
        """Clear open-opportunity state (call at end of the capture window / new session)."""
        self._open = set()
        self._hydrated_date = None
