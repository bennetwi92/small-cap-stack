"""Engine lab synthesis — compose the three agents' proposals and spend the holdout ONCE.

    .venv/bin/python spikes/engine_lab/synthesis.py

Three agents worked in parallel on one population, each owning one question and holding the other
two fixed. This composes them and runs the only out-of-sample test the project has.

## The configurations, DECLARED BEFORE the holdout is opened

Three were declared, and **four were run** — the honest record of what happened, because the count
is itself part of the evidence:

    H0  status quo          shipped selection · 2R/-1R bracket · 2/day · 50% cap
    H1  primary proposal    shipped selection · wide-stop bracket · 1/day · 75% cap
    H1b added after H0-H2   as H1 but 2/day at 50% cap
    H2  secondary           agent A's unproven filter REPLACING shipped selection, 1/day

H1 changes only the two things that carried walk-forward support: the bracket (agent C, 4/6 blocks
fixed and 4/6 refit, with the refit search independently choosing the same multiples in all six)
and the capacity/sizing (agent B, 5/6 blocks). H2's rule **failed** its walk-forward (2/6) and
agent A explicitly does not recommend shipping it — it is a labelled look, not a proposal.

⚠️ **H1b was not pre-declared.** It was added after seeing the first three, because agent B fitted
`max_per_day=1` against the *shipped* bracket, where the notional cap bound on 70% of trades — and
agent C's wider stop shrinks positions enough that the cap may no longer bind, which neither agent
could see from inside its own half. That is a real question, but adding it means the holdout was
consulted four times rather than three, and four looks at 31 sessions is enough to flatter the best
of them by chance. Treat the spread between the four as uninformative; it is one trade wide.

## What each agent contributed

- **A (rules):** no selection rule cleared the bar. Its durable findings are negative and
  structural — `passed` is worse than random and `score` is noise, and `first_rank` /
  `n_scanner_hits` are lookahead and must never be used. Selection is UNCHANGED in H1 as a result.
- **B (risk):** the settled-cash invariant ties capacity and notional together
  (`position_fraction * max_per_day <= 1.0`), so 1 trade a day at 75% is one decision, not two.
- **C (exits):** stop at 1.30x the consolidation range below entry instead of on the low; target
  unchanged in price at 2.00x the range above entry.

## The entanglement that must not be got wrong

`SHIPPED`'s `stop_pct >= 0.025` floor is measured against the **consolidation-low** stop, not the
widened one. Agent C found that restating it against the new stop (lowering it to 2.0% so the
widened stop still clears 2.5%) admits 8 more trades and takes DEV+VAL from +0.168 to -0.021
net R/trade. So the floor stays where it is: selection reads the shipped stop, the bracket uses
the widened one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine_lab.common import (  # noqa: E402
    LAB_OUT,
    SHIPPED,
    Sizing,
    build_book,
    fixed_target_r,
    load_panel,
    load_paths,
    replay_bracket,
    score,
)

STOP_M = 1.30  # stop at entry - m * C
TARGET_T = 2.00  # target at entry + t * C
CONFIGS = ("H0", "H1", "H1b", "H2")
RULES_FILTER = {"hits_max": 2, "min_risk_usd": 0.19, "cons_len_max": 3}


def agent_a_filter(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(
        (pl.col("hits_before_trigger") <= RULES_FILTER["hits_max"])
        & (pl.col("planned_risk") >= RULES_FILTER["min_risk_usd"])
        & (pl.col("cons_len") <= RULES_FILTER["cons_len_max"])
    )


def wide_bracket(df: pl.DataFrame, paths: dict[str, np.ndarray]) -> pl.DataFrame:
    """Re-derive every row's R against the widened stop, from the bars.

    ⚠️ The panel's `max_r` is denominated in the shipped stop's risk, so it is meaningless here.
    `stop` is overwritten because sizing and the cost model must both see the risk actually taken.
    """
    rs, stops = [], []
    for row in df.iter_rows(named=True):
        c = row["entry_fill"] - row["stop"]  # the consolidation range, = shipped planned_risk
        new_stop = row["entry_fill"] - STOP_M * c
        target_price = row["entry_fill"] + TARGET_T * c
        out = replay_bracket(
            paths[row["key"]], row["entry_fill"], new_stop, target_price=target_price
        )
        rs.append(out["r"])
        stops.append(new_stop)
    return df.with_columns(pl.Series("r", rs), pl.Series("stop", stops))


def run(df: pl.DataFrame, paths: dict[str, np.ndarray], config: str) -> dict:
    """One configuration, end to end.

    ⚠️ H2 **replaces** the shipped selection rather than stacking on top of it — agent A's rule
    carries no price band, no `passed`, no staleness cut, because its whole finding is that
    `passed` is worse than random. Stacking the two gives 3 trades in 166 sessions and is not the
    proposal anybody made; the first run of this file got that wrong and the number was discarded.
    """
    sessions = df["dt"].n_unique()
    if config == "H0":
        book = build_book(fixed_target_r(SHIPPED(df), 2.0), max_per_day=2)
        return score(book, sizing=Sizing(position_fraction=0.50), sessions=sessions, by=())
    sel = agent_a_filter(df) if config == "H2" else SHIPPED(df)
    cap = 2 if config == "H1b" else 1
    book = build_book(wide_bracket(sel, paths), max_per_day=cap)
    sizing = Sizing(position_fraction=0.50 if cap == 2 else 0.75)
    return score(book, sizing=sizing, sessions=sessions, by=())


def line(name: str, r: dict) -> str:
    if not r.get("trades"):
        return f"  {name:<26} no trades"
    return (
        f"  {name:<26} {r['trades']:>3} trades ({r['trades_per_session']:.2f}/sess)  "
        f"net {r['net_r']:+6.1f}R ({r['net_r_per_trade']:+.3f}/tr)  "
        f"gross {r['gross_r']:+6.1f}R  win {r['win_rate'] * 100:4.1f}%  "
        f"dd {r['max_dd_net_r']:5.1f}R  ${r['net_usd']:+8.2f}"
    )


def main() -> None:
    panel = load_panel()
    paths = load_paths(panel)
    devval = panel.filter(pl.col("split") != "holdout")
    holdout = panel.filter(pl.col("split") == "holdout")

    # --- gate: reproduce agent C's published DEV+VAL card before trusting the composition -------
    check = score(
        build_book(wide_bracket(SHIPPED(devval), paths), max_per_day=2),
        sessions=devval["dt"].n_unique(),
        by=(),
    )
    ok = check["trades"] == 80 and abs(check["net_r"] - 13.41) < 0.5
    print("agent C bracket reproduction (DEV+VAL, 2/day, 50% cap):")
    print(line("recomputed", check))
    print(
        f"  expected: 80 trades, gross +19.32R, net +13.41R  ->  {'MATCH' if ok else 'MISMATCH'}\n"
    )
    if not ok:
        raise SystemExit("composition does not reproduce agent C's card — stop and investigate")

    print(f"DEV+VAL ({devval['dt'].n_unique()} sessions, fitted here):")
    dv = {c: run(devval, paths, c) for c in CONFIGS}
    for c, r in dv.items():
        print(line(c, r))

    print(f"\nHOLDOUT ({holdout['dt'].n_unique()} live sessions, first and only look):")
    ho = {c: run(holdout, paths, c) for c in CONFIGS}
    for c, r in ho.items():
        print(line(c, r))

    out = LAB_OUT / "synthesis.json"
    payload = {
        "configs": {
            "H0": "shipped selection, 2R/-1R, 2/day, 50% cap",
            "H1": f"shipped selection, stop -{STOP_M}C / target +{TARGET_T}C, 1/day, 75% cap",
            "H1b": "H1 but 2/day at 50% cap",
            "H2": f"agent A rule {RULES_FILTER} REPLACING shipped selection, 1/day",
        },
        "devval": {c: {k: v for k, v in r.items() if k != "_trades"} for c, r in dv.items()},
        "holdout": {c: {k: v for k, v in r.items() if k != "_trades"} for c, r in ho.items()},
    }
    out.write_text(json.dumps(payload, indent=1, default=str))
    print(f"\nwrote {out}")

    for c in ("H1",):
        t = ho[c].get("_trades")
        if t is not None and t.height:
            print(f"\n{c} holdout trades, in order:")
            print(
                t.select(["dt", "symbol", "entry_fill", "stop", "r", "qty", "net_usd"])
                .to_pandas()
                .to_string(index=False)
            )


if __name__ == "__main__":
    main()
