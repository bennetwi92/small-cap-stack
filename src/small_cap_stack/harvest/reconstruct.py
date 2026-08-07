"""Bars → a scanner appearance: the pure half of the harvest (#431, promoted from spike #428).

Nobody sells historical scanner output — IBKR doesn't archive it. But the three hard scan gates are
all price-derived (``scan_min_price``/``scan_max_price``, ``scan_change_pct``,
``scan_min_5m_volume``; float and news are *collected*, never gated), so an appearance is
reconstructible from OHLCV alone. This module is that reconstruction plus the bar-grid arithmetic
around it, kept **vendor-agnostic**: it takes :class:`~small_cap_stack.capture.Bar` and knows
nothing about where the bars came from.

It lived in ``spikes/scanner_reconstruct.py`` + ``spikes/massive_replay.py`` while #428 was
measuring whether the idea works at all. It is here now because #431 makes it a *producer* the
paper book reads through (#430): the spikes are exempt from mypy and the test suite, and a nightly
job that writes 500 sessions into the store is not. The spikes import it back, so the calibration
that justifies the harvest and the harvest itself are the same code — if these functions drift,
#428's measured numbers stop describing what the box actually writes.

## The modelling, and where it is knowingly wrong

1. **Appearance time = the bar's END, not its start.** The scanner can only surface a symbol once
   trailing-5-min volume actually clears the floor, and a bar's volume is only known when it
   closes. ``hit_time = bar.start + interval``.
2. **The live scanner's window is ROLLING; ours is bar-aligned.** IBKR's ``stVolume5minAbove`` is a
   continuously-updated trailing 5 minutes, so the true crossing can happen mid-bar.
   Reconstructing off 5-min bars is therefore late by 0–5 min; off 1-min bars — which is why the
   harvest fetches minute data and aggregates *afterwards* — late by 0–1 min. #428 measured that
   difference as a median −0.34 min (minute) vs +3.16 min (5-min) against the live tracker.
3. **The change gate needs the previous daily close**, which one day's bars do not carry. Where it
   is unknown the gate *abstains* rather than fails — and without it the reconstruction fires a
   median 18 min early (#428), which is why the harvest pulls grouped-daily first and treats the
   previous close as a required input rather than a nicety.
4. **The 50-row rank cap is not modelled — and measurement says it need not be (#460).** The live
   scanner shows at most 50 names, so in principle a name passing every gate waits until it ranks.
   Across 20 live days that cap has **never bound**: the busiest single tick returned 45 symbols,
   the highest ``rank`` ever recorded is 44, and in the **pre-market window this module actually
   covers** the peak is **11 of 50**. Twenty opportunities spread over 5.5 hours is not twenty
   simultaneously on the scanner.
   This retracts the claim that stood here — that #428 had *proved* the cap load-bearing via SNDQ.
   With ~11 names on the pre-market scanner there was no queue for it to be stuck behind between
   04:27 and 08:35. ``portfolio/models.py`` had it right: "unreproducible per-symbol".
   The reconstructed universe does still differ from the live one, but through appearance *timing*
   rather than scanner *capacity* — the leading candidate being the change-percent reference price
   (#433: the vendor's daily close carries post-market prints IBKR's does not). That is still why
   #430 keeps reconstructed days in their own store and stamps every trade with ``source``: the
   reason changed, the decision did not.
5. **Price = the bar's close** — the scanner filters on last price, which at a bar boundary is the
   close.

Everything here is a pure function of bars + ``Settings``, so the methodology stays replayable:
the harvest stores the raw minute bars it reconstructed from, and a changed rule re-derives the
appearance without re-buying two years of data.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from ..capture import Bar, bar_interval
from ..clock import ET, within_window
from ..config import Settings

#: The pre-market session: 04:00 ET (the scan window opens) to the 09:30 bell. The paper book only
#: ever trades here (``select_window_start``/``_cutoff`` sit inside it), so restricting the
#: harvest's appearance search to it is not a filter over the strategy — it *is* its session.
PREMARKET: tuple[time, time] = (time(4, 0), time(9, 30))

# The gates the scanner itself applies, in the order they are reported. `float` and `news` are
# deliberately absent: they are collected downstream and never filter the IBKR scan (#428).
SCAN_GATES = ("price", "change_pct", "volume_5m", "trading_window")


# ------------------------------------------------------------------------------------------------
# Vendor rows → bars, and the bar grid
# ------------------------------------------------------------------------------------------------


def to_bars(rows: Iterable[dict[str, Any]]) -> list[Bar]:
    """Vendor aggregate rows → :class:`Bar`, sorted. ``t`` is epoch milliseconds, UTC."""
    bars = [
        Bar(
            start=datetime.fromtimestamp(int(r["t"]) / 1000, tz=UTC),
            open=float(r["o"]),
            high=float(r["h"]),
            low=float(r["l"]),
            close=float(r["c"]),
            volume=float(r.get("v") or 0.0),
        )
        for r in rows
    ]
    return sorted(bars, key=lambda b: b.start)


def bucket_start(moment: datetime, minutes: int) -> datetime:
    """Floor to the ``minutes`` grid **anchored on the ET hour** — what IBKR hands back.

    Anchoring to the first bar seen instead would silently shift every boundary on any symbol whose
    first print is not on a 5-min mark, so the reconstructed candles would not be the candles the
    live engine reads.
    """
    et = moment.astimezone(ET)
    floored = et.replace(minute=(et.minute // minutes) * minutes, second=0, microsecond=0)
    return floored.astimezone(UTC)


def aggregate(bars: Sequence[Bar], minutes: int = 5, *, fill: bool = True) -> list[Bar]:
    """Resample finer bars onto the ``minutes`` grid: first open, max high, min low, last close.

    Empty buckets **are** filled, with a flat zero-volume candle at the previous bucket's close,
    because that is what IBKR does and the engine is calibrated on IBKR's shape (#442). This was
    the other way round until it was measured: the docstring asserted "IBKR's historical bars omit
    periods with no trades", and all 25 committed real-market fixtures say otherwise — 3,428
    inter-bar gaps, every one exactly 5 minutes, and 331 of 3,453 bars (10%) zero-volume filler at
    the prior close.

    It matters because the engine is **bar-index-based**, not timestamp-based:
    ``bull_flag_max_cons`` and ``bull_flag_max_pole`` count candles, the trigger is the first bar
    whose high clears the
    previous *bar's*, and cycle exhaustion asks whether a prior cycle is within one *bar* of the
    pole. Deleting the quiet candles shortens every span the engine measures — a 7-candle
    consolidation the live engine rejects becomes a clean 2-candle flag, i.e. a trade that could not
    have been taken.

    Filling is **interior only**: from the first traded bucket to the last. The fixtures start at
    the first trade (ARCT opens 07:00, not 04:00) and none of the 25 ends on a zero-volume bar, so
    extrapolating a tail would invent bars IBKR does not send. A name whose tape dies at noon needs
    no tail anyway — ``simulate_exit`` marks an unresolved trade to the last close, and a flat
    filler carries that same close.

    ``fill=False`` keeps the old behaviour for callers that want the traded buckets alone.
    """
    if not bars:
        return []
    out: list[Bar] = []
    bucket: list[Bar] = []
    current = bucket_start(bars[0].start, minutes)
    for bar in bars:
        start = bucket_start(bar.start, minutes)
        if start != current and bucket:
            out.append(_fold(bucket, current))
            bucket = []
        current = start
        bucket.append(bar)
    if bucket:
        out.append(_fold(bucket, current))
    return _fill_gaps(out, minutes) if fill else out


def _fill_gaps(bars: Sequence[Bar], minutes: int) -> list[Bar]:
    """Insert flat zero-volume candles for every empty bucket between the first and the last.

    Anchored to the *previous* bar's close, which is what a no-trade period actually looks like on
    the tape: price did not move because nothing printed. Volume 0 is load-bearing — the trailing
    volume gate must not see invented liquidity, and the engine's own volume comparisons
    (pole peak > consolidation) stay honest.
    """
    if len(bars) < 2:
        return list(bars)
    step = timedelta(minutes=minutes)
    out: list[Bar] = [bars[0]]
    for bar in bars[1:]:
        prev = out[-1]
        gap = bar.start - prev.start
        # A vendor that omits no-trade periods leaves a gap that is a whole multiple of the grid.
        # Cap the run defensively: a malformed timestamp must not synthesise a million candles.
        missing = int(gap / step) - 1
        for i in range(1, min(missing, _MAX_FILL) + 1):
            out.append(
                Bar(
                    start=prev.start + step * i,
                    open=prev.close,
                    high=prev.close,
                    low=prev.close,
                    close=prev.close,
                    volume=0.0,
                )
            )
        out.append(bar)
    return out


#: One session on the 5-min grid is 144 bars, so this can never bind on well-formed data. It exists
#: so a bad vendor timestamp costs a log line rather than the box's memory.
_MAX_FILL = 1_000


def _fold(bucket: Sequence[Bar], start: datetime) -> Bar:
    return Bar(
        start=start,
        open=bucket[0].open,
        high=max(b.high for b in bucket),
        low=min(b.low for b in bucket),
        close=bucket[-1].close,
        volume=sum(b.volume for b in bucket),
    )


def trim_session(bars: Sequence[Bar], trading_date: date | None, lo: time, hi: time) -> list[Bar]:
    """Bars of ``trading_date`` whose START is in ``[lo, hi)`` ET.

    Half-open at the top so the boundary bar belongs to exactly one window: with ``hi=09:30`` the
    09:25 candle is the last pre-market one, and a bar starting at 09:30 is the regular session's.

    ``trading_date`` is checked because a vendor request for one day can hand back an adjacent
    session's overnight prints, and those would otherwise fold into this day's first bucket. Pass
    ``None`` where the caller already knows the bars are one session's (the #428 calibration reads
    single-day fixtures) — the time-of-day window still applies.
    """
    out = []
    for b in bars:
        et = b.start.astimezone(ET)
        if (trading_date is None or et.date() == trading_date) and lo <= et.time() < hi:
            out.append(b)
    return out


def rolling_window_volume(
    bars: Sequence[Bar], minutes: int = 5, *, interval: timedelta | None = None
) -> list[float]:
    """Trailing ``minutes`` of volume as at each bar's END, one value per bar.

    Generic over the bar grid on purpose (module doc, point 2): on 5-min bars each value is just
    that bar's own volume; on 1-min bars it is a true 5-bar rolling sum, the closest a replay gets
    to IBKR's continuously-updated ``stVolume5minAbove``. A bar contributes when its whole span lies
    inside the window ending at the current bar's close, so a gap in the tape shrinks the window's
    contents rather than reaching further back in time for them.

    ``interval`` is the bar duration. Pass it when the caller *knows* it (#442): inferring it costs
    nothing on a dense series but is wrong on a sparse one, and being wrong here is expensive —
    ``bar_interval`` returns the **modal** spacing, so a thin name printing every 3 minutes infers
    3 minutes, and at any inferred interval >= ``minutes`` the trailing window collapses to the
    current bar alone. It falls back to inference so the #428 spike fixtures still work unchanged.
    """
    window = timedelta(minutes=minutes)
    interval = interval or bar_interval(bars)
    out: list[float] = []
    start = 0
    for i, bar in enumerate(bars):
        end = bar.start + interval
        while start < i and bars[start].start < end - window:
            start += 1
        out.append(sum(b.volume for b in bars[start : i + 1]))
    return out


# ------------------------------------------------------------------------------------------------
# Reconstruction
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GateTrace:
    """One bar's worth of scan-gate evaluation — the audit trail behind an appearance time."""

    idx: int
    hit_time: datetime  # the bar's END: the earliest instant this bar's volume is knowable
    price: float  # bar close
    change_pct: float | None  # None when the previous daily close is unknown
    volume_5m: float
    price_ok: bool
    change_ok: bool | None
    volume_ok: bool
    window_ok: bool

    @property
    def all_ok(self) -> bool:
        """Every *decidable* gate passes. An undecidable change gate abstains, it does not fail."""
        return self.price_ok and self.volume_ok and self.window_ok and self.change_ok is not False


@dataclass(frozen=True)
class Reconstruction:
    """What a bars-only scanner would have surfaced for one symbol-day."""

    symbol: str
    trading_date: date
    hit_idx: int | None
    hit_time: datetime | None
    binding_gate: str | None  # the last gate to come true — what actually delayed the appearance
    first_pass: dict[str, int | None]  # per-gate, the first bar index at which it passes
    change_decidable: bool
    n_bars: int
    trace: tuple[GateTrace, ...]

    @property
    def found(self) -> bool:
        return self.hit_idx is not None

    @property
    def hit_times(self) -> tuple[datetime, ...]:
        """**Every** bar-end at which the scan gates all pass, not just the first.

        This is what the harvest writes as ``scanner_hits``, and it is load-bearing rather than
        completeness for its own sake: ``report.symbol_runs`` segments a symbol's day into runs by
        looking for a gap of ``reentry_gap_min`` with no hits (#36). Storing only the first
        appearance would collapse every pre-market pop-fade-pop into a single run and measure the
        second pop's setup against the first one's appearance time. Live writes a hit per scan tick
        (60 s) for as long as the name is on the scanner; a minute-bar reconstruction has the same
        cadence, which is why the appearance is reconstructed on minute bars.
        """
        return tuple(t.hit_time for t in self.trace if t.all_ok)


def _gate_trace(
    bars: Sequence[Bar],
    settings: Settings,
    *,
    prev_close: float | None,
    window_minutes: int,
    interval: timedelta | None = None,
) -> list[GateTrace]:
    interval = interval or bar_interval(bars)
    vols = rolling_window_volume(bars, minutes=window_minutes, interval=interval)
    traces: list[GateTrace] = []
    for i, bar in enumerate(bars):
        end = bar.start + interval
        # Spelled out rather than `prev_close in (None, 0)`: that form leans on __eq__ to do the
        # narrowing and newer mypy declines to follow it. Same meaning — a zero close is as
        # undecidable as a missing one, and dividing by it would be worse than abstaining.
        change = (
            None
            if prev_close is None or prev_close == 0
            else (bar.close / prev_close - 1.0) * 100.0
        )
        traces.append(
            GateTrace(
                idx=i,
                hit_time=end,
                price=bar.close,
                change_pct=change,
                volume_5m=vols[i],
                price_ok=settings.scan_min_price <= bar.close <= settings.scan_max_price,
                change_ok=None if change is None else change > settings.scan_change_pct,
                volume_ok=vols[i] > settings.scan_min_5m_volume,
                # The window is tested at the appearance instant, matching the live scanner's
                # own window (`scan_start`/`scan_end`). The `trading_window_gate` this once
                # pointed at was unused scaffolding, deleted in #517.
                window_ok=within_window(end.astimezone(ET), settings.scan_start, settings.scan_end),
            )
        )
    return traces


def reconstruct_hit(
    bars: Sequence[Bar],
    settings: Settings,
    *,
    symbol: str = "",
    trading_date: date | None = None,
    prev_close: float | None = None,
    window_minutes: int = 5,
    interval: timedelta | None = None,
) -> Reconstruction:
    """The first bar at which every decidable scan gate passes — the reconstructed appearance.

    ``prev_close`` is the previous session's daily close; without it the change gate abstains (see
    the module doc, point 3). ``window_minutes`` is the trailing volume window (5, matching
    ``stVolume5minAbove``) — a parameter only so the sensitivity can be swept, not tuned.

    ``interval`` is the bar duration, which the harvest knows (it asked the vendor for minute bars)
    and should pass rather than have re-derived (#442) — see :func:`rolling_window_volume`. It sets
    ``hit_time = bar.start + interval``, so an inferred-too-long interval credits every appearance
    late, and `first_hit` is what gates the engine's entry bar and its staleness bound.
    """
    trace = _gate_trace(
        bars,
        settings,
        prev_close=prev_close,
        window_minutes=window_minutes,
        interval=interval,
    )
    hit = next((t for t in trace if t.all_ok), None)
    flags = {
        "price": [t.price_ok for t in trace],
        "change_pct": [t.change_ok is not False for t in trace],
        "volume_5m": [t.volume_ok for t in trace],
        "trading_window": [t.window_ok for t in trace],
    }
    first_pass: dict[str, int | None] = {
        name: next((i for i, ok in enumerate(oks) if ok), None) for name, oks in flags.items()
    }
    # The binding gate is the one whose first *sustained-to-the-hit* pass lands latest: with the hit
    # bar in hand, ask which gates were still false on the bar before it. That is the honest answer
    # to "what delayed the appearance", and it is what decides whether the missing change gate
    # matters (module doc, point 3).
    binding: str | None = None
    if hit is not None:
        prior = trace[hit.idx - 1] if hit.idx > 0 else None
        if prior is None:
            binding = "none"  # gates were already true at the first bar we can see
        else:
            still_false = [
                name
                for name, ok in (
                    ("price", prior.price_ok),
                    ("change_pct", prior.change_ok is not False),
                    ("volume_5m", prior.volume_ok),
                    ("trading_window", prior.window_ok),
                )
                if not ok
            ]
            binding = "+".join(still_false) if still_false else "none"
    return Reconstruction(
        symbol=symbol,
        trading_date=trading_date or (bars[0].start.astimezone(ET).date() if bars else date.min),
        hit_idx=hit.idx if hit else None,
        hit_time=hit.hit_time if hit else None,
        binding_gate=binding,
        first_pass=first_pass,
        change_decidable=prev_close is not None,
        n_bars=len(bars),
        trace=tuple(trace),
    )
