"""Spike #690 (stage 4): switch the adaptive target and the risk ladder back on, over 197 sessions.

Both layers the trader asked for **already exist, are tested, and are switched off**:

- the **adaptive target** by ``portfolio_target_grid = (2.0,)`` — a one-value grid makes
  ``best_target`` a no-op, so the book always uses ``portfolio_target_r`` (§D-38);
- the **risk ladder / kill-switch** by ``portfolio_risk_rungs = 1`` — one rung means always full
  risk (§D-23).

Each was retired on the evidence available at the time: the target optimiser moved the target on
**2 of 61 sessions and was wrong both times**, and the throttle cost roughly 3.5%/month between
#239 and #474. The record is now **197 sessions** (166 reconstructed + 31 live), and it contains the
multi-week losing stretches that are exactly what a kill-switch is supposed to catch. So the honest
answer to "would these have helped?" is to turn them on and replay, not to re-argue the old test.

**This runs the real book, not a re-implementation.** ``build_portfolio_payload`` under ``Settings``
overrides, so sizing, costs, the notional cap, the exit model and the daily re-fit are all the
shipped ones and the sweep cannot drift from what would actually trade.

⚠️ Read ``books_all``, not ``books``. ``books`` is deliberately live-only (the book is
path-dependent twice over — the daily re-fit reads a trailing window and every position sizes off
running equity — so splicing the reconstructed days in front *replaces* the live record rather than
extending it). ``books_all`` is the combined recon-then-live simulation, which is the only view that
sees all 197 sessions.

⚠️ Memory: ``build_portfolio_payload`` holds every collected day's bars for the whole run, and each
variant is a full pass. Fine on the Mac, **do not run this on the box** (CLAUDE.md — the CX23 OOMs
on a single `--all` backfill, let alone eight).

    python spikes/adaptive_book_sweep.py --store data/live --recon-store data/recon
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from small_cap_stack.config import Settings
from small_cap_stack.portfolio.payload import build_portfolio_payload
from small_cap_stack.storage import Store

# The target grid the optimiser gets to choose from when it is switched on. Deliberately coarse:
# every extra grid value is another thing the daily re-fit can chase on a handful of trades, which
# is how §D-38's version moved the target twice in 61 sessions and was wrong both times.
GRID = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)

VARIANTS: list[tuple[str, dict[str, Any]]] = [
    ("shipped (both off)", {}),
    # --- the adaptive target on its own ---
    ("target: all prior trades", {"portfolio_target_grid": GRID}),
    ("target: trailing 20d", {"portfolio_target_grid": GRID, "portfolio_adaptive_window_days": 20}),
    ("target: trailing 40d", {"portfolio_target_grid": GRID, "portfolio_adaptive_window_days": 40}),
    (
        # `portfolio_target_switch_z` is the guard that stops the optimiser switching on noise: the
        # new target must beat the incumbent by z standard errors on the SAME trades. Dropping it to
        # 0 is the pure-argmax behaviour the layer had before #476, included so the guard's value is
        # visible rather than assumed.
        "target: all prior, no switch guard",
        {"portfolio_target_grid": GRID, "portfolio_target_switch_z": 0.0},
    ),
    # --- the risk ladder on its own ---
    ("ladder: 3 rungs, 2-day step", {"portfolio_risk_rungs": 3, "portfolio_risk_step_days": 2}),
    ("ladder: 3 rungs, eager (1d)", {"portfolio_risk_rungs": 3, "portfolio_risk_step_days": 1}),
    ("ladder: 5 rungs, 2-day step", {"portfolio_risk_rungs": 5, "portfolio_risk_step_days": 2}),
    ("ladder: 5 rungs, 3-day step", {"portfolio_risk_rungs": 5, "portfolio_risk_step_days": 3}),
    # --- both, which is what the trader actually described ---
    (
        "BOTH: target all prior + 3-rung ladder",
        {
            "portfolio_target_grid": GRID,
            "portfolio_risk_rungs": 3,
            "portfolio_risk_step_days": 2,
        },
    ),
    (
        "BOTH: target 20d + 5-rung ladder",
        {
            "portfolio_target_grid": GRID,
            "portfolio_adaptive_window_days": 20,
            "portfolio_risk_rungs": 5,
            "portfolio_risk_step_days": 2,
        },
    ),
]


def _fmt(label: str, st: dict[str, Any]) -> str:
    return (
        f"{label:<38} n={st['n_trades']:>3} win={st['win_rate']:.3f} "
        f"totR={st['total_r']:>+7.2f} end=${st['end_equity']:>8.2f} "
        f"ret={st['return_pct']:>+8.4f} maxDD={st['max_drawdown_pct']:.4f}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True, help="live store root")
    ap.add_argument("--recon-store", help="recon store root (needed for the 197-session view)")
    ap.add_argument("--json", help="write the full result here")
    args = ap.parse_args()

    store = Store(Path(args.store))
    recon = Store(Path(args.recon_store)) if args.recon_store else None
    now = datetime.now(UTC)
    key = "books_all" if recon is not None else "books"

    out: dict[str, Any] = {}
    print(f"=== the real book, per variant ({key}) ===")
    for label, overrides in VARIANTS:
        payload = build_portfolio_payload(store, Settings(**overrides), now, recon_store=recon)
        books = payload.get(key)
        if not books:
            print(f"{label:<38} (no {key} — is the recon store populated?)")
            continue
        book = books["adaptive"]  # type: ignore[index]
        st = book["stats"]
        out[label] = {
            "stats": st,
            "equity_curve": book.get("equity_curve"),
            "daily_risk": book.get("daily_risk"),
            "trades": book.get("trades"),
        }
        print(_fmt(label, st))

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2, default=str))
        print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
