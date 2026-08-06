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
from ..logging import get_logger
from ..storage import Store
from .adaptive import risk_ladder
from .extract import extract_day_trades
from .models import CandidateTrade, PaperTrade, PortfolioResult, SkippedTrade
from .projection import build_projection, day_rate_net_annual_gbp
from .sim import AdaptiveState, TargetFit, simulate_portfolio, simulate_portfolio_adaptive

log = get_logger(__name__)


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
        # What the position actually risked vs the ceiling it was sized against (#286). `risk_pct`
        # is the honest number; `sized_by` says whether the notional cap held it under the ceiling.
        "risk_fraction": t.risk_fraction,
        "risk_usd": t.risk_usd,
        "risk_pct": t.risk_pct,
        "sized_by": t.sized_by,
        "target_r": t.target_r,
        "realized_r": t.realized_r,
        # What the setup offered vs what this exit took (#390): `max_r - realized_r` is the R left
        # on the table, and `max_pct` is the same peak as a plain move so a wide-stop trade's modest
        # R doesn't hide a big run. `float_shares` is the name's float at flag time.
        "max_r": t.max_r,
        "max_pct": t.max_gain_pct,
        "float_shares": t.float_shares,
        "reason": t.reason,
        "exit_price": t.exit_price,
        "gross_pnl": t.gross_pnl_usd,
        "costs": round(t.commission_usd + t.fees_usd, 4),
        "net_pnl": t.net_pnl_usd,
        "equity_after": t.equity_after,
        # Provenance (#430) — "live" (the tracker saw it) vs "recon" (rebuilt from vendor bars).
        "source": t.source,
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
        "max_r": sk.max_r,
        "max_pct": sk.max_gain_pct,
        "float_shares": sk.float_shares,
        "reason": sk.reason,
        "exit_price": sk.exit_price,
        "skip_reason": sk.skip_reason,
        "source": sk.source,
    }


def _state_json(st: AdaptiveState) -> dict[str, object]:
    return {
        "as_of": st.as_of.isoformat(),
        "target_r": st.target_r,
        # Is that target the optimiser's answer or the fallback standing in? (#463)
        "target_fitted": st.target_fitted,
        "target_trailing_n": st.target_trailing_n,
        "risk_fraction": st.risk_fraction,
        "rung": st.rung,
        "n_rungs": st.n_rungs,
        "streak": st.streak,
        "step_days": st.step_days,
        "risk_budget_usd": st.risk_budget_usd,
        "max_position_usd": st.max_position_usd,
    }


def _by_source_json(res: PortfolioResult) -> dict[str, object]:
    """Split the book's headline numbers by provenance (#430).

    A combined book splices reconstructed days (earlier) in front of live ones, so its equity curve
    and its stats are a *blend* of evidence of two different strengths. Path-dependent numbers
    (equity, drawdown, expectancy in $) cannot be attributed to one source after the fact — they
    depend on the whole ordering — so this reports only the size-independent, per-trade ones, which
    can: how many trades each source contributed, and what they returned in R. That is enough to
    answer "is the combined result being carried by the reconstruction?" without inventing a
    per-source equity curve that never existed."""
    out: dict[str, object] = {}
    for src in ("live", "recon"):
        trades = [t for t in res.trades if t.source == src]
        wins = sum(1 for t in trades if t.realized_r > 0)
        n = len(trades)
        out[src] = {
            "n_trades": n,
            "wins": wins,
            "losses": n - wins,
            "win_rate": round(wins / n, 4) if n else None,
            "total_r": round(sum(t.realized_r for t in trades), 4),
            "avg_r": round(sum(t.realized_r for t in trades) / n, 4) if n else None,
            "n_days": len({t.trading_date for t in trades}),
        }
    return out


def _book_json(
    res: PortfolioResult,
    s: Settings,
    daily_targets: list[tuple[date, TargetFit]] | None,
    daily_risk: list[tuple[date, float]] | None = None,
    state: AdaptiveState | None = None,
    *,
    with_projection: bool = True,
) -> dict[str, object]:
    # The realised risk the book actually took, vs the ceiling the header advertises (#286). The
    # mean is over taken trades only — a skipped setup risked nothing and would drag it toward 0.
    trades = res.trades
    avg_risk_pct = round(sum(t.risk_pct for t in trades) / len(trades), 6) if trades else None
    book: dict[str, object] = {
        "stats": {
            "n_trades": res.n_trades,
            "wins": res.wins,
            "losses": res.losses,
            "win_rate": res.win_rate,
            "total_r": res.total_r,
            "avg_r": res.avg_r,
            "expectancy_usd": res.expectancy_usd,
            # Sizing reality-check (#286): what was risked on average, and how many trades the
            # notional cap held below the configured ceiling.
            "avg_risk_pct": avg_risk_pct,
            "cap_bound_count": sum(1 for t in trades if t.sized_by == "cap"),
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
            # Live vs reconstructed, kept apart so a combined book can never be read as if every
            # trade were equally well evidenced (#430). All-live books carry a zeroed "recon" half.
            "by_source": _by_source_json(res),
        },
        "equity_curve": [{"date": d.isoformat(), "equity": e} for d, e in res.equity_curve],
        "trades": [_trade_json(t) for t in res.trades],
        "skipped": [_skipped_json(sk) for sk in res.skipped],
        "cash_flows": [
            {"date": cf.date.isoformat(), "kind": cf.kind, "usd": cf.usd, "gbp": cf.gbp}
            for cf in res.cash_flows
        ],
        # The forward view (see `projection`): the same book resampled a year into the future, for
        # the drawdown you'd have to sit through and the date the payouts start. Per book, because
        # every book has its own return distribution — projecting the adaptive one and labelling it
        # "5R" would answer a question nobody asked.
        #
        # Deliberately live-only (#430). The projection answers "what will *my account* do", so it
        # has to resample the return distribution the tracker actually observed; bootstrapping it
        # from a history dominated by reconstructed days would forecast an account that trades a
        # universe we know differs from the live one — through appearance TIMING (#433), not the
        # 50-row rank cap once blamed for it, which has never actually bound (#460). Skipping it
        # on the combined books also keeps the EOD build off a second 500-path × 252-day Monte Carlo
        # per target on a 2-vCPU box.
        "projection": build_projection(res, s) if with_projection else None,
    }
    if daily_targets is not None:
        # `fitted` / `n` per day (#463) so the chart can separate the days the optimiser ran from
        # the days it fell back. Without them a flat line reads as "the fit kept choosing the same
        # rung" when it can equally mean the fit never ran — which is what it did mean.
        book["daily_targets"] = [
            {"date": d.isoformat(), "target": f.target_r, "fitted": f.fitted, "n": f.trailing_n}
            for d, f in daily_targets
        ]
    if daily_risk is not None:
        book["daily_risk"] = [{"date": d.isoformat(), "risk": r} for d, r in daily_risk]
    if state is not None:
        book["next_session"] = _state_json(state)
    return book


# The portfolio book is *cross-day*, so :func:`build_portfolio_payload` needs every collected day's
# qualifying trades. Extracting one day (segment + R-metrics per opportunity) costs about as much
# as one EOD report, so rebuilding the whole book from scratch on *every single-date dashboard
# backfill* silently did full-archive-scale work — the per-date backfill that should take seconds
# took minutes as history grew (the very ``--all`` workload CLAUDE.md warns off the box). A day's
# candidates are a pure function of that day's raw partitions + the settings that drive extraction,
# and the raw store is append-only immutable (with one sanctioned exception: `compact.py` may
# rewrite a closed partition's file layout with identical contents, #319 — which correctly busts
# that day's fingerprint and costs one re-extract), so we cache each day's extracted candidates on
# disk keyed by a fingerprint of (those partition files, the whole settings model). A single-date
# backfill then re-extracts only the day that changed and reads the rest back from cache; any
# settings change or late-arriving/backfilled partition shifts the fingerprint and forces a correct
# re-extract, so compute-on-read is preserved. The cache lives under ``<data_dir>/cache`` (NOT
# ``dashboard/``, which publish-dashboard force-pushes wholesale to a public branch) and is fully
# regenerable.
_CANDIDATE_CACHE_SUBDIR = ("cache", "portfolio_candidates")

# Every dataset `extract_day_trades` reads. `fundamentals` is here because the candidate now carries
# the name's float (#390), and the EOD backfill (`capture.capture_missing_fundamentals`) lands rows
# for a day whose bars/opportunities are already final — without it that day's fingerprint would be
# unchanged and the cache would keep serving candidates with a null float forever.
_EXTRACT_DATASETS = ("opportunities", "bars", "scanner_hits", "fundamentals")


def portfolio_candidate_cache_dir(s: Settings, source: str = "live") -> Path:
    """Directory holding the per-day extracted-candidate cache — off the published dashboard dir.

    Keyed by ``source`` (#430) because the cache filename is the date alone. The two stores are
    *expected* to be date-disjoint, but "expected" is not "enforced": one overlap day — a
    calibration date harvested into both — would otherwise have the live and reconstructed
    extractions overwrite each other under the same path, and each rebuild would flip which one the
    book saw. Separate directories make that impossible rather than unlikely."""
    root = s.data_dir.joinpath(*_CANDIDATE_CACHE_SUBDIR)
    return root if source == "live" else root.with_name(f"{root.name}_{source}")


def recon_store_dir(s: Settings) -> Path | None:
    """Root of the reconstructed-history store (#430), or None when the feature is switched off.

    The directory need not exist: :meth:`Store.read` on a missing tree returns an empty frame, so a
    box that has not harvested anything yet produces a payload identical to today's."""
    return s.data_dir / s.recon_subdir if s.recon_subdir else None


def open_recon_store(s: Settings) -> Store | None:
    """The reconstructed-history :class:`Store`, or None when disabled — for payload call sites."""
    d = recon_store_dir(s)
    return None if d is None else Store(d)


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
        "float_shares": c.float_shares,
        "max_r": c.max_r,
        "max_gain_pct": c.max_gain_pct,
        "source": c.source,
        "bars": [_bar_to_json(b) for b in c.bars],
    }


def _opt_float(v: Any) -> float | None:
    return None if v is None else float(v)


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
        # Indexed, not `.get()`, on purpose: a cache written before these fields existed raises
        # KeyError here, which `_read_candidate_cache` turns into a re-extract. A `.get()` default
        # would instead serve a null float / null Max R for every historical day, permanently.
        float_shares=None if d["float_shares"] is None else int(d["float_shares"]),
        max_r=_opt_float(d["max_r"]),
        max_gain_pct=_opt_float(d["max_gain_pct"]),
        source=str(d["source"]),
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
    source: str = "live",
) -> list[CandidateTrade]:
    """:func:`extract_day_trades` with a fingerprinted on-disk cache (``cache_dir=None`` disables).

    On a cache hit the day is not re-read/re-computed at all; ``force`` skips the read so a date the
    caller knows just changed is always re-extracted (and its fingerprint refreshed)."""
    if cache_dir is None:
        return extract_day_trades(store, s, trading_date, source=source)
    fingerprint = _day_fingerprint(store, s, trading_date, settings_fp)
    path = cache_dir / f"{trading_date.isoformat()}.json"
    if not force:
        cached = _read_candidate_cache(path, fingerprint)
        if cached is not None:
            return cached
    cands = extract_day_trades(store, s, trading_date, source=source)
    _write_candidate_cache(path, fingerprint, cands)
    return cands


def _recon_days_within_budget(
    recon_store: Store,
    s: Settings,
    recon_all: Sequence[date],
    live_dates: set[date],
    recon_cache_dir: Path | None,
    settings_fp: str,
) -> tuple[list[tuple[date, list[CandidateTrade]]], int]:
    """Reconstructed days, newest-first, up to a candidate budget. Returns (days, days_dropped).

    This function is the bound on #273's failure mode, applied where it can still be applied.
    ``build_portfolio_payload`` retains every day's ``CandidateTrade``, and each one carries its
    ``bars`` tuple — the whole 04:00–16:00 chart window — because ``_books_json`` re-simulates the
    same ``by_day`` list once per selectable target. Peak memory is therefore linear in
    *days × candidates*, and that is precisely what OOM-killed the box in #264 with ~25 live days.
    A completed harvest makes it ~500, and a recon day can surface candidates the live scanner
    never showed — through appearance timing (#433), not the 50-row scanner cap once assumed, which
    #460 measured as never binding (pre-market peaks at 11 of 50).

    Budgeting on **candidates rather than days** is the point. Days are a proxy; candidates are the
    thing that costs memory, and the density of a reconstructed day is exactly the number nobody
    has measured yet (the harvest has produced none). A candidate budget self-adjusts: dense days
    buy fewer of them, sparse days buy more, and the ceiling holds either way.

    The walk is newest-first and stops **before** extracting a day it cannot afford, so the days it
    drops cost nothing to skip — the alternative, extracting everything and slicing afterwards, has
    already spent the memory by the time it decides. Newest-first also matches the harvest's own
    ordering, so what survives the cap is the segment contiguous with the live record.
    """
    budget = s.portfolio_recon_max_candidates
    kept: list[tuple[date, list[CandidateTrade]]] = []
    spent = 0
    dropped = 0
    for d in sorted(recon_all, reverse=True):
        if d in live_dates:
            continue  # live wins on an overlap; not a cap decision, and not counted as dropped
        trades = _extract_day_trades_cached(
            recon_store, s, d, recon_cache_dir, settings_fp, force=False, source="recon"
        )
        # Always take the first day, whatever it costs: a budget that can yield nothing at all would
        # turn one unusually busy session into an empty `books_all` with no explanation.
        if budget > 0 and kept and spent + len(trades) > budget:
            # `<=`, not `<`: `d` is the day being refused, so it is the newest of the dropped set.
            dropped = sum(1 for x in recon_all if x <= d and x not in live_dates)
            log.warning(
                "portfolio.recon_capped",
                budget=budget,
                kept_days=len(kept),
                dropped_days=dropped,
                oldest_kept=d.isoformat(),
            )
            break
        kept.append((d, trades))
        spent += len(trades)
    kept.reverse()  # callers splice ascending
    return kept, dropped


def _coverage_json(by_day: Sequence[tuple[date, list[CandidateTrade]]]) -> dict[str, object]:
    """Span + volume of one provenance's contribution, for the page's coverage line."""
    days = [d for d, _ in by_day]
    return {
        "from": min(days).isoformat() if days else None,
        "to": max(days).isoformat() if days else None,
        "days": len(days),
        "candidates": sum(len(c) for _, c in by_day),
    }


def _books_json(
    by_day: Sequence[tuple[date, list[CandidateTrade]]],
    s: Settings,
    targets: Sequence[float],
    *,
    with_projection: bool,
) -> dict[str, object]:
    """One full set of books — the adaptive re-fit plus a fixed book per selectable target."""
    adaptive = simulate_portfolio_adaptive(list(by_day), s)
    books: dict[str, object] = {
        "adaptive": _book_json(
            adaptive.result,
            s,
            adaptive.daily_targets,
            adaptive.daily_risk,
            adaptive.state,
            with_projection=with_projection,
        )
    }
    for t in targets:
        books[f"{t:g}"] = _book_json(
            simulate_portfolio(list(by_day), s, target_r=t),
            s,
            None,
            with_projection=with_projection,
        )
    return books


def build_portfolio_payload(
    store: Store,
    s: Settings,
    generated_utc: datetime,
    *,
    cache_dir: Path | None = None,
    force_dates: Iterable[date] | None = None,
    recon_store: Store | None = None,
    recon_cache_dir: Path | None = None,
) -> dict[str, object]:
    """Build the ``portfolio.json`` the web page reads: the adaptive book plus a fixed-target sweep.

    Extracts every day's qualifying trades once, then simulates the adaptive (daily re-fit) book
    and one fixed-target book per selectable target — all server-side so the page needs no bars and
    no duplicated logic. Written to ``/data/dashboard`` at EOD and shipped by publish-dashboard.

    ``cache_dir`` enables the per-day candidate cache (see :func:`portfolio_candidate_cache_dir`) so
    a single-date backfill re-extracts only the day(s) in ``force_dates`` and reads the rest from
    cache instead of re-doing the whole archive; leave it None to always extract fresh.

    **Reconstructed history (#430).** ``recon_store`` is an optional second store holding days
    rebuilt from purchased vendor minute bars (see :func:`recon_store_dir`). When it carries days,
    the payload grows a *second* set of books under ``books_all``, simulated over the reconstructed
    days spliced in front of the live ones in date order — the deepening sample the nightly harvest
    exists to produce.

    ``books`` itself stays **live-only and byte-identical to before**, and that is the load-bearing
    part. The book is path-dependent twice over: the adaptive re-fit chooses each day's target and
    risk rung from a trailing window, and every position sizes off the running equity. Splicing ~500
    reconstructed days in front of the live ones therefore does not *extend* the live record, it
    *replaces* it — the live segment would start from whatever equity the reconstruction ended at
    and trade targets chosen by vendor-derived trades. Phase-1's deliverable is what the tracker
    actually saw, so the two are published side by side rather than merged in place.
    """
    settings_fp = _settings_fingerprint(s)
    force = set(force_dates or ())
    by_day = [
        (
            d,
            _extract_day_trades_cached(store, s, d, cache_dir, settings_fp, force=d in force),
        )
        for d in collected_dates(store)
    ]
    live_dates = {d for d, _ in by_day}
    # A date the tracker watched live is never taken from the reconstruction, even if the harvest
    # also covered it (the #428 calibration days are exactly that overlap). Live is the ground
    # truth the reconstruction is *calibrated against*; preferring it keeps the combined book from
    # double-counting a day, and from quietly substituting vendor bars for observed ones.
    recon_all = collected_dates(recon_store) if recon_store is not None else []
    recon_by_day, recon_capped = (
        _recon_days_within_budget(
            recon_store, s, recon_all, live_dates, recon_cache_dir, settings_fp
        )
        if recon_store is not None
        else ([], 0)
    )
    # Selectable fixed targets: the adaptive grid widened with a couple of extremes for exploration.
    targets = sorted(set(s.portfolio_target_grid) | {1.0, 4.0, 5.0})
    books = _books_json(by_day, s, targets, with_projection=True)
    payload: dict[str, object] = {
        "generated_utc": generated_utc.isoformat(),
        "start_equity": s.portfolio_start_equity_usd,
        "gbpusd_rate": s.portfolio_gbpusd_rate,
        "config": {
            "risk_fraction": s.portfolio_risk_fraction,
            "position_fraction": s.portfolio_position_fraction,
            "max_trades_per_day": s.portfolio_max_trades_per_day,
            "premarket_earliest_et": s.portfolio_premarket_earliest.isoformat(),
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
            # The grid the daily re-fit picks from, plus the target it falls back to before the
            # window has samples. `targets` (below) is the *selectable book* list — the grid
            # widened with extremes — so it can't stand in as the ladder for the target chart.
            "target_grid": sorted(s.portfolio_target_grid),
            "target_fallback_r": s.portfolio_target_r,
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
            # Forward projection — the knobs, so the page states its own assumptions rather
            # than hard-coding a day rate or horizon it would be wrong about after an edit.
            "projection_days": s.portfolio_projection_days,
            "projection_paths": s.portfolio_projection_paths,
            "projection_block_days": s.portfolio_projection_block_days,
            "day_rate_gbp": s.portfolio_day_rate_gbp,
            "day_rate_days_per_year": s.portfolio_day_rate_days_per_year,
            "day_rate_net_fraction": s.portfolio_day_rate_net_fraction,
            "day_rate_net_annual_gbp": day_rate_net_annual_gbp(s),
        },
        "targets": [f"{t:g}" for t in targets],
        "books": books,
    }
    # What each provenance contributed, so the page can state its own span rather than inferring it
    # from the trade log (a day with no qualifying setup contributes coverage but no trade).
    recon_cov = _coverage_json(recon_by_day)
    if recon_store is not None:
        # Overlap is dropped in favour of live above; report the count whenever a recon store was
        # consulted — including when *every* harvested day overlapped and nothing survived. Tying
        # this to `recon_by_day` instead would make a fully-overlapping harvest look like an
        # unharvested box, which is the one case where the reader most needs to know why.
        recon_cov["overlap_days_dropped"] = len([d for d in recon_all if d in live_dates])
        # Never a silent truncation: a capped payload must say so, or "coverage from 2025-02-11"
        # reads as "that is all the harvest has" rather than "that is all the payload can hold".
        recon_cov["capped_days_dropped"] = recon_capped
        recon_cov["candidate_budget"] = s.portfolio_recon_max_candidates
    payload["coverage"] = {"live": _coverage_json(by_day), "recon": recon_cov}
    if recon_by_day:
        combined = sorted([*recon_by_day, *by_day], key=lambda item: item[0])
        payload["books_all"] = _books_json(combined, s, targets, with_projection=False)
    return payload
