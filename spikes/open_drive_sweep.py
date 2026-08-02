"""Spike #418: Open Drive — a 10-minute ORB with a consolidation requirement.

The book trades one strategy, the pre-market bull-flag, gated to ``[05:30, 09:15)`` ET. The
time-of-day report (`docs/reports/2026-07-31-time-of-day-…`) measures the tape's forward excursion
peaking at 09:00–10:00 (+8.52% median 60-min upside pre-bell, +5.60% at 09:30–10:00) against
+1.12% at 04:00–06:00, with liquidity **170×** the pre-market — and the engine puts 32 of its 787
triggers there. Its recommendation #4 is to look harder at that window. This is that look.

**The strategy, stated as an ORB.** The 09:30–09:35 bar is the opening range; the 09:35–09:40 bar is
a consolidation of it; entry triggers one tick above the consolidation high from 09:40. Framing it
as an *opening-range breakout with a consolidation requirement* makes the parameter that actually
matters obvious — where the range and the consolidation end — which is the secondary arm below.

⚠️ **The universe is symbols already on the scanner before the trigger fires.** That is not a gate,
a filter or a treatment: it is the definition of what could have been traded. A symbol the scanner
surfaced at 10:15 was never available at 09:40 and does not exist for this strategy. It is applied
in `extract_open_candidates` before anything is counted, and **no variant relaxes it** — when a
longer range moves the trigger to 09:50, the cutoff moves with it (`OrbLength.trigger_from`).

⚠️ Variants must be **decidable at trigger time** (the standing rule from #379). "First to trigger
today" is; "the best of today's candidates" is not.

⚠️ This replays from the **Parquet store**, so it runs on the box or a machine with a store copy:

    docker exec small-cap-stack-app-1 python /tmp/open_drive_sweep.py --store /data --validate
    docker exec small-cap-stack-app-1 python /tmp/open_drive_sweep.py --store /data \
        --payload /tmp/portfolio.json --json /tmp/open-drive.json

`--validate` replays the *current* book through the production `simulate_portfolio_adaptive` and
asserts it reproduces the published `portfolio.json` trade-for-trade. If that fails the replay is
not faithful and no other number here is either.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from small_cap_stack.capture import Bar
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.portfolio import sim as sim_mod
from small_cap_stack.portfolio.extract import extract_day_trades
from small_cap_stack.portfolio.models import CandidateTrade
from small_cap_stack.portfolio.payload import collected_dates
from small_cap_stack.portfolio.sim import simulate_portfolio, simulate_portfolio_adaptive
from small_cap_stack.report import _funds_for, day_chart_bars, day_opportunities
from small_cap_stack.storage import Store

MARKET_OPEN = time(9, 30)

# Fixed seeds: a spike whose CIs move between runs can't be argued with.
BOOTSTRAP_DRAWS = 3000
PERMUTATION_DRAWS = 20_000
SEED = 418


# --------------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------------


def _shift(t: time, minutes: int) -> time:
    return (datetime.combine(date(2000, 1, 1), t) + timedelta(minutes=minutes)).time()


@dataclass(frozen=True)
class OrbLength:
    """One (opening range, consolidation) split. The primary spec is 5/5; the rest are the
    secondary arm. Each carries its own universe cutoff, because a later trigger legitimately
    admits symbols the scanner surfaced later."""

    label: str
    range_min: int
    cons_min: int

    @property
    def cons_start(self) -> time:
        return _shift(MARKET_OPEN, self.range_min)

    @property
    def trigger_from(self) -> time:
        """Also the universe cutoff: seen on the scanner strictly before this."""
        return _shift(MARKET_OPEN, self.range_min + self.cons_min)


ORB_LENGTHS = (
    OrbLength("OD-5/5", 5, 5),  # primary — the owner's spec
    OrbLength("OD-10/5", 10, 5),
    OrbLength("OD-15/5", 15, 5),
    OrbLength("OD-5/10", 5, 10),
)
PRIMARY = ORB_LENGTHS[0]


@dataclass(frozen=True)
class Agg:
    """One or more consecutive bars collapsed into a single candle."""

    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low


def aggregate(bars: Sequence[Bar]) -> Agg | None:
    """Collapse a window of 5-min bars into one candle (multi-bar ranges in the secondary arm)."""
    if not bars:
        return None
    return Agg(
        open=bars[0].open,
        high=max(b.high for b in bars),
        low=min(b.low for b in bars),
        close=bars[-1].close,
        volume=sum(b.volume for b in bars),
    )


def window(bars: Sequence[Bar], start: time, end: time) -> list[Bar]:
    """Bars whose ET start falls in ``[start, end)``."""
    return [b for b in bars if start <= b.start.astimezone(ET).time() < end]


# --------------------------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OdCandidate:
    """A `CandidateTrade` (so the production exit model and book apply unchanged) plus the
    Open-Drive features the gates and contrasts are measured on."""

    trade: CandidateTrade
    feat: dict[str, Any]


def _gates(rng: Agg, cons: Agg) -> dict[str, bool]:
    """The four pre-registered gates. Fixed before any fitting; nothing is tuned here.

    `cons_wickier` and a pre-market-relative RVOL were in the owner's original statement and are
    deliberately **absent** — both measured inert at n=138 (P(≥2R) 19% either way; RVOL>1 to RVOL>10
    moved n 137→119 with flat stats) and were dropped rather than demoted to score terms. The
    measurement that dropped them is reported, so the decision stays auditable."""
    return {
        "open_green": rng.body > 0,
        "body_dominant": rng.body > rng.upper_wick + rng.lower_wick,
        "cons_lower_vol": cons.volume < rng.volume,
        "cons_shorter": cons.range < rng.range,
    }


def _find_trigger(
    day_bars: Sequence[Bar],
    *,
    from_time: time,
    entry: float,
    stop: float,
    staleness_min: int | None,
) -> int | None:
    """Index of the bar that first trades through ``entry``, or None.

    **Stop-first**, mirroring `simulate_exit` / `rmetrics._measure`: a bar that breaches the
    consolidation low before reaching the trigger kills the setup rather than filling it. A bar
    doing both in the same 5 minutes counts as the break — conservative, and the only assumption
    5-min bars support."""
    deadline = _shift(from_time, staleness_min) if staleness_min is not None else None
    for k, b in enumerate(day_bars):
        et = b.start.astimezone(ET).time()
        if et < from_time:
            continue
        if deadline is not None and et >= deadline:
            return None
        if b.low <= stop:
            return None
        if b.high >= entry:
            return k
    return None


def extract_open_candidates(
    store: Store,
    s: Settings,
    trading_date: date,
    *,
    orb: OrbLength = PRIMARY,
    staleness_min: int | None = None,
    apply_gates: bool = True,
) -> list[OdCandidate]:
    """Every Open-Drive candidate for one day, in trigger-time order.

    Mirrors `portfolio.extract.extract_day_trades` in shape and ordering discipline, and reads
    through the same `day_opportunities` / `day_chart_bars` / `_funds_for` seams so this can't
    drift from the results page. Unlike the bull-flag path there is no run segmentation: the setup
    is fixed by the clock, so there is at most one per symbol-day.

    ``apply_gates=False`` keeps every symbol whose range/consolidation geometry is well-formed and
    that triggered, tagging the gate outcomes as features instead of filtering on them. **The
    contrast table needs this**: a gate cannot be shown to earn its keep on a population it has
    already filtered — measured post-gate, every gate trivially has an empty "false" cell. The
    universe cutoff is *not* part of this and is applied either way; it is not a gate."""
    opps = day_opportunities(store, trading_date)
    if opps.is_empty():
        return []
    bars_df = store.read("bars", dt=trading_date)
    funds = store.read("fundamentals", dt=trading_date)
    excluded = {sym.upper() for sym in s.portfolio_exclude_symbols}
    tick = s.tick_size

    out: list[OdCandidate] = []
    for row in opps.iter_rows(named=True):
        if str(row["symbol"]).upper() in excluded:
            continue

        # THE UNIVERSE. Not a gate — what could have been traded. Never relaxed anywhere.
        first_seen = row["first_seen_utc"]
        if first_seen is None or first_seen.astimezone(ET).time() >= orb.trigger_from:
            continue

        oid = row["opportunity_id"]
        day_bars = day_chart_bars(bars_df, oid, s)
        if not day_bars:
            continue

        rng = aggregate(window(day_bars, MARKET_OPEN, orb.cons_start))
        cons = aggregate(window(day_bars, orb.cons_start, orb.trigger_from))
        if rng is None or cons is None or rng.range <= 0 or cons.range <= 0:
            continue

        gates = _gates(rng, cons)
        if apply_gates and not all(gates.values()):
            continue

        # House convention (#182/#190): 1 tick decides *when*, 3 ticks is what R is measured
        # against. Stop is the consolidation low.
        entry_trigger = cons.high + tick
        entry_fill = cons.high + 3 * tick
        stop = cons.low
        if entry_fill <= stop:
            continue

        k = _find_trigger(
            day_bars,
            from_time=orb.trigger_from,
            entry=entry_trigger,
            stop=stop,
            staleness_min=staleness_min,
        )
        if k is None:
            continue

        # Gap-through, mirroring `rmetrics._measure`: fill no better than the trigger bar's open.
        entry_price = max(entry_fill, day_bars[k].open)
        risk = entry_price - stop
        if risk <= 0:
            continue

        float_shares, _short = _funds_for(funds, oid)
        trade = CandidateTrade(
            trading_date=trading_date,
            symbol=row["symbol"],
            seg_id=oid,
            run=1,
            trigger_at=day_bars[k].start,
            entry_price=entry_price,
            entry_fill=entry_fill,
            stop=stop,
            risk=risk,
            entry_index=k,
            bars=tuple(day_bars),
            float_shares=float_shares,
        )
        out.append(
            OdCandidate(
                trade=trade,
                feat={
                    "orb": orb.label,
                    "price": rng.close,
                    "risk_abs": risk,
                    "risk_pct": risk / entry_price,
                    "range_move_pct": rng.body / rng.open if rng.open > 0 else 0.0,
                    "cons_vol_ratio": cons.volume / rng.volume if rng.volume > 0 else None,
                    "cons_range_ratio": cons.range / rng.range,
                    "cons_green": cons.close > cons.open,
                    "cons_holds_under_range_high": cons.high <= rng.high,
                    "float_shares": float_shares,
                    "range_vol": rng.volume,
                    **gates,
                },
            )
        )

    # A total order (#381) — trigger_at alone is a stable sort over upstream row order.
    out.sort(key=lambda c: (c.trade.trigger_at, c.trade.symbol, c.trade.seg_id))
    return out


# --------------------------------------------------------------------------------------------
# Thresholds — fitted, but only four, each with a plateau rule
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    """The only fitted parameters. Defaults are permissive: a threshold whose improvement CI
    includes zero stays here, and that is reported as the result."""

    stop_floor_cents: float = 0.0  # absolute risk floor
    stop_ceiling_pct: float | None = None  # risk as a fraction of entry
    cons_vol_ratio_max: float = 1.0  # already implied by the cons_lower_vol gate at 1.0
    staleness_min: int | None = None

    def admits(self, c: OdCandidate) -> bool:
        f = c.feat
        if f["risk_abs"] < self.stop_floor_cents:
            return False
        if self.stop_ceiling_pct is not None and f["risk_pct"] > self.stop_ceiling_pct:
            return False
        cvr = f["cons_vol_ratio"]
        return cvr is not None and cvr <= self.cons_vol_ratio_max


STOP_FLOOR_GRID = (0.0, 0.05, 0.10, 0.15)
STOP_CEILING_GRID = (None, 0.10, 0.07, 0.05)
CONS_VOL_GRID = (1.0, 0.7, 0.5)
STALENESS_GRID = (None, 30, 20, 10)


# --------------------------------------------------------------------------------------------
# The 1-trade-a-day book
# --------------------------------------------------------------------------------------------


def select_one_per_day_list(
    cands: Sequence[OdCandidate], thresholds: Thresholds
) -> list[OdCandidate]:
    """The day's single pick as a (0- or 1-element) list, for feeding the book."""
    pick = select_one_per_day([c for c in cands if thresholds.admits(c)])
    return [pick] if pick is not None else []


def select_one_per_day(cands: Sequence[OdCandidate]) -> OdCandidate | None:
    """First to trigger. Ranking a day's candidates against each other is the lookahead bias
    #379 forbids; "first" is known the moment it fires."""
    return cands[0] if cands else None


@dataclass(frozen=True)
class DayResult:
    day: date
    symbol: str
    realized_r: float
    reason: str
    max_r: float | None
    feat: dict[str, Any]


def run_strategy(
    by_day: dict[date, list[OdCandidate]],
    s: Settings,
    *,
    thresholds: Thresholds,
    target_r: float,
    breakeven_r: float = 0.0,
) -> list[DayResult]:
    """One trade per day, under the book's own exit model."""
    out: list[DayResult] = []
    for day in sorted(by_day):
        admitted = [c for c in by_day[day] if thresholds.admits(c)]
        pick = select_one_per_day(admitted)
        if pick is None:
            continue
        ex = pick.trade.exit_under(s, target_r, breakeven_r)
        out.append(
            DayResult(
                day=day,
                symbol=pick.trade.symbol,
                realized_r=ex.realized_r,
                reason=ex.reason,
                max_r=pick.trade.max_r,
                feat=pick.feat,
            )
        )
    return out


def summarise(results: Sequence[DayResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"n_trades": 0}
    rs = [r.realized_r for r in results]
    wins = sum(1 for r in rs if r > 0)
    return {
        "n_trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "total_r": round(sum(rs), 3),
        "expectancy_r": round(sum(rs) / n, 3),
    }


# --------------------------------------------------------------------------------------------
# Statistics — day-clustered, because setups within a day are not independent
# --------------------------------------------------------------------------------------------


def day_block_bootstrap(
    by_day: dict[date, list[float]], *, draws: int = BOOTSTRAP_DRAWS, seed: int = SEED
) -> tuple[float, float, float]:
    """(mean, lo, hi) at 95%, resampling **days** with replacement.

    The unit of independence is the day, not the setup — the same choice the engine-feature and
    time-of-day reports make. Resampling setups would understate the interval."""
    days = sorted(by_day)
    pool = [by_day[d] for d in days if by_day[d]]
    if not pool:
        return (float("nan"),) * 3
    flat = [v for vs in pool for v in vs]
    mean = sum(flat) / len(flat)
    rnd = random.Random(seed)
    means: list[float] = []
    for _ in range(draws):
        vals: list[float] = []
        for _ in range(len(pool)):
            vals.extend(pool[rnd.randrange(len(pool))])
        if vals:
            means.append(sum(vals) / len(vals))
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return (mean, lo, hi)


def within_day_permutation(
    rows: Sequence[tuple[date, bool, float]],
    *,
    draws: int = PERMUTATION_DRAWS,
    seed: int = SEED,
) -> float:
    """Two-sided p for `mean(True) - mean(False)`, shuffling the label **within each day**.

    This holds the day fixed, so a result can't be manufactured by a single exceptional session."""

    def diff(labelled: Sequence[tuple[bool, float]]) -> float | None:
        a = [v for lab, v in labelled if lab]
        b = [v for lab, v in labelled if not lab]
        if not a or not b:
            return None
        return sum(a) / len(a) - sum(b) / len(b)

    flat = [(lab, v) for _, lab, v in rows]
    observed = diff(flat)
    if observed is None:
        return float("nan")

    grouped: dict[date, list[tuple[bool, float]]] = {}
    for d, lab, v in rows:
        grouped.setdefault(d, []).append((lab, v))

    rnd = random.Random(seed)
    hits = 0
    for _ in range(draws):
        shuffled: list[tuple[bool, float]] = []
        for items in grouped.values():
            labels = [lab for lab, _ in items]
            rnd.shuffle(labels)
            shuffled.extend((lab, v) for lab, (_, v) in zip(labels, items, strict=True))
        d0 = diff(shuffled)
        if d0 is not None and abs(d0) >= abs(observed):
            hits += 1
    return (hits + 1) / (draws + 1)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm–Bonferroni step-down across the pre-registered contrasts."""
    items = sorted(((p, k) for k, p in pvals.items() if p == p), key=lambda x: x[0])
    m = len(items)
    out: dict[str, float] = {}
    running = 0.0
    for i, (p, k) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = round(running, 5)
    return out


# The ten contrasts, fixed before the run. Each splits the candidate population in two.
CONTRASTS: tuple[tuple[str, Callable[[dict[str, Any]], bool | None]], ...] = (
    ("body_dominant", lambda f: f["body_dominant"]),
    ("cons_lower_vol", lambda f: f["cons_lower_vol"]),
    ("cons_shorter", lambda f: f["cons_shorter"]),
    ("risk_pct_below_median", lambda f: f["risk_pct"] < 0.04),
    ("stop_at_least_10c", lambda f: f["risk_abs"] >= 0.10),
    ("price_at_least_5", lambda f: f["price"] >= 5.0),
    (
        "float_at_least_20m",
        lambda f: None if f["float_shares"] is None else f["float_shares"] >= 20_000_000,
    ),
    ("cons_green", lambda f: f["cons_green"]),
    ("cons_holds_under_range_high", lambda f: f["cons_holds_under_range_high"]),
    ("range_move_below_5pct", lambda f: f["range_move_pct"] < 0.05),
)

MIN_CELL = 10  # below this a cell is "not measurable", not a result


def contrast_table(
    by_day: dict[date, list[OdCandidate]], s: Settings, *, target_r: float
) -> dict[str, Any]:
    """Every contrast measured on realised R over the **ungated** triggered population.

    Pass `by_day` built with ``apply_gates=False``: the selection is what the strategy *does*, the
    contrasts are what *separates*, and a gate measured downstream of itself has no false cell."""
    scored: list[tuple[date, dict[str, Any], float]] = []
    for day, cands in sorted(by_day.items()):
        for c in cands:
            ex = c.trade.exit_under(s, target_r, 0.0)
            scored.append((day, c.feat, ex.realized_r))

    raw: dict[str, float] = {}
    detail: dict[str, Any] = {}
    for name, pred in CONTRASTS:
        rows = [(d, bool(pred(f)), r) for d, f, r in scored if pred(f) is not None]
        a = [r for _, lab, r in rows if lab]
        b = [r for _, lab, r in rows if not lab]
        cell: dict[str, Any] = {"n_true": len(a), "n_false": len(b)}
        if len(a) < MIN_CELL or len(b) < MIN_CELL:
            cell["verdict"] = "not measurable"
            detail[name] = cell
            continue
        by_day_true: dict[date, list[float]] = {}
        for d, lab, r in rows:
            if lab:
                by_day_true.setdefault(d, []).append(r)
        mean_a = sum(a) / len(a)
        mean_b = sum(b) / len(b)
        _, lo, hi = day_block_bootstrap(by_day_true)
        p = within_day_permutation(rows)
        raw[name] = p
        cell.update(
            mean_true=round(mean_a, 3),
            mean_false=round(mean_b, 3),
            effect_r=round(mean_a - mean_b, 3),
            true_ci=[round(lo, 3), round(hi, 3)],
            p_raw=round(p, 5),
        )
        detail[name] = cell

    for name, p_adj in holm(raw).items():
        detail[name]["p_holm"] = p_adj
        detail[name]["survives_holm"] = p_adj < 0.05
    return detail


# --------------------------------------------------------------------------------------------
# Threshold fitting, with a plateau rule
# --------------------------------------------------------------------------------------------


def fit_thresholds(
    by_day: dict[date, list[OdCandidate]], s: Settings, *, target_r: float
) -> dict[str, Any]:
    """Sweep the four knobs one at a time from the permissive default.

    **Prefer a plateau over the argmax** (`decisions.md`'s standing rule for exit targets, for the
    same overfit reason), and **keep the permissive default whenever the winner's bootstrap CI on
    expectancy includes the default's point estimate** — at ~14 trades a knob has to earn its
    place, not merely win a coin toss."""
    base = Thresholds()
    base_res = run_strategy(by_day, s, thresholds=base, target_r=target_r)
    base_exp = summarise(base_res).get("expectancy_r")

    def sweep(field: str, grid: Iterable[Any]) -> dict[str, Any]:
        rows = []
        for v in grid:
            th = Thresholds(**{**base.__dict__, field: v})
            res = run_strategy(by_day, s, thresholds=th, target_r=target_r)
            summary = summarise(res)
            per_day = {r.day: [r.realized_r] for r in res}
            mean, lo, hi = day_block_bootstrap(per_day) if res else (float("nan"),) * 3
            rows.append(
                {
                    "value": v,
                    **summary,
                    "ci": [round(lo, 3), round(hi, 3)] if res else None,
                }
            )
        return {"grid": rows, "default": getattr(base, field)}

    return {
        "baseline": {"thresholds": base.__dict__, **summarise(base_res), "expectancy_r": base_exp},
        "stop_floor_cents": sweep("stop_floor_cents", STOP_FLOOR_GRID),
        "stop_ceiling_pct": sweep("stop_ceiling_pct", STOP_CEILING_GRID),
        "cons_vol_ratio_max": sweep("cons_vol_ratio_max", CONS_VOL_GRID),
        "staleness_min": sweep("staleness_min", STALENESS_GRID),
        "note": (
            "One knob at a time from the permissive default. A grid row only justifies moving off "
            "the default if its CI clears the default's point estimate; report the plateau, not "
            "the argmax."
        ),
    }


# --------------------------------------------------------------------------------------------
# The combined book — reserving slot 2
# --------------------------------------------------------------------------------------------


def _is_open_drive(c: CandidateTrade, s: Settings) -> bool:
    """Which stream a candidate came from, decided by its own trigger time — no tagging needed:
    bull-flag candidates trigger strictly before `portfolio_premarket_cutoff`, Open Drive strictly
    after the bell."""
    return c.trigger_at.astimezone(ET).time() >= s.portfolio_premarket_cutoff


@contextmanager
def reserved_slots(s: Settings, *, size_up_when_alone: bool = False) -> Any:
    """Patch `sim._select_day` so slot 1 is the day's first bull-flag and slot 2 its first Open
    Drive — instead of the first two by trigger time.

    Both are decidable at trigger time. `size_up_when_alone` additionally lets Open Drive take the
    full 1.0 notional fraction when no bull-flag candidate fired before the 09:15 cutoff: that is
    known by 09:40, and total daily buy notional still never exceeds the day's settled cash, which
    is what `research/broker-costs.md`'s good-faith rule requires."""
    orig_select = sim_mod._select_day
    orig_size = sim_mod.size_position
    alone = [False]  # day-local: did selection return an Open Drive and nothing else?

    def select(cands: Sequence[CandidateTrade], settings: Settings) -> list[CandidateTrade]:
        ordered = sorted(cands, key=lambda c: (c.trigger_at, c.symbol, c.seg_id, c.run))
        bull = next((c for c in ordered if not _is_open_drive(c, settings)), None)
        od = next((c for c in ordered if _is_open_drive(c, settings)), None)
        picked = [c for c in (bull, od) if c is not None]
        alone[0] = len(picked) == 1 and _is_open_drive(picked[0], settings)
        return picked

    def size_scaled(equity: float, entry_price: float, stop: float, **kw: Any) -> Any:
        if size_up_when_alone and alone[0]:
            kw = {**kw, "max_position_fraction": 1.0}
        return orig_size(equity, entry_price, stop, **kw)

    sim_mod._select_day = select  # type: ignore[assignment]
    sim_mod.size_position = size_scaled  # type: ignore[assignment]
    try:
        yield
    finally:
        sim_mod._select_day = orig_select  # type: ignore[assignment]
        sim_mod.size_position = orig_size  # type: ignore[assignment]


def _as_days(
    by_day: dict[date, list[CandidateTrade]],
) -> list[tuple[date, Sequence[CandidateTrade]]]:
    """The book takes a chronological sequence of (date, candidates), not a mapping."""
    return [(d, by_day[d]) for d in sorted(by_day)]


def _book_stats(result: Any) -> dict[str, Any]:
    return {
        "n_trades": result.n_trades,
        "wins": result.wins,
        "losses": result.losses,
        "total_r": round(result.total_r, 4),
        "end_equity": round(result.end_equity, 4),
        "return_pct": round(result.return_pct, 4),
        "max_drawdown_pct": round(result.max_drawdown_pct, 4),
    }


def combined_book(
    bull_by_day: dict[date, list[CandidateTrade]],
    od_by_day: dict[date, list[OdCandidate]],
    s: Settings,
    *,
    thresholds: Thresholds,
) -> dict[str, Any]:
    """Baseline vs slot-2-reserved vs slot-2-reserved-with-size-up, all through the real book.

    Run at **both** the adaptive target/risk and a fixed 2R, because the adaptive book confounds
    the comparison: its target is re-fit over a trailing window of *all* candidates and its risk
    ladder steps on the day's aggregate R, so adding a second strategy changes the target and rung
    the **bull-flag** trades get — the merged book's bull-flag leg is no longer the baseline's.
    The fixed-2R pair isolates what Open Drive itself contributes. #416 ran both for this reason."""
    merged: dict[date, list[CandidateTrade]] = {}
    for day in sorted(set(bull_by_day) | set(od_by_day)):
        picks = list(bull_by_day.get(day, []))
        od = select_one_per_day([c for c in od_by_day.get(day, []) if thresholds.admits(c)])
        if od is not None:
            picks.append(od.trade)
        merged[day] = picks

    bull_days = _as_days(bull_by_day)
    merged_days = _as_days(merged)
    # Open Drive alone, on its own capital — the cleanest read on what the strategy is worth,
    # with none of the shared target/risk machinery in the way.
    od_only = _as_days(
        {
            d: [c.trade for c in select_one_per_day_list(od_by_day.get(d, []), thresholds)]
            for d in sorted(set(bull_by_day) | set(od_by_day))
        }
    )
    fixed = 2.0

    out: dict[str, Any] = {"adaptive": {}, "fixed_2r": {}}
    out["adaptive"]["open_drive_alone"] = _book_stats(
        simulate_portfolio_adaptive(od_only, s).result
    )
    out["fixed_2r"]["open_drive_alone"] = _book_stats(
        simulate_portfolio(od_only, s, target_r=fixed)
    )
    out["adaptive"]["baseline"] = _book_stats(simulate_portfolio_adaptive(bull_days, s).result)
    out["fixed_2r"]["baseline"] = _book_stats(simulate_portfolio(bull_days, s, target_r=fixed))
    with reserved_slots(s):
        out["adaptive"]["slot2_reserved"] = _book_stats(
            simulate_portfolio_adaptive(merged_days, s).result
        )
        out["fixed_2r"]["slot2_reserved"] = _book_stats(
            simulate_portfolio(merged_days, s, target_r=fixed)
        )
    with reserved_slots(s, size_up_when_alone=True):
        out["adaptive"]["slot2_reserved_size_up"] = _book_stats(
            simulate_portfolio_adaptive(merged_days, s).result
        )
        out["fixed_2r"]["slot2_reserved_size_up"] = _book_stats(
            simulate_portfolio(merged_days, s, target_r=fixed)
        )

    # Why the R doesn't become money: Open Drive's stops are tight (1-7% of entry), and #416
    # established the notional cap binds whenever the stop is tighter than
    # risk_fraction/position_fraction = 10% of entry. So the cap sizes nearly every trade and each
    # one risks ~1% of equity rather than the configured 5%. Sweep both knobs on the standalone
    # book to see what it takes to monetise the R.
    sizing: list[dict[str, Any]] = []
    for rf, pf in ((0.05, 0.50), (0.05, 1.0), (0.10, 1.0), (0.15, 1.0), (0.20, 1.0)):
        cfg = s.model_copy(
            update={"portfolio_risk_fraction": rf, "portfolio_position_fraction": pf}
        )
        res = simulate_portfolio(od_only, cfg, target_r=fixed)
        sizing.append(
            {
                "risk_fraction": rf,
                "position_fraction": pf,
                "cap_bound": sum(1 for tr in res.trades if tr.sized_by == "cap"),
                "avg_risk_pct": round(sum(tr.risk_pct for tr in res.trades) / len(res.trades), 4)
                if res.trades
                else None,
                **_book_stats(res),
            }
        )
    out["open_drive_sizing"] = sizing

    # The bull-flag leg on its own, under the merged book's selection — if this differs from the
    # baseline, the deterioration is the shared adaptive machinery, not Open Drive's own trades.
    out["note"] = (
        "Compare like with like: `adaptive` re-fits target and risk over the merged candidate "
        "stream, so its bull-flag leg is not the baseline's. `fixed_2r` holds both constant."
    )
    return out


# --------------------------------------------------------------------------------------------
# Reproduction check
# --------------------------------------------------------------------------------------------


def validate(bull_by_day: dict[date, list[CandidateTrade]], s: Settings, payload: Path) -> bool:
    """Replay today's book and assert it reproduces the published `portfolio.json`.

    Gates every other number: if this replay isn't faithful, nothing downstream of it is."""
    published = json.loads(payload.read_text())["books"]["adaptive"]
    want = published["stats"]
    got = simulate_portfolio_adaptive(_as_days(bull_by_day), s).result

    checks = [
        ("n_trades", want["n_trades"], got.n_trades),
        ("wins", want["wins"], got.wins),
        ("losses", want["losses"], got.losses),
        ("total_r", round(want["total_r"], 3), round(got.total_r, 3)),
        ("end_equity", round(want["end_equity"], 2), round(got.end_equity, 2)),
    ]
    ok = all(w == g for _, w, g in checks)
    print("--- reproduction check vs published portfolio.json ---")
    for name, w, g in checks:
        print(f"  {'ok ' if w == g else 'FAIL'} {name:12s} published={w!r:>12} replay={g!r:>12}")

    pub_trades = [(t["date"], t["symbol"], round(t["realized_r"], 3)) for t in published["trades"]]
    our_trades = [
        (t.trading_date.isoformat(), t.symbol, round(t.realized_r, 3)) for t in got.trades
    ]
    if pub_trades != our_trades:
        ok = False
        print("  FAIL trade-for-trade mismatch")
        for p, o in zip(pub_trades, our_trades, strict=False):
            if p != o:
                print(f"        published={p} replay={o}")
    else:
        print(f"  ok  trade-for-trade  {len(our_trades)} trades match")
    print(f"--- {'VALIDATED' if ok else 'NOT VALIDATED — no other number here is trustworthy'} ---")
    return ok


# --------------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True, type=Path, help="Parquet store root (/data on box)")
    ap.add_argument("--payload", type=Path, help="published portfolio.json, for --validate")
    ap.add_argument("--json", dest="out", type=Path, help="write the full result here")
    ap.add_argument("--validate", action="store_true", help="run the reproduction check first")
    ap.add_argument("--target-r", type=float, default=2.0)
    args = ap.parse_args(argv)

    s = Settings()
    store = Store(args.store)
    days = collected_dates(store)
    print(f"store={args.store} days={len(days)} ({days[0]} → {days[-1]})")

    bull_by_day = {d: extract_day_trades(store, s, d) for d in days}

    if args.validate:
        if args.payload is None:
            print("--validate needs --payload <portfolio.json>")
            return 2
        if not validate(bull_by_day, s, args.payload):
            return 1

    result: dict[str, Any] = {
        "days": [d.isoformat() for d in days],
        "target_r": args.target_r,
        "universe": (
            "symbols on the scanner strictly before the trigger time; applied at extraction, "
            "never relaxed — each ORB length carries its own cutoff"
        ),
    }

    # Primary arm.
    od_by_day = {d: extract_open_candidates(store, s, d, orb=PRIMARY) for d in days}
    n_cand = sum(len(v) for v in od_by_day.values())
    n_days = sum(1 for v in od_by_day.values() if v)
    print(f"\n{PRIMARY.label}: {n_cand} candidates on {n_days} days")

    base_res = run_strategy(od_by_day, s, thresholds=Thresholds(), target_r=args.target_r)
    print(f"  1-trade/day book @ {args.target_r}R: {summarise(base_res)}")

    # Ungated: same universe and geometry, gates carried as features rather than applied.
    # The contrast table needs this — see `extract_open_candidates(apply_gates=...)`.
    ungated_by_day = {
        d: extract_open_candidates(store, s, d, orb=PRIMARY, apply_gates=False) for d in days
    }
    n_ungated = sum(len(v) for v in ungated_by_day.values())
    print(f"  ungated triggered population: {n_ungated}")

    result["primary"] = {
        "orb": PRIMARY.label,
        "n_candidates": n_cand,
        "n_ungated_triggered": n_ungated,
        "book": summarise(base_res),
        "trades": [
            {
                "date": r.day.isoformat(),
                "symbol": r.symbol,
                "r": round(r.realized_r, 3),
                "reason": r.reason,
            }
            for r in base_res
        ],
        "contrasts": contrast_table(ungated_by_day, s, target_r=args.target_r),
        "thresholds": fit_thresholds(od_by_day, s, target_r=args.target_r),
    }

    # Secondary arm — is 10 minutes the right range?
    print("\n--- secondary arm: ORB length (different populations by construction) ---")
    lengths: dict[str, Any] = {}
    for orb in ORB_LENGTHS:
        by_day = {d: extract_open_candidates(store, s, d, orb=orb) for d in days}
        res = run_strategy(by_day, s, thresholds=Thresholds(), target_r=args.target_r)
        stats = summarise(res)
        lengths[orb.label] = {
            "range_min": orb.range_min,
            "cons_min": orb.cons_min,
            "trigger_from": orb.trigger_from.isoformat(),
            "universe_cutoff": orb.trigger_from.isoformat(),
            "n_candidates": sum(len(v) for v in by_day.values()),
            **stats,
        }
        nc = lengths[orb.label]["n_candidates"]
        print(f"  {orb.label:9s} trigger>={orb.trigger_from} cands={nc:4d} {stats}")
    result["orb_lengths"] = lengths
    result["orb_lengths_note"] = (
        "Each length sits on its own tradable population — a 09:50 trigger legitimately admits "
        "symbols the scanner surfaced at 09:47 — so a longer range showing a larger n is expected "
        "and is not evidence it is better."
    )

    # Combined book.
    print("\n--- combined book ---")
    result["combined_book"] = combined_book(bull_by_day, od_by_day, s, thresholds=Thresholds())
    for book in ("adaptive", "fixed_2r"):
        print(f"  [{book}]")
        for name, stats in result["combined_book"][book].items():
            print(f"    {name:24s} {stats}")
    print("  [open-drive-alone sizing sweep @2R]")
    for row in result["combined_book"]["open_drive_sizing"]:
        print(
            f"    risk={row['risk_fraction']:.2f} pos={row['position_fraction']:.2f} "
            f"cap_bound={row['cap_bound']:2d}/{row['n_trades']} "
            f"avg_risk_pct={row['avg_risk_pct']} equity={row['end_equity']:8.2f} "
            f"ret={row['return_pct']:+.3f} dd={row['max_drawdown_pct']:.3f}"
        )

    if args.out:
        args.out.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
