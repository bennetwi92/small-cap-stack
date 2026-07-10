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
from datetime import date, datetime, timedelta
from pathlib import Path

from small_cap_stack.bullflag import (
    Segment,
    Setup,
    evaluate,
    extract,
    score,
    segment_at_end,
    tokenize,
    trailing_atr,
)
from small_cap_stack.bullflag.gates import passed as gates_passed
from small_cap_stack.capture import Bar
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.report import day_opportunities, symbol_runs
from small_cap_stack.rmetrics import bar_interval
from small_cap_stack.storage import Store

_V2 = {"max_pole": 4, "max_cons": 4, "min_pole_pct": 0.02}
_THRUST_MIN_BODY_FRAC = 0.5  # a candle needs body >= this fraction of its range to count as thrust


def _is_green(bar: Bar) -> bool:
    return bar.close > bar.open


def _is_thrust(bar: Bar, threshold: float = _THRUST_MIN_BODY_FRAC) -> bool:
    """A decisive/thrust candle: green (close > open) with body >= threshold of its range. A
    technically-higher-high bar that's doji-like (small body relative to range) is a quiet pause,
    not a real continuation of momentum, even though its high still ticks up — spotted on MUZ
    (#182 review). A red candle never counts as thrust regardless of body size (see _pole_base_len)."""
    if not _is_green(bar):
        return False
    rng = bar.high - bar.low
    return rng > 0 and (bar.close - bar.open) / rng >= threshold


def _pole_base_len(
    bars: list[Bar], tokens: list[str], peak: int, max_pole: int
) -> tuple[int, int] | None:
    """(base, pole_len): walk backward from the peak through strict-H steps, but only extend PAST
    a bar if that bar is itself a thrust candle — a quiet/doji bar breaks the run and becomes the
    base instead of an intermediate pole bar, even though the step into it was technically H.

    No red candle can be part of the pole, including the peak itself: a red "peak" (a new high
    that reverses and closes weak within the same bar, e.g. IRE's shooting-star top, #182 review)
    isn't a genuine thrust — it's disqualified here and the caller keeps searching later prefixes
    for a green peak instead.
    """
    if peak - 1 < 0 or tokens[peak - 1] != "H" or not _is_green(bars[peak]):
        return None  # no strict higher high into the peak, or the peak itself is red -> no pole
    base, pole_len = peak - 1, 1
    while (
        pole_len < max_pole and base - 1 >= 0 and tokens[base - 1] == "H" and _is_thrust(bars[base])
    ):
        base -= 1
        pole_len += 1
    return base, pole_len


def _params(settings: Settings) -> dict[str, object]:
    tick = settings.tick_size
    # entry_offset isn't read here — pick_setup always uses the validated +1 tick directly.
    return {**_V2, "eps": tick, "gate_window": False}


def pick_setup(
    bars: list[Bar], settings: Settings, *, first_hit: datetime | None, params: dict[str, object]
) -> tuple[Setup | None, int | None, int | None]:
    """The setup the trader would take, per the entry rule: (setup, cons_end_idx, trigger_idx).

    1. Find a valid pole + peak: the earliest STRUCTURAL segment (base/peak/pole_len — not yet
       gate-checked) whose peak is something we could actually have seen. A pole whose peak bar had
       already fully closed before the symbol's first scanner appearance isn't observable, so it
       can't be "the" pole for execution purposes — the earliest visible peak becomes the reference
       thrust instead (its base/launch bar may still be earlier; that's just a height reference, not
       something we needed to act on). Mirrors the entry appearance gate's bar-close granularity
       (#122): a peak bar counts as visible if it hadn't fully closed by first_hit.
    2. From that peak, the CONSOLIDATION is the run of candles making lower-or-equal highs; the
       ENTRY is the first candle that breaks the previous candle's high by 1 tick (a higher high).
       Entry trigger = last-consolidation-candle high + 1 tick; fill (slippage) = + 3 ticks; stop =
       consolidation low. Gates/score are evaluated honestly on this consolidation — an
       appearance-anchored pole can have a much deeper retracement than the "unseen" original, and
       may legitimately fail gates (e.g. cons_retracement).
    Returns (None, None, None) if no pole forms; trig_idx is None if it never breaks out or the
    breakout isn't takeable (past staleness).
    """
    tick = settings.tick_size
    max_pole = int(params.get("max_pole", 4))  # type: ignore[arg-type]
    max_cons = int(params.get("max_cons", 4))  # type: ignore[arg-type]
    min_peak_idx = 0
    if first_hit is not None:
        interval = bar_interval(bars)
        min_peak_idx = next(
            (i for i, b in enumerate(bars) if b.start + interval > first_hit), len(bars)
        )

    # Peak selection reuses segment_at_end (the #163 dominant-high search); base/pole_len are then
    # RE-derived via the thrust-aware walk (_pole_base_len) rather than trusted from the segment —
    # a quiet/doji bar the segmenter would still count as a strict-H pole step gets excluded here.
    base = peak = pole_len = None
    for i in range(1, len(bars)):
        toks = tokenize(bars[: i + 1], eps=tick)
        seg = segment_at_end(bars[: i + 1], toks, max_pole=max_pole, max_cons=max_cons)
        if seg is not None and seg.peak_idx >= min_peak_idx:
            refined = _pole_base_len(bars, toks, seg.peak_idx, max_pole)
            if refined is None:
                continue  # the dominant-high peak is red (a reversal, not a genuine thrust) ->
                # keep scanning later prefixes for a green one instead of accepting this candidate
            peak = seg.peak_idx
            base, pole_len = refined
            break
    if peak is None or base is None or pole_len is None:
        return None, None, None

    # Forward-scan: consolidation = lower/equal highs after the peak; entry = first higher high.
    trig = next(
        (j for j in range(peak + 1, len(bars)) if bars[j].high >= bars[j - 1].high + tick), None
    )
    cons_end = (trig - 1) if trig is not None else len(bars) - 1
    if cons_end <= peak:
        return None, None, None  # no consolidation formed (immediate new high)

    toks = tokenize(bars[: cons_end + 1], eps=tick)
    seg = Segment(base, peak, cons_end, tuple(toks[base:]), pole_len, cons_end - peak)
    fv = extract(bars, seg, atr=trailing_atr(bars, base, window=14))
    gates = evaluate(
        fv,
        max_pole=max_pole,
        max_cons=max_cons,
        max_peak_wick=float(params.get("max_peak_wick", 0.5)),  # type: ignore[arg-type]
        min_pole_pct=float(params.get("min_pole_pct", 0.02)),  # type: ignore[arg-type]
        max_retracement=float(params.get("max_retracement", 0.5)),  # type: ignore[arg-type]
    )
    sc, contrib = score(fv, max_pole=max_pole)
    last_high = bars[cons_end].high
    stop = min(b.low for b in bars[peak + 1 : cons_end + 1])
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
        if bars[trig].start >= first_hit + stale:
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
    symbol: str, seg_id: str, d: date, n: int, setup: Setup | None, trig_idx: int | None
) -> str:
    if setup is None:
        return f'<header class="hdr"><b>{symbol}</b> · {d} · {seg_id} — no v2 setup ({n} bars)</header>'
    badge = "PASS" if setup.passed else "REJECT"
    trig = "triggered" if trig_idx is not None else "no trigger"
    return (
        f'<header class="hdr"><b>{symbol}</b> · {d} · {seg_id}'
        f'<span class="badge {badge.lower()}">{badge}</span>'
        f'<span class="badge trig-{"y" if trig_idx is not None else "n"}">{trig}</span></header>'
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
.band{opacity:.12}.band.pole{fill:var(--pole)}.band.cons{fill:var(--cons)}
.band-lbl{font-size:10px;font-weight:700;letter-spacing:.08em;opacity:.8}.band-lbl.pole{fill:var(--pole)}.band-lbl.cons{fill:var(--cons)}
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
) -> str:
    body = (
        f'<div class="wrap">{_panel(symbol, seg_id, d, len(bars), setup, trig_idx)}'
        f'<div class="chart-wrap">{_svg(bars, tokens, setup, detect_idx, trig_idx, first_hit)}</div>'
        '<div class="legend"><span><b>H/L/E</b> higher / lower / equal high (per bar)</span>'
        '<span><b class="pole">blue</b> pole</span><span><b class="cons">amber</b> consolidation</span>'
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

    tokens = tokenize(run.bars, eps=settings.tick_size)
    setup, detect_idx, trig_idx = pick_setup(
        run.bars, settings, first_hit=run.first_hit, params=_params(settings)
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"viz_{args.symbol}_{args.date}.html"
    path.write_text(
        render(
            args.symbol,
            run.seg_id,
            args.date,
            run.bars,
            tokens,
            setup,
            detect_idx,
            trig_idx,
            run.first_hit,
        )
    )
    verdict = (
        "no setup"
        if setup is None
        else f"{'PASS' if setup.passed else 'REJECT'}, {'triggered' if trig_idx is not None else 'no trigger'}"
    )
    print(
        f"{args.symbol} {args.date} run {args.run}: {len(run.bars)} bars — {verdict}\nwrote {path}"
    )


if __name__ == "__main__":
    main()
