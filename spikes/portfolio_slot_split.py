"""Spike #416: does the 2/day trade cap waste capital, and would a 75/25 slot split fix it?

Two questions from the owner:

1. On a day with only **one** setup, does the 2-trade cap leave capital idle?
2. Would splitting the notional cap **75% to the day's first trade / 25% to its second** (instead
   of the flat 50%/50%) deploy more of the book?

Both are answered by replaying the **real** book — `simulate_portfolio` /
`simulate_portfolio_adaptive`, the same sizing, cost and ledger code the dashboard runs — under
variant per-slot notional caps. Nothing here re-implements the simulator.

⚠️ This spike replays from the **published payload** (`portfolio.json` on the `dashboard-data`
branch), not from the Parquet store, because a web session has no box access. That is sound *for
this question only*: the per-day candidate set and every exit outcome are size-independent, so the
payload's `trades` + `skipped` records are a complete description of what the book saw, at every
target on the published grid. What it CANNOT do is change a rule that alters *which* candidates
qualify (the price band, the time window, `max_trades_per_day` above the published 2) — those need
the store. `--validate` re-runs the published configuration through this harness and asserts it
reproduces the published equity curve trade-for-trade; if that fails, the replay is not faithful
and no other number here is either.

⚠️ Variants must be decidable at trigger time (the standing rule from #379). A slot split is:
"first setup of the day" is known when it fires. Ranking a day's setups against each other would
not be.

    .venv/bin/python spikes/portfolio_slot_split.py --payload data/spikes/portfolio.json --validate
    .venv/bin/python spikes/portfolio_slot_split.py --payload data/spikes/portfolio.json \
        --json data/spikes/slot-split.json
"""

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.portfolio import (
    PortfolioResult,
    simulate_portfolio,
    simulate_portfolio_adaptive,
)
from small_cap_stack.portfolio import sim as sim_mod
from small_cap_stack.portfolio.costs import SizedPosition
from small_cap_stack.portfolio.exit import ExitOutcome

# The slot splits to sweep. Each is (name, per-slot notional fraction of opening equity). Every one
# must sum to <= 1.0 or the settled-cash invariant breaks (research/decisions.md, #232 §6).
SPLITS: list[tuple[str, tuple[float, ...]]] = [
    ("50/50 (live)", (0.50, 0.50)),
    ("60/40", (0.60, 0.40)),
    ("75/25", (0.75, 0.25)),
    ("90/10", (0.90, 0.10)),
    ("100/0", (1.00, 0.00)),
]


@dataclass(frozen=True)
class _ReplayCandidate:
    """A `CandidateTrade` stand-in whose exit is looked up rather than simulated from bars.

    Structurally compatible with everything `sim._take_day` / `adaptive._day_signal_r` touch. It
    deliberately has no `bars`/`entry_index`: this replay cannot answer questions that need them,
    and a stub that silently returned an empty bar window would answer them wrongly.
    """

    trading_date: date
    symbol: str
    seg_id: str
    run: int
    trigger_at: datetime
    entry_price: float
    entry_fill: float
    stop: float
    risk: float
    outcomes: dict[float, ExitOutcome]
    float_shares: int | None = None
    max_r: float | None = None
    max_gain_pct: float | None = None

    def exit_under(self, s: Settings, target_r: float, breakeven_r: float) -> ExitOutcome:
        if breakeven_r != 0.0:
            raise KeyError(f"replay has no outcome at breakeven_r={breakeven_r} (payload is 0)")
        try:
            return self.outcomes[round(target_r, 4)]
        except KeyError:
            raise KeyError(
                f"{self.seg_id} run {self.run}: no published outcome at target {target_r}; "
                f"have {sorted(self.outcomes)}"
            ) from None


def _outcome(rec: dict[str, Any]) -> ExitOutcome:
    # exit_index is not published and nothing in the sizing/ledger path reads it. -1 rather than a
    # plausible 0 so anything that ever does read it fails a bounds check instead of silently
    # pointing at the entry bar.
    return ExitOutcome(
        realized_r=rec["realized_r"],
        reason=rec["reason"],
        exit_index=-1,
        exit_price=rec["exit_price"],
    )


def load_candidates(payload: dict[str, Any]) -> list[tuple[date, list[_ReplayCandidate]]]:
    """Rebuild the per-day candidate set from every published book.

    A day's candidates are target-independent (selection runs before sizing and before the exit is
    known), so each fixed-target book lists the same setups — the books differ only in the outcome
    attached. Merging them gives each candidate its full target -> outcome map, which is what lets
    the adaptive book's per-day re-fit be replayed.
    """
    by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    outcomes: dict[tuple[str, str, int], dict[float, ExitOutcome]] = {}
    for book in payload["books"].values():
        for rec in [*book["trades"], *book["skipped"]]:
            key = (rec["date"], rec["seg_id"], rec["run"])
            by_key.setdefault(key, rec)
            outcomes.setdefault(key, {})[round(rec["target_r"], 4)] = _outcome(rec)

    days: dict[date, list[_ReplayCandidate]] = {}
    for key, rec in by_key.items():
        d = date.fromisoformat(rec["date"])
        entry, stop = rec["entry"], rec["stop"]
        days.setdefault(d, []).append(
            _ReplayCandidate(
                trading_date=d,
                symbol=rec["symbol"],
                seg_id=rec["seg_id"],
                run=rec["run"],
                trigger_at=datetime.fromisoformat(rec["trigger_at"]),
                entry_price=entry,
                entry_fill=entry,
                stop=stop,
                risk=round(entry - stop, 6),
                outcomes=outcomes[key],
                float_shares=rec.get("float_shares"),
                max_r=rec.get("max_r"),
                max_gain_pct=rec.get("max_pct"),
            )
        )
    # Every collected session, including the ones with no candidates: the ledgers roll on them and
    # the adaptive ladder counts them, so dropping them would change the book.
    for point in payload["books"]["adaptive"]["equity_curve"]:
        days.setdefault(date.fromisoformat(point["date"]), [])
    return sorted(((d, sorted(cs, key=lambda c: c.trigger_at)) for d, cs in days.items()))


class _SlotSizer:
    """Patches `sim.size_position` so slot *i* of a day is capped at `fractions[i]` of equity.

    `_take_day` sizes its selected trades in trigger order, calling `_select_day` once first — so
    patching that call to reset the counter gives an exact, day-local slot index without touching
    the simulator. Restores both names on exit.
    """

    def __init__(self, fractions: Sequence[float]) -> None:
        self.fractions = tuple(fractions)
        self._slot = 0
        self._real_select = sim_mod._select_day
        self._real_size = sim_mod.size_position

    def __enter__(self) -> "_SlotSizer":
        def select(cands: Any, s: Settings) -> Any:
            self._slot = 0
            return self._real_select(cands, s)

        def sized(
            equity: float,
            entry_price: float,
            stop: float,
            *,
            risk_fraction: float,
            max_position_fraction: float,
        ) -> SizedPosition:
            i = min(self._slot, len(self.fractions) - 1)
            self._slot += 1
            return self._real_size(
                equity,
                entry_price,
                stop,
                risk_fraction=risk_fraction,
                max_position_fraction=self.fractions[i],
            )

        sim_mod._select_day = select  # type: ignore[assignment]
        sim_mod.size_position = sized  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        sim_mod._select_day = self._real_select  # type: ignore[assignment]
        sim_mod.size_position = self._real_size  # type: ignore[assignment]


def _days_arg(days: list[tuple[date, list[_ReplayCandidate]]]) -> Any:
    """The simulator is typed against `CandidateTrade`; the replay stub is structural, not nominal.

    Keeping the cast in one named place beats scattering `# type: ignore` at every call site.
    """
    return [(d, list(cs)) for d, cs in days]


def run_book(
    days: list[tuple[date, list[_ReplayCandidate]]],
    s: Settings,
    fractions: Sequence[float],
    *,
    adaptive: bool,
    target_r: float | None = None,
) -> PortfolioResult:
    with _SlotSizer(fractions):
        if adaptive:
            return simulate_portfolio_adaptive(_days_arg(days), s).result
        return simulate_portfolio(_days_arg(days), s, target_r=target_r)


def _deployment(res: PortfolioResult, sessions: int) -> dict[str, float]:
    """How much of the book was actually at work, and what bounded it."""
    by_day: dict[date, float] = {}
    peak_frac: list[float] = []
    for t in res.trades:
        by_day.setdefault(t.trading_date, t.equity_before)
        frac = t.qty * t.entry_price / by_day[t.trading_date]
        peak_frac.append(frac)
    day_frac: dict[date, float] = {}
    for t in res.trades:
        day_frac[t.trading_date] = day_frac.get(t.trading_date, 0.0) + t.qty * t.entry_price
    opening = {t.trading_date: t.equity_before for t in reversed(res.trades)}
    day_pct = [day_frac[d] / opening[d] for d in day_frac]
    return {
        "sessions": sessions,
        "trading_days": len(day_frac),
        "avg_deploy_pct_trading_day": round(100 * sum(day_pct) / len(day_pct), 2) if day_pct else 0,
        "avg_deploy_pct_all_sessions": round(100 * sum(day_pct) / sessions, 2) if sessions else 0,
        "max_deploy_pct": round(100 * max(day_pct), 2) if day_pct else 0.0,
        "avg_position_pct": round(100 * sum(peak_frac) / len(peak_frac), 2) if peak_frac else 0.0,
        "cap_bound_trades": sum(1 for t in res.trades if t.sized_by == "cap"),
    }


def _row(name: str, res: PortfolioResult, sessions: int) -> dict[str, Any]:
    return {
        "variant": name,
        "n_trades": res.n_trades,
        "total_r": res.total_r,
        "end_equity": round(res.end_equity, 2),
        "return_pct": round(100 * res.return_pct, 2),
        "max_dd_pct": round(100 * res.max_drawdown_pct, 2),
        "costs_usd": round(res.total_costs_usd, 2),
        "cap_skips": sum(1 for sk in res.skipped if sk.skip_reason == "cap"),
        **_deployment(res, sessions),
    }


def validate(days: list[tuple[date, list[_ReplayCandidate]]], payload: dict[str, Any]) -> bool:
    """Reproduce every published book through this harness and diff it against the payload."""
    s = Settings()
    ok = True
    for name, book in payload["books"].items():
        adaptive = name == "adaptive"
        res = run_book(
            days,
            s,
            (s.portfolio_position_fraction,) * s.portfolio_max_trades_per_day,
            adaptive=adaptive,
            target_r=None if adaptive else float(name),
        )
        want_eq = book["stats"]["end_equity"]
        got_qty = [t.qty for t in res.trades]
        want_qty = [t["qty"] for t in book["trades"]]
        match = abs(res.end_equity - want_eq) < 0.01 and got_qty == want_qty
        ok = ok and match
        print(
            f"  {'OK ' if match else 'FAIL'} book={name:<9} "
            f"end_equity {res.end_equity:9.4f} vs {want_eq:9.4f}  "
            f"trades {res.n_trades} vs {len(book['trades'])}"
        )
    return ok


# Gate variants for the latent-exposure count, newest first. Each is (label, price band, window) —
# the live book plus every looser configuration it has actually run under, plus the raw scan band.
# This answers "is the cap dormant, or merely dormant *right now*".
GATE_VARIANTS: list[tuple[str, tuple[float, float], tuple[time, time]]] = [
    ("LIVE $2-20, 05:30-09:15", (2.0, 20.0), (time(5, 30), time(9, 15))),
    ("pre-#405 $2-20, 04:00-09:15", (2.0, 20.0), (time(4, 0), time(9, 15))),
    ("pre-#386 $1-20, 05:30-09:15", (1.0, 20.0), (time(5, 30), time(9, 15))),
    ("pre-07-21 $1-20, 04:00-09:30", (1.0, 20.0), (time(4, 0), time(9, 30))),
    ("scan band $1-50, 04:00-09:30", (1.0, 50.0), (time(4, 0), time(9, 30))),
    ("scan band $1-50, whole window", (1.0, 50.0), (time(0, 0), time(23, 59))),
]


def _takeable_setups(charts_dir: Path, s: Settings) -> tuple[list[dict[str, Any]], int]:
    """Every engine-v2 **takeable** setup in the published charts, with its trigger time + fill.

    The charts payload carries `engine.takeable` and `markers.entry` for every setup on every
    collected day — i.e. the population *before* the book's price band and time window are applied.
    That is the only published source that can answer whether the cap has latent exposure; the
    portfolio payload by construction contains only setups that already passed those filters.

    `levels.entry` is the 1-tick mechanical trigger; the band is applied to the 3-tick fill
    (`research/engine-v2.md`, #182/#190), hence the +2-tick adjustment. The reconstruction is
    checked against the published book in `main` — if that check fails, this mapping is wrong.
    """
    excluded = {sym.upper() for sym in s.portfolio_exclude_symbols}
    rows: list[dict[str, Any]] = []
    files = sorted(charts_dir.glob("*.json"))
    for f in files:
        payload = json.loads(f.read_text())
        for c in payload["charts"]:
            if not (c.get("engine") or {}).get("takeable"):
                continue
            if str(c["symbol"]).upper() in excluded:  # mis-captured ETFs, never candidates
                continue
            entry_at = (c.get("markers") or {}).get("entry")
            if entry_at is None:
                continue
            rows.append(
                {
                    "date": payload["trading_date"],
                    "symbol": c["symbol"],
                    "trigger_et": datetime.fromtimestamp(entry_at, ET).time(),
                    "fill": round((c["levels"]["entry"] or 0.0) + 2 * s.tick_size, 4),
                }
            )
    return rows, len(files)


def gate_exposure(rows: list[dict[str, Any]], sessions: int, cap: int) -> list[dict[str, Any]]:
    """How many setups each gate variant admits, and how many the day cap would then drop."""
    out = []
    for label, (lo, hi), (start, end) in GATE_VARIANTS:
        per: dict[str, int] = {}
        for r in rows:
            if lo <= r["fill"] <= hi and start <= r["trigger_et"] < end:
                per[r["date"]] = per.get(r["date"], 0) + 1
        counts = list(per.values())
        dist: dict[int, int] = {}
        for n in counts:
            dist[n] = dist.get(n, 0) + 1
        out.append(
            {
                "variant": label,
                "setups": sum(counts),
                "per_session": round(sum(counts) / sessions, 2) if sessions else 0.0,
                "days_over_cap": sum(1 for n in counts if n > cap),
                "cap_dropped": sum(max(0, n - cap) for n in counts),
                "setups_per_day": dict(sorted(dist.items())),
            }
        )
    return out


def binding_constraint(res: PortfolioResult, s: Settings) -> list[dict[str, Any]]:
    """Per trade: the stop distance, the notional the risk budget wanted, and what actually bound.

    The whole slot-split question turns on this table. `risk_qty < cap_qty` exactly when
    `(entry-stop)/entry > risk_fraction / position_fraction`, so the notional cap can only ever
    bite on a **tight** stop — raising slot 1's cap changes nothing on a trade the risk budget
    already sized below it.
    """
    opening: dict[date, float] = {}
    for t in reversed(res.trades):
        opening[t.trading_date] = t.equity_before
    out = []
    for t in res.trades:
        eq = opening[t.trading_date]
        stop_pct = (t.entry_price - t.stop) / t.entry_price
        out.append(
            {
                "date": t.trading_date.isoformat(),
                "symbol": t.symbol,
                "stop_pct": round(100 * stop_pct, 2),
                # What the risk budget alone wanted, as a fraction of opening equity. Above the
                # cap => cap-bound; below => the cap is irrelevant to this trade.
                "risk_wants_pct": round(100 * t.risk_fraction / stop_pct, 1),
                "cap_pct": round(100 * s.portfolio_position_fraction, 1),
                "got_pct": round(100 * t.qty * t.entry_price / eq, 1),
                "sized_by": t.sized_by,
            }
        )
    return out


def trade_diff(a: PortfolioResult, b: PortfolioResult) -> list[dict[str, Any]]:
    """Per-trade qty/P&L delta between two variants, with each trade's slot in its day."""
    other = {(t.trading_date, t.symbol): t for t in b.trades}
    slot: dict[date, int] = {}
    out = []
    for t in a.trades:
        i = slot.get(t.trading_date, 0)
        slot[t.trading_date] = i + 1
        o = other.get((t.trading_date, t.symbol))
        out.append(
            {
                "date": t.trading_date.isoformat(),
                "symbol": t.symbol,
                "slot": i + 1,
                "qty_a": t.qty,
                "qty_b": o.qty if o else None,
                "net_a": round(t.net_pnl_usd, 2),
                "net_b": round(o.net_pnl_usd, 2) if o else None,
                "d_net": round(o.net_pnl_usd - t.net_pnl_usd, 2) if o else None,
            }
        )
    return out


def risk_sweep(
    days: list[tuple[date, list[_ReplayCandidate]]], s: Settings, fractions: Sequence[float]
) -> list[dict[str, Any]]:
    """The *other* lever on deployment: raise the risk budget, at the live notional cap.

    Included because it is the honest answer to "why is capital idle" — at a 5% risk budget the
    notional cap is not what stops the book deploying, the risk budget is.
    """
    out = []
    for rf in (0.05, 0.075, 0.10, 0.15, 0.20):
        res = run_book(
            days,
            s.model_copy(update={"portfolio_risk_fraction": rf}),
            fractions,
            adaptive=False,
            target_r=2.0,
        )
        opening: dict[date, float] = {}
        for t in reversed(res.trades):
            opening[t.trading_date] = t.equity_before
        avg_pos = (
            100
            * sum(t.qty * t.entry_price / opening[t.trading_date] for t in res.trades)
            / len(res.trades)
            if res.trades
            else 0.0
        )
        out.append(
            {
                "risk_pct": round(100 * rf, 1),
                "n_trades": res.n_trades,
                "end_equity": round(res.end_equity, 2),
                "return_pct": round(100 * res.return_pct, 1),
                "max_dd_pct": round(100 * res.max_drawdown_pct, 1),
                "avg_position_pct": round(avg_pos, 1),
                "cap_bound_trades": sum(1 for t in res.trades if t.sized_by == "cap"),
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", type=Path, required=True, help="portfolio.json (dashboard-data)")
    ap.add_argument("--charts", type=Path, help="charts/ dir (dashboard-data) for gate exposure")
    ap.add_argument("--validate", action="store_true", help="check the replay reproduces the book")
    ap.add_argument("--json", type=Path, help="write results here")
    args = ap.parse_args()

    payload = json.loads(args.payload.read_text())
    days = load_candidates(payload)
    sessions = len(days)
    s = Settings()

    per_day = [len(cs) for _, cs in days]
    print(f"\nsessions={sessions}  candidates={sum(per_day)}")
    hist: dict[int, int] = {}
    for n in per_day:
        hist[n] = hist.get(n, 0) + 1
    print("qualifying setups per session:", dict(sorted(hist.items())))
    print(
        f"days at or above the {s.portfolio_max_trades_per_day}/day cap:",
        sum(1 for n in per_day if n >= s.portfolio_max_trades_per_day),
    )
    print(
        "days the cap would have dropped a setup:",
        sum(1 for n in per_day if n > s.portfolio_max_trades_per_day),
    )

    if args.validate:
        print("\nvalidating replay against the published books:")
        if not validate(days, payload):
            raise SystemExit("replay does not reproduce the published book — numbers are not valid")

    out: dict[str, Any] = {"sessions": sessions, "setups_per_session": hist, "books": {}}
    for label, adaptive, target in [("adaptive", True, None), ("fixed 2R", False, 2.0)]:
        print(f"\n=== {label} book, slot splits ===")
        head = f"{'variant':<14}{'trades':>7}{'total R':>9}{'end eq':>9}{'ret%':>8}{'dd%':>7}"
        print(head + f"{'cap-bound':>11}{'avg dep%':>10}{'max dep%':>10}")
        rows = []
        for name, fractions in SPLITS:
            res = run_book(days, s, fractions, adaptive=adaptive, target_r=target)
            r = _row(name, res, sessions)
            rows.append(r)
            print(
                f"{r['variant']:<14}{r['n_trades']:>7}{r['total_r']:>9.2f}{r['end_equity']:>9.2f}"
                f"{r['return_pct']:>8.2f}{r['max_dd_pct']:>7.2f}{r['cap_bound_trades']:>11}"
                f"{r['avg_deploy_pct_trading_day']:>10.1f}{r['max_deploy_pct']:>10.1f}"
            )
        out["books"][label] = rows

        base = run_book(days, s, SPLITS[0][1], adaptive=adaptive, target_r=target)
        split = run_book(days, s, (0.75, 0.25), adaptive=adaptive, target_r=target)
        out.setdefault("binding_constraint", {})[label] = binding_constraint(base, s)
        out.setdefault("trade_diff_50_50_to_75_25", {})[label] = trade_diff(base, split)
        print(f"\n  --- {label}: what bound each position's size ---")
        print(f"  {'date':12}{'sym':6}{'stop%':>7}{'wants%':>9}{'cap%':>7}{'got%':>7}{'bound':>7}")
        for r in out["binding_constraint"][label]:
            print(
                f"  {r['date']:12}{r['symbol']:6}{r['stop_pct']:7.2f}{r['risk_wants_pct']:13.1f}"
                f"{r['cap_pct']:7.1f}{r['got_pct']:7.1f}{r['sized_by']:>7}"
            )
        print(f"\n  --- {label}: 50/50 -> 75/25, per trade ---")
        print(f"  {'date':12}{'sym':6}{'slot':>5}{'qty':>6}{'->':>6}{'d net $':>10}")
        for r in out["trade_diff_50_50_to_75_25"][label]:
            qty_b = "—" if r["qty_b"] is None else str(r["qty_b"])
            d_net = "dropped" if r["d_net"] is None else f"{r['d_net']:+.2f}"
            print(
                f"  {r['date']:12}{r['symbol']:6}{r['slot']:5d}{r['qty_a']:6d}{qty_b:>6}{d_net:>10}"
            )

    out["risk_sweep"] = risk_sweep(days, s, SPLITS[0][1])
    print("\n=== the other lever: risk_fraction sweep at the live 50/50 cap (fixed 2R) ===")
    print(
        f"{'risk%':>7}{'trades':>8}{'end eq':>9}{'ret%':>8}{'dd%':>7}{'avg pos%':>10}{'bound':>8}"
    )
    for r in out["risk_sweep"]:
        print(
            f"{r['risk_pct']:7.1f}{r['n_trades']:8d}{r['end_equity']:9.2f}{r['return_pct']:8.1f}"
            f"{r['max_dd_pct']:7.1f}{r['avg_position_pct']:10.1f}{r['cap_bound_trades']:8d}"
        )

    if args.charts:
        rows_ch, n_files = _takeable_setups(args.charts, s)
        exposure = gate_exposure(rows_ch, n_files, s.portfolio_max_trades_per_day)
        out["gate_exposure"] = exposure
        # The live variant must reproduce the published book exactly, or the chart-derived
        # reconstruction (and every looser row below it) is measuring something else.
        live = exposure[0]
        assert live["setups"] == sum(per_day), (
            f"chart reconstruction gives {live['setups']} setups under the live gates but the "
            f"published book has {sum(per_day)} — the reconstruction is wrong, not the book"
        )
        print(
            f"\n=== latent cap exposure: takeable setups by gate variant ({n_files} sessions) ==="
        )
        print(f"{'variant':<32}{'setups':>8}{'/session':>10}{'days>cap':>10}{'dropped':>9}")
        for r in exposure:
            print(
                f"{r['variant']:<32}{r['setups']:8d}{r['per_session']:10.2f}"
                f"{r['days_over_cap']:10d}{r['cap_dropped']:9d}   {r['setups_per_day']}"
            )

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(out, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
