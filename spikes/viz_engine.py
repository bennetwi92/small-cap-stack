"""Spike #182-prep — visualise how the engine-v2 detector categorises a single opportunity.

For one symbol/date/run, replay the v2 detector over the run's bars, pick the setup it would take
(first passing setup that triggers within the appearance/staleness window, else the first valid
one), and render a **standalone HTML** candlestick chart with the segmentation overlaid: pole bars,
consolidation bars, base/peak markers, entry/stop lines, the trigger bar, per-bar H/L/E tokens, and
a gate/score panel. Open it in a browser and iterate one opportunity at a time.

    python spikes/viz_engine.py --data-dir /tmp/scs-data --symbol VRAX --date 2026-07-09
    open data/spikes/viz_VRAX_2026-07-09.html

Runs anywhere the store is reachable (the box's /data, or a local copy). Reads only.
"""

# ruff: noqa: E501 — the CSS block and SVG-building f-strings below are naturally long single
# statements (a CSS ruleset per line, an HTML tag per append); wrapping them mid-declaration would
# be uglier and error-prone for zero benefit in this throwaway visualization spike.

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from small_cap_stack.bullflag import (
    Segment,
    Setup,
    evaluate,
    extract,
    score,
    tokenize,
    trailing_atr,
)
from small_cap_stack.bullflag.detect import _is_big_green, classify
from small_cap_stack.bullflag.gates import passed as gates_passed
from small_cap_stack.capture import Bar
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.report import day_chart_bars, day_opportunities, symbol_runs
from small_cap_stack.rmetrics import bar_interval
from small_cap_stack.storage import Store

_V2 = {"max_pole": 4, "max_cons": 4, "min_pole_pct": 0.02}
_EXHAUSTION_CAP = 2  # reject entry if this many complete pump/fade cycles already happened today


@dataclass(frozen=True)
class Cycle:
    """One pump/fade cycle in the PURE H/E/L token walk — deliberately looser than segment_at_end:
    no color/thrust rule, no dominant-peak search, no gates. A pole is any run of H; a consolidation
    is any run of L/E after it (any length, need not contain a strict L); the first H after a
    consolidation both ends it and starts the next cycle's pole. Used only to count how many times
    a name has already pumped-and-faded today, for the exhaustion cap (#182 review: FWDI) — entirely
    separate from the entry-detection pole (which does apply the color/thrust rule)."""

    pole_start: int  # base bar (the bar before the first H of this cycle's pole)
    peak: int  # last H bar of this cycle's pole
    cons_start: int | None  # first consolidation bar, None if this cycle is still mid-pole
    cons_end: int  # last consolidation bar so far (== peak if no consolidation has started yet)
    breakout: int | None  # the bar that both ends this cycle and starts the next pole; None if open


def segment_cycles(tokens: list[str]) -> list[Cycle]:
    """Walk the WHOLE day's tokens once, left to right, segmenting into consecutive cycles. The
    last cycle may be "open" (breakout is None) if the day ends mid-pole or mid-consolidation."""
    cycles: list[Cycle] = []
    state = "searching"  # searching -> pole -> cons -> pole (next cycle) -> ...
    pole_start = peak = cons_start = None
    for i, t in enumerate(tokens):
        if state == "searching":
            if t == "H":
                pole_start, peak, state = i, i + 1, "pole"
        elif state == "pole":
            if t == "H":
                peak = i + 1
            else:
                cons_start, state = i + 1, "cons"
        elif t == "H":  # state == "cons": the first H ends this cycle and starts the next pole
            assert pole_start is not None and peak is not None
            cycles.append(Cycle(pole_start, peak, cons_start, i, i + 1))
            pole_start, peak, cons_start, state = i, i + 1, None, "pole"
    if pole_start is not None and state != "searching":
        cycles.append(
            Cycle(pole_start, peak, cons_start, cons_start - 1 if cons_start else peak, None)
        )
    return cycles


def significant_cycles(bars: list[Bar], cycles: list[Cycle], min_volume: float) -> list[Cycle]:
    """Drop noise-level cycles that satisfy the loose H/E/L grammar but never had real participation
    — a cycle only counts toward exhaustion if ANY bar across its whole pole span clears min_volume.
    Checking only the FINAL peak bar undercounts multi-bar poles that front-load volume on the
    initial breakout bar and taper as they grind higher (FWDI: bar18's 09:30 breakout had 265,239
    volume, but the pole's last bar, bar21 at 09:45, had only 55,679 — checking just bar21 wrongly
    called this cycle insignificant). Height% alone doesn't work as a filter either: TVRD's early
    pre-market cycles moved 5-12% but on only hundreds/low-thousands of shares, while the real
    cycles (from market open on) cleared 100k+ (#182 review). min_volume reuses
    Settings.scan_min_5m_volume — the same trailing-5-min threshold the scanner itself requires to
    surface a name in the first place; a cycle that never cleared it wouldn't have been visible on
    the scanner either."""
    return [
        c
        for c in cycles
        if max(b.volume for b in bars[c.pole_start + 1 : c.peak + 1]) >= min_volume
    ]


def cycle_number_for(all_cycles: list[Cycle], sig_cycles: list[Cycle], peak_idx: int) -> int | None:
    """1-based cycle number for the target (the setup being evaluated): 1 + how many SIGNIFICANT
    cycles completed entirely before it. The target is located in the RAW (unfiltered) cycle list —
    it must count as itself regardless of whether ITS OWN peak clears the significance floor (FWDI:
    the target's peak volume was 97,227, just under the 100k floor, so it must never be silently
    "not found" — only PRIOR cycles need to pass the filter to count against it). Returns None if
    the target's peak matches no cycle at all (the color/thrust-gated entry pole and the pure-token
    cycle pole usually coincide, but can differ for a multi-bar pole with a red/doji intermediate)."""
    target = next((c for c in all_cycles if c.peak == peak_idx), None)
    if target is None:
        return None
    prior = sum(1 for c in sig_cycles if c.peak < target.pole_start)
    return prior + 1


def _params(settings: Settings) -> dict[str, object]:
    tick = settings.tick_size
    # entry_offset isn't read here — pick_setup always uses the validated +1 tick directly.
    return {**_V2, "eps": tick, "gate_window": False}


def _refine_pole(
    bars: list[Bar], tokens: list[str], peak: int, max_pole: int
) -> tuple[int, int] | None:
    """(base, pole_len): walk backward from a GIVEN peak through strict-H steps, extending only
    through genuine thrust candles (green, body >= half range, reusing detect._is_big_green) —
    the SAME color/thrust rule segment.py applies natively, but here anchored to whatever peak the
    GREEDY cycle walk found (see pick_setup) rather than a dominant-high search. Temporarily
    duplicated in the spike pending a core segment.py refactor to share this between the two
    peak-finding strategies (#182 review: DFDV — greedy peak-finding is being prototyped here first).
    None if the peak itself isn't green (a red/flat peak is disqualified)."""
    if peak - 1 < 0 or tokens[peak - 1] != "H" or classify(bars[peak]) != "green":
        return None
    base, pole_len = peak - 1, 1
    while (
        pole_len < max_pole
        and base - 1 >= 0
        and tokens[base - 1] == "H"
        and _is_big_green(bars[base])
    ):
        base -= 1
        pole_len += 1
    return base, pole_len


def pick_setup(
    day_bars: list[Bar],
    day_tokens: list[str],
    all_cycles: list[Cycle],
    settings: Settings,
    *,
    first_hit: datetime | None,
    params: dict[str, object],
) -> tuple[Setup | None, int | None, int | None]:
    """The setup the trader would take, per the entry rule: (setup, cons_end_idx, trigger_idx).

    Peak/consolidation/breakout boundaries come from the GREEDY H/E/L cycle walk (segment_cycles) —
    NOT a dominant-high search — because the dominant-high approach can skip right past a genuine
    "pole -> shallow pullback -> breakout of the last consolidation candle" pattern whenever that
    breakout doesn't exceed some earlier, taller candle in the trailing window (#182 review: DFDV,
    confirmed as a real gap, superseding the #163 dominant-peak heuristic for entry-detection). The
    pole itself is then refined with the SAME color/thrust rule as segment.py (_refine_pole).

    Cycles are walked in chronological order; the first one whose peak is visible (hadn't fully
    closed before first_hit, #99/#122) and whose peak is a genuine green thrust becomes the setup —
    mirroring the entry appearance gate's bar-close granularity. Entry trigger = last-consolidation-
    candle high + 1 tick; fill (slippage) = + 3 ticks; stop = consolidation low. Gates/score are
    evaluated honestly — an appearance-anchored pole can have a much deeper retracement than the
    "unseen" original, and may legitimately fail gates (e.g. cons_retracement).

    Returns (None, None, None) if no pole forms; trig_idx is None if it never breaks out or the
    breakout isn't takeable (past staleness).
    """
    tick = settings.tick_size
    max_pole = int(params.get("max_pole", 4))  # type: ignore[arg-type]
    max_cons = int(params.get("max_cons", 4))  # type: ignore[arg-type]  # used by the cons_len gate

    def _takeable(trig_idx: int) -> bool:
        """True if the ENTRY (breakout) bar STARTS at or after first_hit — i.e. we were already
        watching when it opened, so we could have caught the break. If first_hit falls INSIDE the
        entry bar ("seen in the entry bar"), we can't have taken it: the breakout may have printed
        before we ever saw the symbol (#182 review: MSTZ). Always True when first_hit is unknown."""
        return first_hit is None or day_bars[trig_idx].start >= first_hit

    # Each cycle contributes only its POLE/PEAK (greedy — fixes DFDV's dominant-peak skip). The
    # ENTRY is then found PRICE-BASED: the first bar after the peak whose high breaks the previous
    # bar's high by >= 1 tick. This is deliberately NOT the cycle's token breakout: the entry
    # trigger is a price level (last_cons_high + 1 tick), and an exactly-1-tick break is an `E`
    # token (within eps), so the token walk would run the consolidation right past it (#182 review:
    # FWDI's 10:50 entry is a 1-tick break -> token breakout wouldn't fire until 11:20, past
    # staleness). Appearance gates the ENTRY bar, not the peak: the pole/consolidation can form
    # before we saw the symbol; the entry bar must START at/after first_hit (if we were first seen
    # mid-entry-bar, the break within it may already be behind us). When a cycle's entry isn't
    # takeable, we move to the next cycle — its pole is the prior token breakout, so the re-anchored
    # entry lands later (#182 review: MSTZ — seen 09:03 inside the 09:00 entry bar -> that becomes
    # the pole -> real entry 09:15). Contrast DFDV: seen 09:45:10, entry bar opens 09:55 -> takeable.
    base = peak = pole_len = cons_end = trig = None
    for c in all_cycles:
        refined = _refine_pole(day_bars, day_tokens, c.peak, max_pole)
        if refined is None:
            continue  # peak isn't green -> disqualified, try the next cycle
        t = next(
            (
                j
                for j in range(c.peak + 1, len(day_bars))
                if day_bars[j].high >= day_bars[j - 1].high + tick
            ),
            None,
        )
        if t is None or t <= c.peak + 1:
            continue  # no consolidation (need >= 1 cons bar between peak and entry), or no entry
        if not _takeable(t):
            continue  # entry bar started before (or contains) first_hit -> couldn't have taken it
        base, pole_len = refined
        peak, cons_end, trig = c.peak, t - 1, t
        break
    if peak is None or base is None or pole_len is None or cons_end is None:
        return None, None, None

    seg = Segment(base, peak, cons_end, tuple(day_tokens[base:cons_end]), pole_len, cons_end - peak)
    fv = extract(day_bars, seg, atr=trailing_atr(day_bars, base, window=14))
    gates = evaluate(
        fv,
        max_pole=max_pole,
        max_cons=max_cons,
        max_peak_wick=float(params.get("max_peak_wick", 0.5)),  # type: ignore[arg-type]
        min_pole_pct=float(params.get("min_pole_pct", 0.02)),  # type: ignore[arg-type]
        max_retracement=float(params.get("max_retracement", 0.5)),  # type: ignore[arg-type]
    )
    sc, contrib = score(fv, max_pole=max_pole)
    last_high = day_bars[cons_end].high
    stop = min(b.low for b in day_bars[peak + 1 : cons_end + 1])
    setup = Setup(
        seg,
        fv,
        round(last_high + tick, 4),  # entry_trigger: +1 tick (mechanical, validated)
        round(last_high + 3 * tick, 4),  # entry_fill: +3 ticks (conservative R fill, confirmed)
        round(last_high, 4),
        round(stop, 4),
        gates,
        gates_passed(gates),
        sc,
        contrib,
    )

    # Staleness (#130): a break too long after appearance reads as faded. (The "closed before
    # appearance" half of the gate, #99/#122, is already enforced by min_peak_idx above — any bar
    # after a visible peak is necessarily itself visible.)
    if trig is not None and first_hit is not None:
        stale = timedelta(minutes=settings.entry_staleness_min)
        if day_bars[trig].start >= first_hit + stale:
            trig = None
    return setup, cons_end, trig


# --- rendering -------------------------------------------------------------------------------------

_STEP = 26
_MT, _MB, _ML, _MR = 56, 48, 62, 104
_PLOT_H = 720


def _et(b: Bar) -> str:
    return b.start.astimezone(ET).strftime("%H:%M")


def _svg(
    bars: list[Bar],
    tokens: list[str],
    setup: Setup | None,
    detect_idx: int | None,
    trig_idx: int | None,
    first_hit: datetime | None,
    cycles: list[Cycle],
) -> str:
    n = len(bars)
    width = _ML + n * _STEP + _MR
    height = _MT + _PLOT_H + _MB
    lo = min(b.low for b in bars)
    hi = max(b.high for b in bars)
    pad = (hi - lo) * 0.08 or 0.01
    top, bot = hi + pad, lo - pad

    def x(i: int) -> float:
        return _ML + i * _STEP + _STEP / 2

    def y(p: float) -> float:
        return _MT + (top - p) / (top - bot) * _PLOT_H

    out: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" class="chart">'
    ]

    # price gridlines + axis
    for k in range(6):
        p = top - (top - bot) * k / 5
        yy = y(p)
        out.append(
            f'<line class="grid" x1="{_ML}" y1="{yy:.1f}" x2="{width - _MR}" y2="{yy:.1f}"/>'
        )
        out.append(
            f'<text class="axis" x="{_ML - 8}" y="{yy + 3:.1f}" text-anchor="end">{p:.2f}</text>'
        )

    # Prior-cycle markers (#182 review: FWDI/TVRD) — the pure H/E/L cycle walk, muted/faint, drawn
    # UNDERNEATH the current setup's own bands. Filtered chronologically (peak strictly before the
    # target's own base) rather than by list position — cycles is the SIGNIFICANT list, which may
    # include cycles chronologically AFTER the target too (e.g. later in the day), and the target
    # itself may not appear in it at all if its own peak volume is borderline (FWDI: 97,227, just
    # under the 100k floor) — neither should affect which prior cycles get drawn.
    target_base = setup.segment.base_idx if setup is not None else None
    shown = 0
    for c in cycles:
        if target_base is not None and c.peak >= target_base:
            continue  # not chronologically before the target -> not a "prior" cycle
        shown += 1
        i = shown
        cx0 = x(c.pole_start + 1) - _STEP / 2
        cw = (c.cons_end - c.pole_start) * _STEP
        out.append(
            f'<rect class="band prior" x="{cx0:.1f}" y="{_MT}" width="{cw:.1f}" height="{_PLOT_H}"/>'
        )
        out.append(
            f'<text class="band-lbl prior" x="{cx0 + cw / 2:.1f}" y="{_MT + 30}" '
            f'text-anchor="middle">cycle {i}</text>'
        )

    # pole / consolidation bands. The pole band is the HIGHER-HIGH bars only (base+1..peak) — the
    # launch/base bar is the rise's reference low, not part of the pole, so it never shows an E.
    if setup is not None:
        seg = setup.segment
        px0 = x(seg.base_idx + 1) - _STEP / 2
        pw = (seg.peak_idx - seg.base_idx) * _STEP
        out.append(
            f'<rect class="band pole" x="{px0:.1f}" y="{_MT}" width="{pw:.1f}" height="{_PLOT_H}"/>'
        )
        out.append(
            f'<text class="band-lbl pole" x="{px0 + pw / 2:.1f}" y="{_MT + 14}" text-anchor="middle">POLE</text>'
        )
        cx0 = x(seg.peak_idx + 1) - _STEP / 2
        cw = (seg.cons_end_idx - seg.peak_idx) * _STEP
        out.append(
            f'<rect class="band cons" x="{cx0:.1f}" y="{_MT}" width="{cw:.1f}" height="{_PLOT_H}"/>'
        )
        out.append(
            f'<text class="band-lbl cons" x="{cx0 + cw / 2:.1f}" y="{_MT + 14}" text-anchor="middle">CONSOLIDATION</text>'
        )

    # candles + tokens
    tok_cls = {"H": "tok-h", "L": "tok-l", "E": "tok-e"}
    for i, b in enumerate(bars):
        cls = "up" if b.close >= b.open else "down"
        cx = x(i)
        out.append(
            f'<line class="wick {cls}" x1="{cx:.1f}" y1="{y(b.high):.1f}" x2="{cx:.1f}" y2="{y(b.low):.1f}"/>'
        )
        yo, yc = y(b.open), y(b.close)
        bh = max(abs(yo - yc), 1.0)
        out.append(
            f'<rect class="body {cls}" x="{cx - _STEP * 0.32:.1f}" y="{min(yo, yc):.1f}" '
            f'width="{_STEP * 0.64:.1f}" height="{bh:.1f}"><title>{_et(b)}  O{b.open} H{b.high} L{b.low} C{b.close}  vol{int(b.volume)}</title></rect>'
        )
        if i >= 1:  # token for the step into bar i
            t = tokens[i - 1]
            out.append(
                f'<text class="tok {tok_cls[t]}" x="{cx:.1f}" y="{_MT - 6}" text-anchor="middle">{t}</text>'
            )
        if i % max(1, n // 12) == 0:
            out.append(
                f'<text class="axis" x="{cx:.1f}" y="{height - _MB + 16}" text-anchor="middle">{_et(b)}</text>'
            )

    # base / peak markers, entry / stop lines, trigger
    if setup is not None:
        seg = setup.segment
        bx, px = x(seg.base_idx), x(seg.peak_idx)
        out.append(
            f'<text class="mark base" x="{bx:.1f}" y="{y(bars[seg.base_idx].low) + 18:.1f}" text-anchor="middle">▲ base</text>'
        )
        out.append(
            f'<text class="mark peak" x="{px:.1f}" y="{y(bars[seg.peak_idx].high) - 10:.1f}" text-anchor="middle">▼ peak</text>'
        )
        ye, ys = y(setup.entry_trigger), y(setup.stop)
        out.append(
            f'<line class="level entry" x1="{_ML}" y1="{ye:.1f}" x2="{width - _MR}" y2="{ye:.1f}"/>'
        )
        out.append(
            f'<text class="level-lbl entry" x="{width - _MR + 6}" y="{ye + 3:.1f}">entry {setup.entry_trigger}</text>'
        )
        out.append(
            f'<line class="level stop" x1="{_ML}" y1="{ys:.1f}" x2="{width - _MR}" y2="{ys:.1f}"/>'
        )
        out.append(
            f'<text class="level-lbl stop" x="{width - _MR + 6}" y="{ys + 3:.1f}">stop {setup.stop}</text>'
        )
        if trig_idx is not None:
            tx = x(trig_idx)
            out.append(
                f'<line class="trigger" x1="{tx:.1f}" y1="{_MT}" x2="{tx:.1f}" y2="{_MT + _PLOT_H}"/>'
            )
            out.append(
                f'<text class="mark trig" x="{tx:.1f}" y="{_MT + _PLOT_H - 6:.1f}" text-anchor="middle">entry hit</text>'
            )

    # "seen" = first scanner appearance (first_hit), which gates the entry (#99). It can fall
    # between bars, so interpolate its x within the bar it lands in.
    if first_hit is not None:
        secs = bar_interval(bars).total_seconds() or 300.0
        xs = None
        for i, b in enumerate(bars):
            nxt = bars[i + 1].start if i + 1 < len(bars) else b.start + timedelta(seconds=secs)
            if b.start <= first_hit < nxt:
                frac = max(0.0, min(1.0, (first_hit - b.start).total_seconds() / secs))
                xs = (x(i) - _STEP / 2) + frac * _STEP
                break
        if xs is None:
            xs = x(0) - _STEP / 2 if first_hit < bars[0].start else x(len(bars) - 1) + _STEP / 2
        out.append(
            f'<line class="seen" x1="{xs:.1f}" y1="{_MT}" x2="{xs:.1f}" y2="{_MT + _PLOT_H}"/>'
        )
        out.append(
            f'<text class="mark seen" x="{xs:.1f}" y="{_MT - 22}" text-anchor="middle">'
            f"seen {first_hit.astimezone(ET).strftime('%H:%M')}</text>"
        )
    out.append("</svg>")
    return "".join(out)


def _panel(
    symbol: str,
    seg_id: str,
    d: date,
    n: int,
    setup: Setup | None,
    trig_idx: int | None,
    cycle_num: int | None,
    exhausted: bool,
) -> str:
    if setup is None:
        return f'<header class="hdr"><b>{symbol}</b> · {d} · {seg_id} — no v2 setup ({n} bars)</header>'
    badge = "PASS" if setup.passed else "REJECT"
    trig = "triggered" if trig_idx is not None else "no trigger"
    cycle_badge = (
        f'<span class="badge {"exhausted" if exhausted else "cycle-ok"}">'
        f"cycle {cycle_num}{' EXHAUSTED' if exhausted else ''}</span>"
        if cycle_num is not None
        else ""
    )
    return (
        f'<header class="hdr"><b>{symbol}</b> · {d} · {seg_id}'
        f'<span class="badge {badge.lower()}">{badge}</span>'
        f'<span class="badge trig-{"y" if trig_idx is not None else "n"}">{trig}</span>'
        f"{cycle_badge}</header>"
    )


_CSS = """
:root{--bg:#0f1216;--fg:#e6e9ef;--mut:#8b93a1;--grid:#232833;--up:#26a269;--down:#e01b24;
--pole:#3584e4;--cons:#e5a50a;--entry:#26a269;--stop:#e01b24;--card:#161a21;}
@media(prefers-color-scheme:light){:root{--bg:#fbfcfd;--fg:#1b1f27;--mut:#5c6472;--grid:#e6e9ef;
--card:#ffffff;--pole:#1a5fb4;--cons:#b5820a;}}
:root[data-theme=dark]{--bg:#0f1216;--fg:#e6e9ef;--mut:#8b93a1;--grid:#232833;--card:#161a21;}
:root[data-theme=light]{--bg:#fbfcfd;--fg:#1b1f27;--mut:#5c6472;--grid:#e6e9ef;--card:#fff;}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{height:100vh;display:flex;flex-direction:column;padding:12px;gap:10px}
.hdr{background:var(--card);border:1px solid var(--grid);border-radius:10px;padding:8px 14px;margin:0;flex:0 0 auto;display:flex;align-items:center;gap:10px;font-size:14px}
h1{font-size:18px;margin:0 0 8px}.badges{display:flex;gap:10px;align-items:center;margin-bottom:12px}
.badge{font-size:12px;font-weight:600;padding:2px 9px;border-radius:20px}
.badge.pass{background:rgba(38,162,105,.18);color:var(--up)}.badge.reject{background:rgba(224,27,36,.18);color:var(--down)}
.badge.trig-y{background:rgba(53,132,228,.18);color:var(--pole)}.badge.trig-n{background:var(--grid);color:var(--mut)}
.badge.exhausted{background:rgba(224,27,36,.18);color:var(--down)}.badge.cycle-ok{background:rgba(38,162,105,.18);color:var(--up)}
.score{color:var(--mut);font-size:13px}
.verdict.none{color:var(--mut)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 14px;margin:0 0 12px}.kv dt{color:var(--mut)}.kv dd{margin:0}
table.gates{border-collapse:collapse;font-size:13px;width:100%;max-width:360px}
.gates td,.gates th{padding:3px 10px 3px 0;text-align:left}.gates th{color:var(--mut);font-weight:500;border-bottom:1px solid var(--grid)}
.gates tr.no td{color:var(--down)}.gates tr.ok td:nth-child(2){color:var(--up)}
.chart-wrap{flex:1 1 auto;min-height:0;overflow:auto;background:var(--card);border:1px solid var(--grid);border-radius:12px;padding:8px}
svg.chart{display:block}
.grid{stroke:var(--grid);stroke-width:1}.axis{fill:var(--mut);font-size:10px}
.wick{stroke-width:1.4}.wick.up,.body.up{stroke:var(--up)}.wick.down,.body.down{stroke:var(--down)}
.body.up{fill:var(--up)}.body.down{fill:var(--down)}.body{stroke-width:1}
.band{opacity:.12}.band.pole{fill:var(--pole)}.band.cons{fill:var(--cons)}.band.prior{fill:var(--mut);opacity:.10}
.band-lbl{font-size:10px;font-weight:700;letter-spacing:.08em;opacity:.8}.band-lbl.pole{fill:var(--pole)}.band-lbl.cons{fill:var(--cons)}.band-lbl.prior{fill:var(--mut);opacity:.7;font-weight:600}
.tok{font-size:10px;font-weight:700}.tok-h{fill:var(--up)}.tok-l{fill:var(--down)}.tok-e{fill:var(--mut)}
.level{stroke-dasharray:4 4;stroke-width:1.3}.level.entry{stroke:var(--entry)}.level.stop{stroke:var(--stop)}
.level-lbl{font-size:10px;font-weight:600}.level-lbl.entry{fill:var(--entry)}.level-lbl.stop{fill:var(--stop)}
.mark{font-size:10px;font-weight:700}.mark.base{fill:var(--pole)}.mark.peak{fill:var(--cons)}.mark.trig{fill:var(--pole)}
.trigger{stroke:var(--pole);stroke-width:1.3;stroke-dasharray:2 3}
.seen{stroke:#c061cb;stroke-width:1.8;stroke-dasharray:7 3}.mark.seen{fill:#c061cb}
.legend{color:var(--mut);font-size:12px;margin:0;flex:0 0 auto;display:flex;gap:16px;flex-wrap:wrap}
.legend b{color:var(--fg);font-weight:600}
"""


def render(
    symbol: str,
    seg_id: str,
    d: date,
    bars: list[Bar],
    tokens: list[str],
    setup: Setup | None,
    detect_idx: int | None,
    trig_idx: int | None,
    first_hit: datetime | None,
    cycles: list[Cycle],
    cycle_num: int | None,
    exhausted: bool,
) -> str:
    body = (
        f'<div class="wrap">{_panel(symbol, seg_id, d, len(bars), setup, trig_idx, cycle_num, exhausted)}'
        f'<div class="chart-wrap">{_svg(bars, tokens, setup, detect_idx, trig_idx, first_hit, cycles)}</div>'
        '<div class="legend"><span><b>H/L/E</b> higher / lower / equal high (per bar)</span>'
        '<span><b class="pole">blue</b> pole</span><span><b class="cons">amber</b> consolidation</span>'
        '<span><b style="color:var(--mut)">grey</b> prior cycle</span>'
        "<span>dashed = entry (green) / stop (red)</span>"
        '<span><b style="color:#c061cb">violet</b> seen (first scan)</span></div></div>'
    )
    return (
        f'<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{symbol} {d} — engine v2</title><style>{_CSS}</style></head><body>{body}</body></html>"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="/data")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--date", type=date.fromisoformat, required=True)
    ap.add_argument("--run", type=int, default=1, help="1-based run index within the day")
    ap.add_argument("--out", default="data/spikes")
    args = ap.parse_args()

    settings = Settings()
    store = Store(Path(args.data_dir))
    bars_df, scans = store.read("bars"), store.read("scanner_hits")
    opps = day_opportunities(store, args.date)
    row = next((r for r in opps.iter_rows(named=True) if r["symbol"] == args.symbol), None)
    if row is None:
        raise SystemExit(f"no opportunity for {args.symbol} on {args.date}")
    runs = symbol_runs(row, bars_df, scans, settings)
    run = next((r for r in runs if r.idx == args.run), None)
    if run is None or not run.bars:
        raise SystemExit(f"run {args.run} has no bars for {args.symbol} {args.date}")

    # Detection and rendering both operate directly on the full trading-day window (04:00-16:00 ET,
    # the same bars the real review workbench charts, via day_chart_bars) — no more run-space vs
    # day-space remapping, since pick_setup's greedy cycle walk (#182 review: DFDV) needs the whole
    # day anyway to find pole/cons/breakout boundaries correctly.
    day_bars = day_chart_bars(bars_df, row["opportunity_id"], settings)
    day_tokens = tokenize(day_bars, eps=settings.tick_size)
    all_cycles = segment_cycles(day_tokens)

    setup, _cons_end_idx, trig_idx = pick_setup(
        day_bars,
        day_tokens,
        all_cycles,
        settings,
        first_hit=run.first_hit,
        params=_params(settings),
    )

    # Exhaustion (#182 review: FWDI/TVRD) — a SEPARATE, looser pass over the whole day (pure H/E/L,
    # no color/gates) counting complete pump/fade cycles, then dropping ones whose peak never
    # cleared real scanner-level volume. Reject entry if _EXHAUSTION_CAP+ SIGNIFICANT cycles already
    # completed before this one's pole — the "easy" move is spent by the 3rd+ repeat. The target is
    # located in the RAW cycle list (cycle_number_for) so it's always found even if its own peak
    # volume is borderline (FWDI: 97,227, just under the 100k floor) — only PRIOR cycles need to
    # clear the floor to count against it.
    cycles = significant_cycles(day_bars, all_cycles, min_volume=settings.scan_min_5m_volume)
    cycle_num = (
        cycle_number_for(all_cycles, cycles, setup.segment.peak_idx) if setup is not None else None
    )
    exhausted = cycle_num is not None and cycle_num > _EXHAUSTION_CAP

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"viz_{args.symbol}_{args.date}.html"
    path.write_text(
        render(
            args.symbol,
            run.seg_id,
            args.date,
            day_bars,
            day_tokens,
            setup,
            None,
            trig_idx,
            run.first_hit,
            cycles,
            cycle_num,
            exhausted,
        )
    )
    verdict = (
        "no setup"
        if setup is None
        else f"{'PASS' if setup.passed else 'REJECT'}, {'triggered' if trig_idx is not None else 'no trigger'}"
    )
    cycle_note = (
        f", cycle {cycle_num}/{len(cycles)}{' EXHAUSTED' if exhausted else ''}" if cycle_num else ""
    )
    print(
        f"{args.symbol} {args.date} run {args.run}: {len(day_bars)} bars (full day) — {verdict}{cycle_note}\n"
        f"wrote {path}"
    )


if __name__ == "__main__":
    main()
