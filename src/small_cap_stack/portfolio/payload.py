"""The ``portfolio.json`` the web page reads, plus the per-day candidate cache (#230, #243).

Split out of the old single-file ``portfolio.py`` (#259) with no behaviour change.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ..capture import Bar
from ..clock import ET
from ..config import Settings
from ..storage import Store
from .adaptive import risk_ladder
from .extract import extract_day_trades
from .models import CandidateTrade, PaperTrade, PortfolioResult, SkippedTrade
from .sim import simulate_portfolio, simulate_portfolio_adaptive


def collected_dates(store: Store) -> list[date]:
    """Every trading date with a captured opportunity, ascending (compute-on-read).

    The single source of truth — ``dashboard_backfill`` imports this rather than keeping its own
    copy (#257), so a future change here (say, also requiring bars to be present) can't apply to
    only one of them."""
    opps = store.read("opportunities")
    if opps.is_empty() or "trading_date" not in opps.columns:
        return []
    vals = opps.select(pl.col("trading_date")).unique().to_series().to_list()
    return sorted(d for d in vals if d is not None)


def _trade_json(t: PaperTrade) -> dict[str, object]:
    return {
        "date": t.trading_date.isoformat(),
        "symbol": t.symbol,
        "seg_id": t.seg_id,
        "run": t.run,
        "trigger_at": t.trigger_at.astimezone(ET).isoformat(),
        "entry": t.entry_price,
        "stop": t.stop,
        "qty": t.qty,
        "target_r": t.target_r,
        "realized_r": t.realized_r,
        "reason": t.reason,
        "exit_price": t.exit_price,
        "gross_pnl": t.gross_pnl_usd,
        "costs": round(t.commission_usd + t.fees_usd, 4),
        "net_pnl": t.net_pnl_usd,
        "equity_after": t.equity_after,
    }


def _skipped_json(sk: SkippedTrade) -> dict[str, object]:
    return {
        "date": sk.trading_date.isoformat(),
        "symbol": sk.symbol,
        "seg_id": sk.seg_id,
        "run": sk.run,
        "trigger_at": sk.trigger_at.astimezone(ET).isoformat(),
        "entry": sk.entry_price,
        "stop": sk.stop,
        "target_r": sk.target_r,
        "realized_r": sk.realized_r,
        "reason": sk.reason,
        "exit_price": sk.exit_price,
        "skip_reason": sk.skip_reason,
    }


def _book_json(
    res: PortfolioResult,
    daily_targets: list[tuple[date, float]] | None,
    daily_risk: list[tuple[date, float]] | None = None,
) -> dict[str, object]:
    book: dict[str, object] = {
        "stats": {
            "n_trades": res.n_trades,
            "wins": res.wins,
            "losses": res.losses,
            "win_rate": res.win_rate,
            "total_r": res.total_r,
            "avg_r": res.avg_r,
            "expectancy_usd": res.expectancy_usd,
            "end_equity": res.end_equity,
            "return_pct": res.return_pct,
            "max_drawdown_pct": res.max_drawdown_pct,
            "commission_usd": res.commission_usd,
            "fees_usd": res.fees_usd,
            "data_fees_usd": res.data_fees_usd,
            "total_costs_usd": res.total_costs_usd,
            # Getting-paid layer.
            "withdrawals_usd": res.withdrawals_usd,
            "withdrawals_gbp": res.withdrawals_gbp,
            "tax_paid_usd": res.tax_paid_usd,
            "tax_paid_gbp": res.tax_paid_gbp,
            "vps_costs_usd": res.vps_costs_usd,
            "vps_costs_gbp": res.vps_costs_gbp,
            "net_take_home_gbp": res.net_take_home_gbp,
            # Cap-only: the page's note asks "what did the N/day cap cost me?", so mixing the
            # unaffordable population into these would make it misattribute (#251).
            "skipped_count": sum(1 for sk in res.skipped if sk.skip_reason == "cap"),
            "skipped_total_r": res.skipped_total_r,
            "unaffordable_count": sum(1 for sk in res.skipped if sk.skip_reason == "unaffordable"),
        },
        "equity_curve": [{"date": d.isoformat(), "equity": e} for d, e in res.equity_curve],
        "trades": [_trade_json(t) for t in res.trades],
        "skipped": [_skipped_json(sk) for sk in res.skipped],
        "cash_flows": [
            {"date": cf.date.isoformat(), "kind": cf.kind, "usd": cf.usd, "gbp": cf.gbp}
            for cf in res.cash_flows
        ],
    }
    if daily_targets is not None:
        book["daily_targets"] = [{"date": d.isoformat(), "target": t} for d, t in daily_targets]
    if daily_risk is not None:
        book["daily_risk"] = [{"date": d.isoformat(), "risk": r} for d, r in daily_risk]
    return book


# The portfolio book is *cross-day*, so :func:`build_portfolio_payload` needs every collected day's
# qualifying trades. Extracting one day (segment + R-metrics per opportunity) costs about as much
# as one EOD report, so rebuilding the whole book from scratch on *every single-date dashboard
# backfill* silently did full-archive-scale work — the per-date backfill that should take seconds
# took minutes as history grew (the very ``--all`` workload CLAUDE.md warns off the box). A day's
# candidates are a pure function of that day's raw partitions + the settings that drive extraction,
# and the raw store is append-only immutable, so we cache each day's extracted candidates on disk
# keyed by a fingerprint of (those partition files, the whole settings model). A single-date
# backfill then re-extracts only the day that changed and reads the rest back from cache; any
# settings change or late-arriving/backfilled partition shifts the fingerprint and forces a correct
# re-extract, so compute-on-read is preserved. The cache lives under ``<data_dir>/cache`` (NOT
# ``dashboard/``, which publish-dashboard force-pushes wholesale to a public branch) and is fully
# regenerable.
_CANDIDATE_CACHE_SUBDIR = ("cache", "portfolio_candidates")

_EXTRACT_DATASETS = ("opportunities", "bars", "scanner_hits")


def portfolio_candidate_cache_dir(s: Settings) -> Path:
    """Directory holding the per-day extracted-candidate cache — off the published dashboard dir."""
    return s.data_dir.joinpath(*_CANDIDATE_CACHE_SUBDIR)


def _settings_fingerprint(s: Settings) -> str:
    """Hash the whole settings model: any change (price band, cutoff, excludes, tick size, or an
    engine param feeding ``symbol_runs`` / ``compute_r_metrics``) may alter extraction, and hashing
    everything can't miss one — a change just triggers one correct re-extract across all days."""
    body = json.dumps(s.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _day_fingerprint(store: Store, s: Settings, trading_date: date, settings_fp: str) -> str:
    """Fingerprint the day's extraction inputs: the raw partition files (name/size/mtime) that
    ``extract_day_trades`` reads, plus the settings hash. Append-only immutable parts mean a stable
    fingerprint until a new part lands for the date (a late backfill), which correctly busts it."""
    parts: dict[str, list[tuple[str, int, int]]] = {}
    for dataset in _EXTRACT_DATASETS:
        root = store.data_dir / dataset / f"dt={trading_date.isoformat()}"
        files = sorted(root.glob("**/*.parquet"))
        parts[dataset] = [(p.name, (st := p.stat()).st_size, st.st_mtime_ns) for p in files]
    body = json.dumps({"settings": settings_fp, "partitions": parts}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def _bar_to_json(b: Bar) -> list[object]:
    return [b.start.isoformat(), b.open, b.high, b.low, b.close, b.volume]


def _bar_from_json(r: list[Any]) -> Bar:
    return Bar(
        start=datetime.fromisoformat(str(r[0])),
        open=float(r[1]),
        high=float(r[2]),
        low=float(r[3]),
        close=float(r[4]),
        volume=float(r[5]),
    )


def _candidate_to_json(c: CandidateTrade) -> dict[str, Any]:
    return {
        "trading_date": c.trading_date.isoformat(),
        "symbol": c.symbol,
        "seg_id": c.seg_id,
        "run": c.run,
        "trigger_at": c.trigger_at.isoformat(),
        "entry_price": c.entry_price,
        "entry_fill": c.entry_fill,
        "stop": c.stop,
        "risk": c.risk,
        "entry_index": c.entry_index,
        "bars": [_bar_to_json(b) for b in c.bars],
    }


def _candidate_from_json(d: dict[str, Any]) -> CandidateTrade:
    return CandidateTrade(
        trading_date=date.fromisoformat(str(d["trading_date"])),
        symbol=str(d["symbol"]),
        seg_id=str(d["seg_id"]),
        run=int(d["run"]),
        trigger_at=datetime.fromisoformat(str(d["trigger_at"])),
        entry_price=float(d["entry_price"]),
        entry_fill=float(d["entry_fill"]),
        stop=float(d["stop"]),
        risk=float(d["risk"]),
        entry_index=int(d["entry_index"]),
        bars=tuple(_bar_from_json(b) for b in d["bars"]),
    )


def _read_candidate_cache(path: Path, fingerprint: str) -> list[CandidateTrade] | None:
    """Return cached candidates iff the file parses and its fingerprint matches; else None."""
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict) or loaded.get("fingerprint") != fingerprint:
        return None
    cands = loaded.get("candidates")
    if not isinstance(cands, list):
        return None
    try:
        return [_candidate_from_json(c) for c in cands]
    except (KeyError, ValueError, TypeError):  # a schema change in the cached shape → re-extract
        return None


def _write_candidate_cache(path: Path, fingerprint: str, cands: Sequence[CandidateTrade]) -> None:
    """Atomically persist a day's candidates + fingerprint (tmp + os.replace, like write_json)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fingerprint, "candidates": [_candidate_to_json(c) for c in cands]}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def _extract_day_trades_cached(
    store: Store,
    s: Settings,
    trading_date: date,
    cache_dir: Path | None,
    settings_fp: str,
    *,
    force: bool,
) -> list[CandidateTrade]:
    """:func:`extract_day_trades` with a fingerprinted on-disk cache (``cache_dir=None`` disables).

    On a cache hit the day is not re-read/re-computed at all; ``force`` skips the read so a date the
    caller knows just changed is always re-extracted (and its fingerprint refreshed)."""
    if cache_dir is None:
        return extract_day_trades(store, s, trading_date)
    fingerprint = _day_fingerprint(store, s, trading_date, settings_fp)
    path = cache_dir / f"{trading_date.isoformat()}.json"
    if not force:
        cached = _read_candidate_cache(path, fingerprint)
        if cached is not None:
            return cached
    cands = extract_day_trades(store, s, trading_date)
    _write_candidate_cache(path, fingerprint, cands)
    return cands


def build_portfolio_payload(
    store: Store,
    s: Settings,
    generated_utc: datetime,
    *,
    cache_dir: Path | None = None,
    force_dates: Iterable[date] | None = None,
) -> dict[str, object]:
    """Build the ``portfolio.json`` the web page reads: the adaptive book plus a fixed-target sweep.

    Extracts every day's qualifying trades once, then simulates the adaptive (daily re-fit) book
    and one fixed-target book per selectable target — all server-side so the page needs no bars and
    no duplicated logic. Written to ``/data/dashboard`` at EOD and shipped by publish-dashboard.

    ``cache_dir`` enables the per-day candidate cache (see :func:`portfolio_candidate_cache_dir`) so
    a single-date backfill re-extracts only the day(s) in ``force_dates`` and reads the rest from
    cache instead of re-doing the whole archive; leave it None to always extract fresh."""
    settings_fp = _settings_fingerprint(s)
    force = set(force_dates or ())
    by_day = [
        (
            d,
            _extract_day_trades_cached(store, s, d, cache_dir, settings_fp, force=d in force),
        )
        for d in collected_dates(store)
    ]
    adaptive_res, daily_targets, daily_risk = simulate_portfolio_adaptive(by_day, s)
    # Selectable fixed targets: the adaptive grid widened with a couple of extremes for exploration.
    targets = sorted(set(s.portfolio_target_grid) | {1.0, 4.0, 5.0})
    books: dict[str, object] = {"adaptive": _book_json(adaptive_res, daily_targets, daily_risk)}
    for t in targets:
        books[f"{t:g}"] = _book_json(simulate_portfolio(by_day, s, target_r=t), None)
    return {
        "generated_utc": generated_utc.isoformat(),
        "start_equity": s.portfolio_start_equity_usd,
        "gbpusd_rate": s.portfolio_gbpusd_rate,
        "config": {
            "risk_fraction": s.portfolio_risk_fraction,
            "position_fraction": s.portfolio_position_fraction,
            "max_trades_per_day": s.portfolio_max_trades_per_day,
            "premarket_cutoff_et": s.portfolio_premarket_cutoff.isoformat(),
            "entry_price_min": s.portfolio_entry_price_min,
            "entry_price_max": s.portfolio_entry_price_max,
            "breakeven_r": s.portfolio_breakeven_r,
            "commission_per_share": s.portfolio_commission_per_share,
            "commission_min": s.portfolio_commission_min,
            "exchange_fee_per_share": s.portfolio_exchange_fee_per_share,
            "clearing_fee_per_share": s.portfolio_clearing_fee_per_share,
            "market_data_usd_per_month": s.portfolio_market_data_usd_per_month,
            "market_data_waiver_usd": s.portfolio_market_data_waiver_usd,
            "exit_slippage_ticks": s.portfolio_exit_slippage_ticks,
            "adaptive_window_days": s.portfolio_adaptive_window_days,
            "adaptive_min_samples": s.portfolio_adaptive_min_samples,
            # Getting-paid layer.
            "gbpusd_rate": s.portfolio_gbpusd_rate,
            "withdraw_fraction": s.portfolio_withdraw_fraction,
            "withdraw_cadence_months": s.portfolio_withdraw_cadence_months,
            "withdraw_floor_usd": s.portfolio_withdraw_floor_usd,
            "cgt_rate": s.portfolio_cgt_rate,
            "cgt_annual_exempt_gbp": s.portfolio_cgt_annual_exempt_gbp,
            "vps_gbp_per_month": s.portfolio_vps_gbp_per_month,
            # Adaptive risk throttle / kill-switch.
            "risk_rungs": s.portfolio_risk_rungs,
            "risk_ladder": list(risk_ladder(s)),
            "risk_step_days": s.portfolio_risk_step_days,
        },
        "targets": [f"{t:g}" for t in targets],
        "books": books,
    }
