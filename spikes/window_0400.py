"""What does moving the book's trigger floor 05:30 -> 04:00 actually cost or buy?

The floor was added 2026-07-31 (#405) as an explicit owner's call, not a measured edge — the
time-of-day report found no pre-market window statistically separable from another. This replays
the whole local store under both floors and diffs the book, so the reversal is decided on the
number rather than on the same intuition that put it there.

Compute-on-read: nothing is written, the store is read-only. Run on the Mac — NOT the box.

    .venv/bin/python spikes/window_0400.py
"""

from __future__ import annotations

from datetime import time

from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.portfolio import extract_day_trades, simulate_portfolio_adaptive
from small_cap_stack.storage import Store

STORE = "data/live"


def _settings(earliest: time) -> Settings:
    return Settings(_env_file=None, select_window_start=earliest)  # type: ignore[call-arg]


def _dates(store: Store) -> list:
    opps = store.read("opportunities")
    return sorted(set(opps["trading_date"].to_list()))


def run(earliest: time) -> tuple[object, list]:
    s = _settings(earliest)
    store = Store(STORE)
    days = [(d, extract_day_trades(store, s, d)) for d in _dates(store)]
    book = simulate_portfolio_adaptive(days, s)
    return book.result, [t for _d, cands in days for t in cands]


def main() -> None:
    store = Store(STORE)
    dates = _dates(store)
    print(f"store={STORE}  sessions={len(dates)}  {dates[0]} -> {dates[-1]}\n")

    rows = []
    for label, earliest in (("live  05:30", time(5, 30)), ("wider 04:00", time(4, 0))):
        res, cands = run(earliest)
        rows.append((label, res, cands))
        print(
            f"{label}: candidates={len(cands):3d}  trades={res.n_trades:3d}  "
            f"R={res.total_r:+7.2f}  end=${res.end_equity:8.2f}  "
            f"dd={res.max_drawdown_pct:5.2f}%  win={res.win_rate:5.1%}"
        )

    (_, live, live_c), (_, wide, wide_c) = rows
    print(
        f"\ndelta: candidates {len(wide_c) - len(live_c):+d}  "
        f"trades {wide.n_trades - live.n_trades:+d}  "
        f"R {wide.total_r - live.total_r:+.2f}  equity ${wide.end_equity - live.end_equity:+.2f}  "
        f"dd {wide.max_drawdown_pct - live.max_drawdown_pct:+.2f}pp"
    )

    # Candidates are only half the story. The day takes the first 2 BY TRIGGER TIME, so an earlier
    # floor doesn't just add setups — it can displace later ones that used to make the cut.
    def key(t):
        return (t.trading_date, t.symbol, t.run)

    live_trades = {key(t): t for t in live.trades}
    wide_trades = {key(t): t for t in wide.trades}

    added = [t for k, t in wide_trades.items() if k not in live_trades]
    dropped = [t for k, t in live_trades.items() if k not in wide_trades]

    def show(label: str, trades: list) -> None:
        print(f"\n{label}: {len(trades)}")
        for t in sorted(trades, key=lambda t: (t.trading_date, t.trigger_at)):
            et = t.trigger_at.astimezone(ET).strftime("%H:%M")
            print(
                f"  {t.trading_date}  {t.symbol:<6} {et} ET  entry=${t.entry_price:6.2f}  "
                f"R={t.realized_r:+6.2f}  net=${t.net_pnl_usd:+7.2f}  ({t.reason})"
            )

    show("TAKEN under 04:00 but not under 05:30", added)
    show("DISPLACED — taken under 05:30, pushed out by an earlier trigger", dropped)


if __name__ == "__main__":
    main()
