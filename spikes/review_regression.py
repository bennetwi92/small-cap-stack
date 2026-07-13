"""Spike #182 — regression harness for the per-opportunity visual review cases.

Every opportunity the trader has walked through and confirmed (pole/consolidation/entry/exhaustion)
is pinned here as a committed fixture: the day's bars PLUS the expected engine outcome. The checker
re-runs the current engine (viz_engine.pick_setup + the cycle/exhaustion logic) over each fixture
and asserts it still matches — so as we keep changing the rules we can prove we didn't silently
regress a case the trader already signed off on.

    python spikes/review_regression.py                 # CHECK: assert every fixture still matches
    python spikes/review_regression.py --extract       # REBUILD fixtures from a live /data snapshot

Check mode reads only the committed fixtures (spikes/review_fixtures/*.json) — no box/`/data` needed,
so it runs anywhere and survives a reboot. Extract mode needs the data snapshot (--data-dir) and is
how you (re)generate a fixture: run it, eyeball the viz, and if the trader confirms the new outcome,
commit the regenerated fixture (that's the intentional "update the golden value" step).

Spike-side for now (the greedy-walk / appearance / cycle / exhaustion rules it covers still live in
viz_engine.py); graduates into tests/ with committed fixtures once those rules land in the core
engine (bullflag package). Fixtures are tiny curated OHLCV slices, distinct from the runtime dataset
the "never commit data" rule protects — committed deliberately (user OK, #182 review).
"""

# ruff: noqa: E501 — a few bar-serialisation / print lines are naturally long; wrapping them adds
# noise for zero benefit in this throwaway regression spike.
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from small_cap_stack.bullflag import tokenize
from small_cap_stack.capture import Bar
from small_cap_stack.clock import ET
from small_cap_stack.config import Settings

sys.path.insert(0, str(Path(__file__).parent))
from viz_engine import (  # noqa: E402
    _EXHAUSTION_CAP,
    _params,
    cycle_number_for,
    pick_setup,
    segment_cycles,
    significant_cycles,
    token_eps,
)

_FIXTURES = Path(__file__).parent / "review_fixtures"

# Every case the trader has walked through and confirmed in the visual review (#182). Add a
# (symbol, date) here, run --extract, eyeball the viz, and commit the fixture once confirmed.
CASES: list[tuple[str, str]] = [
    ("VRAX", "2026-07-09"),
    ("MSTZ", "2026-07-06"),
    ("MUZ", "2026-07-08"),
    ("TVRD", "2026-07-07"),
    ("CRCG", "2026-07-02"),
    ("ARCT", "2026-07-02"),
    ("IRE", "2026-07-08"),
    ("CONL", "2026-07-02"),
    ("FCEL", "2026-07-09"),
    ("OKLL", "2026-07-01"),
    ("SDOT", "2026-07-09"),
    ("FWDI", "2026-07-01"),
    ("CANF", "2026-07-01"),
    ("DFDV", "2026-07-02"),
    ("SNDQ", "2026-07-01"),
    ("OPEN", "2026-07-09"),
    ("WULF", "2026-07-08"),
    ("MARA", "2026-07-09"),
    ("DJT", "2026-07-02"),
    ("ENVX", "2026-07-09"),
    ("IREN", "2026-07-06"),
    ("CIFR", "2026-07-06"),
    ("QGEN", "2026-07-09"),
]

# Notes ride along in the fixture header for the next reader; they are not asserted. The structural-
# significance fix (#102) resolved the earlier SNDQ/WULF soft cycle-count residuals, so those cases
# no longer need a caveat; the remaining note flags a setup that is easy to misread.
NOTES: dict[str, str] = {
    "OPEN": (
        "Red-peak setup (#196): the pole 10:50->11:00 has a RED peak (11:00), now IDENTIFIED and "
        "rejected via the peak_green gate rather than skipped (the old engine wandered to a junk "
        "11:20 pole). Correctly cycle 3 / EXHAUSTED (a fading 3rd-cycle entry) and rejects on the "
        "red peak plus the 4-bar cons / retracement — the point of this fixture."
    ),
    "QGEN": (
        "Confirmed DOUBLE reject: cons_retracement (pullback gave back 70% of the pole) AND "
        "exhausted (cycle 3). Two real prior pumps precede the target — 09:25->09:55 to 39.32, then "
        "a violent +10% spike 10:00->10:10 to 43.21 — so the 10:30 push to 44.03 is the 3rd leg, and "
        "its flag retraced to 42.03. Trader-confirmed; the 2 prior cycles read correctly."
    ),
    "CIFR": (
        "Confirmed reject on pole_height (+ wick_peak + cons_retracement): a marginal late-window "
        "(seen 11:35) setup that is pure chop — the whole thing lives in a ~1% band (22.09-22.36). "
        "The 'pole' (11:40) is only ~1.2% (< the 2% min), with a 57% upper wick, and the flag gave "
        "back 56%. The suite's clearest pole_height (too-small-pole) reject as the lead reason."
    ),
    "IREN": (
        "Confirmed reject on cons_retracement: the pullback retraced ~86% of the pole (base low "
        "43.28 -> peak high 44.34, cons low 43.43) — deep, though the base is the weak-body (18%) "
        "09:40 bar; measured from the real run start (09:35) it's ~42%. Volume signature is "
        "otherwise healthy (pole 1.2-1.8M, cons fading 0.84->0.52M). Trader-confirmed reject; would "
        "have been ~1.5R (a modest winner correctly skipped). Also notable: seen 10:06 is very late "
        "for a 09:40-09:50 setup — the old top-10 scanner surfaced it late (fixed by scan_max_rows "
        "10->50, #192; worst at market open, #203). Review data predates that deploy."
    ),
    "ENVX": (
        "Confirmed reject: a fresh (cycle 1) pump whose PEAK (07:45) is a red shooting star — 69% "
        "upper wick, red body, on the day's heaviest volume (303k, a blow-off-top climax). Fails "
        "peak_green (red) + wick_peak (69% > 50%). Pole boundary left as-is per trader: the engine's "
        "tight 07:40->07:45 thrust launches off the borderline-body (41%) 07:35 base — 07:30/07:35 "
        "are low-volume (~42k) lead-in, so excluding them is reasonable. Trader happy with reject."
    ),
    "DJT": (
        "Confirmed reject: a fresh (cycle 1) pump whose flag COLLAPSED into a reversal — 3 heavy "
        "red bars (10:15-10:25) retraced ~137% of the pole, broke below the base (low 8.44 < base "
        "8.55) on HIGHER volume than the peak; fails vol_peak_gt_cons + cons_retracement + "
        "cons_holds_base together. Also the trader's market-open blind spot (#203): the move "
        "extended from 09:30 with slowdown (not lower-high) consolidations the cycle walk can't see, "
        "so cycle 1 UNDER-counts its over-extension — verdict is unaffected (rejects on the gates)."
    ),
    "MARA": (
        "Exhaustion-driven reject (with FWDI, the only two): passes EVERY gate but is the 5th "
        "contiguous pump of a worn-out move (4 real green-thrust priors 08:55->10:10, ascending "
        "12.96->13.53 into the 13.80 target). Trader-confirmed correct reject on exhaustion alone. "
        "Entry (10:35) is right and it would have been PROFITABLE — the deliberate cost of skipping "
        "late chases. The point of this fixture is guarding the exhaustion path on a gates-passing "
        "setup. Minor known render nuance: the 10:20 base bar (a weak non-bar; the real pole is the "
        "10:25 expansion) reads as part of the pullback but is drawn as the pole's base, not shaded."
    ),
}


def evaluate(day_bars: list[Bar], first_hit: datetime | None, settings: Settings) -> dict[str, Any]:
    """The engine outcome for one opportunity, as a plain dict of confirmed-meaningful values
    (times in ET so a diff is human-readable). This is the single source of truth the extractor
    pins and the checker re-derives, so any drift shows up as a field mismatch."""
    tokens = tokenize(day_bars, eps=token_eps(settings))
    all_cycles = segment_cycles(tokens)
    setup, cons_end, trig = pick_setup(
        day_bars, tokens, all_cycles, settings, first_hit=first_hit, params=_params(settings)
    )
    if setup is None or cons_end is None:
        return {"setup_found": False}

    def t(i: int) -> str:
        return day_bars[i].start.astimezone(ET).strftime("%H:%M")

    sig = significant_cycles(day_bars, all_cycles, min_volume=settings.scan_min_5m_volume // 2)
    seg = setup.segment
    cycle_num = cycle_number_for(day_bars, sig, seg.base_idx)
    return {
        "setup_found": True,
        "pole_base": t(seg.base_idx),
        "pole_peak": t(seg.peak_idx),
        "pole_len": seg.pole_len,
        "cons_start": t(seg.peak_idx + 1),
        "cons_end": t(cons_end),
        "cons_len": seg.cons_len,
        "entry_time": t(trig) if trig is not None else None,
        "entry_trigger": setup.entry_trigger,
        "entry_fill": setup.entry_fill,
        "stop": setup.stop,
        "passed": setup.passed,
        "failing_gates": [g.name for g in setup.gates if not g.passed],
        "cycle_num": cycle_num,
        "total_significant_cycles": len(sig),
        "exhausted": cycle_num is not None and cycle_num > _EXHAUSTION_CAP,
    }


def _bars_from_fixture(rows: list[list[Any]]) -> list[Bar]:
    return [
        Bar(start=datetime.fromisoformat(s), open=o, high=h, low=lo, close=c, volume=v)
        for s, o, h, lo, c, v in rows
    ]


def extract(data_dir: str) -> None:
    from small_cap_stack.report import day_chart_bars, day_opportunities, symbol_runs
    from small_cap_stack.storage import Store

    settings = Settings()
    store = Store(Path(data_dir))
    bars_df, scans = store.read("bars"), store.read("scanner_hits")
    _FIXTURES.mkdir(parents=True, exist_ok=True)
    for symbol, d in CASES:
        trading_date = date.fromisoformat(d)
        row = next(
            (
                r
                for r in day_opportunities(store, trading_date).iter_rows(named=True)
                if r["symbol"] == symbol
            ),
            None,
        )
        if row is None:
            print(f"  SKIP {symbol} {d}: no opportunity in the snapshot")
            continue
        run = next((r for r in symbol_runs(row, bars_df, scans, settings) if r.idx == 1), None)
        if run is None or not run.bars:
            print(f"  SKIP {symbol} {d}: run 1 has no bars")
            continue
        day_bars = day_chart_bars(bars_df, row["opportunity_id"], settings)
        header = {
            "symbol": symbol,
            "date": d,
            "opportunity_id": row["opportunity_id"],
            "first_hit": run.first_hit.isoformat() if run.first_hit else None,
            **({"notes": NOTES[symbol]} if symbol in NOTES else {}),
            "expected": evaluate(day_bars, run.first_hit, settings),
        }
        bars = [[b.start.isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in day_bars]
        path = _FIXTURES / f"{symbol}_{d}.json"
        path.write_text(_dump(header, bars))
        print(f"  wrote {path.name} ({len(day_bars)} bars)")


def _dump(header: dict[str, Any], bars: list[list[Any]]) -> str:
    """Pretty header (the human-reviewed metadata + expected outcome) with one compact line per
    bar — readable git diffs, a third the size of a fully-indented dump."""
    head = json.dumps(header, indent=2)[1:-1].rstrip()  # drop the outer braces, keep inner fields
    rows = ",\n    ".join(json.dumps(b) for b in bars)
    return f'{{{head},\n  "bars": [\n    {rows}\n  ]\n}}\n'


def check() -> int:
    settings = Settings()
    fixtures = sorted(_FIXTURES.glob("*.json"))
    if not fixtures:
        print("no fixtures found — run with --extract first")
        return 1
    failures = 0
    for path in fixtures:
        fx = json.loads(path.read_text())
        day_bars = _bars_from_fixture(fx["bars"])
        first_hit = datetime.fromisoformat(fx["first_hit"]) if fx["first_hit"] else None
        actual = evaluate(day_bars, first_hit, settings)
        expected = fx["expected"]
        diffs = {
            k: (expected.get(k), actual.get(k))
            for k in expected
            if expected.get(k) != actual.get(k)
        }
        diffs.update({k: (None, actual.get(k)) for k in actual if k not in expected})
        if diffs:
            failures += 1
            print(f"FAIL {fx['symbol']} {fx['date']}")
            for k, (exp, act) in diffs.items():
                print(f"       {k}: expected {exp!r} -> got {act!r}")
        else:
            v = actual
            verdict = (
                "no setup"
                if not v["setup_found"]
                else (
                    f"{'PASS' if v['passed'] else 'REJECT'}, entry {v['entry_time']}, "
                    f"cycle {v['cycle_num']}{' EXHAUSTED' if v['exhausted'] else ''}"
                )
            )
            print(f"ok   {fx['symbol']:5} {fx['date']}  {verdict}")
    print(
        f"\n{len(fixtures) - failures}/{len(fixtures)} cases match"
        + ("" if not failures else " — REGRESSIONS ABOVE")
    )
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extract", action="store_true", help="rebuild fixtures from a /data snapshot")
    ap.add_argument("--data-dir", default="/tmp/scs-data", help="store root for --extract")
    args = ap.parse_args()
    if args.extract:
        extract(args.data_dir)
    else:
        sys.exit(check())


if __name__ == "__main__":
    main()
