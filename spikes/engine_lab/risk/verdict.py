"""Agent B — the final checks and the machine-readable result.

Everything here runs on DEV+VAL only.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim as S  # noqa: E402
import study as T  # noqa: E402

#: The primary proposal. One trade a day frees the notional cap to rise inside the UK cash
#: account's settled-cash invariant (position_fraction x max_per_day <= 1.0), so the intended 5%
#: risk actually gets deployed - the shipped 50% cap deploys it on only 30% of trades.
#: 0.75 rather than the arithmetic optimum 1.00: the cap sweep is a plateau from 0.75 up
#: ($113 / $122 / $131 at 0.75 / 0.90 / 1.00, WF 5/6 blocks at all three), so 0.75 sits in the
#: middle of it instead of on the edge, and leaves a quarter of the account uncommitted against
#: the halt/gap risk that a stop-based R model does not price.
PROPOSED = S.RiskConfig(
    equity=500.0,
    risk_fraction=0.05,
    position_fraction=0.75,
    max_per_day=1,
    max_cost_r=1e9,
    one_per_symbol=False,
    daily_loss_limit=None,
    ladder=(),
)

#: The same with the cost guardrail. Roughly a wash on the shipped selection (whose $3 price floor
#: and 2.5% stop floor already remove most high-drag names) and worth ~$650 over 166 sessions on
#: the raw pool. Required if selection loosens; optional if it does not.
GUARDED = replace(PROPOSED, max_cost_r=0.10)

#: If the trader wants two slots a day, the notional cap must halve to stay settled - and then the
#: guardrail stops being optional.
TWO_SLOT = S.RiskConfig(max_per_day=2, position_fraction=0.50, max_cost_r=0.10)


CONFIGS: list[tuple[str, S.RiskConfig]] = [
    ("shipped baseline: 2/day cap50", T.BASE),
    ("PROPOSED 1/day cap75", PROPOSED),
    ("GUARDED 1/day cap75 + cost<=10%", GUARDED),
    ("TWO_SLOT 2/day cap50 + cost<=10%", TWO_SLOT),
]


def stability(work: pl.DataFrame, cfgs: list[tuple[str, S.RiskConfig]]) -> None:
    T.hdr("F. STABILITY — DEV/VAL and odd/even session split for each candidate config")
    print("   (recon-vs-live is unavailable: DEV+VAL is 100% recon, live IS the holdout)")
    dates = sorted(work["dt"].unique().to_list())
    idx = {d: i % 2 for i, d in enumerate(dates)}
    for sel in ("shipped", "pool"):
        print(f"\n  -- selection = {sel} --")
        for nm, cfg in cfgs:
            x = S.simulate(S.SELECTIONS[sel](work), cfg)
            if x.is_empty():
                continue
            x = x.with_columns(pl.col("dt").replace_strict(idx).alias("par"))
            parts = {
                "dev": x.filter(pl.col("split") == "dev"),
                "val": x.filter(pl.col("split") == "val"),
                "odd": x.filter(pl.col("par") == 1),
                "even": x.filter(pl.col("par") == 0),
            }
            bits = "  ".join(
                f"{k}:{p.height:>3}/${float(p['net_usd'].sum()):+8.2f}" for k, p in parts.items()
            )
            print(
                f"    {nm:<34} n={x.height:>3} ${float(x['net_usd'].sum()):+8.2f} | {bits}  "
                f"| signs {sum(1 for p in parts.values() if float(p['net_usd'].sum()) > 0)}/4"
            )


def wf_capacity(work: pl.DataFrame) -> dict[str, Any]:
    T.hdr("G. WALK-FORWARD on the capacity/cap choice (no threshold fitted at all)")
    out = {}
    for sel in ("shipped", "pool"):
        f = S.SELECTIONS[sel]
        print(f"\n  -- selection = {sel} --")
        for nm, cfg in CONFIGS:
            wf = T.walk_forward_risk(work, f, fixed=cfg.max_cost_r, cfg=cfg)
            print(
                f"    {nm:<26} {wf['blocks_positive_usd']}/{wf['n_blocks']} blocks +$  "
                f"${wf['total_net_usd']:+9.2f}  {wf['total_net_r']:+7.2f}R  "
                f"{wf['total_trades']:>4} trades"
            )
            out[f"{sel}|{nm}"] = wf
    return out


def cost_ledger(work: pl.DataFrame) -> None:
    T.hdr("H. THE COST LEDGER — where the money actually goes, in dollars")
    for sel in ("shipped", "pool"):
        for nm, cfg in CONFIGS:
            x = S.simulate(S.SELECTIONS[sel](work), cfg)
            if x.is_empty():
                continue
            gross = float(x["gross_usd"].sum())
            share = float(x["cost_usd"].sum()) / max(abs(gross), 1e-9)
            print(
                f"  {sel:<8} {nm:<34} n={x.height:>3}  gross ${gross:+9.2f}  "
                f"fees ${float(x['fees_usd'].sum()):8.2f}  "
                f"slip ${float(x['slip_usd'].sum()):8.2f}  "
                f"=> net ${float(x['net_usd'].sum()):+9.2f}   "
                f"(costs are {share:.0%} of |gross|)"
            )


def scorecard(work: pl.DataFrame, cfg: S.RiskConfig, sel: str) -> dict[str, Any]:
    x = S.simulate(S.SELECTIONS[sel](work), cfg)
    r = S.report(x, sessions=work["dt"].n_unique())
    r["splits"] = S.by_split(x, work)
    r.pop("label", None)
    return r


def compounded(work: pl.DataFrame, cfg: S.RiskConfig, sel: str) -> dict[str, Any]:
    """Day-open compounding on $500 — the number a trader actually feels."""
    x = S.simulate(S.SELECTIONS[sel](work), replace(cfg, compound=True))
    if x.is_empty():
        return {}
    eq = x["equity_before"].to_numpy()
    final = float(eq[-1] + x["net_usd"][-1])
    peak = np.maximum.accumulate(np.append(eq, final))
    return {
        "final_equity": round(final, 2),
        "return_pct": round(final / cfg.equity - 1, 4),
        "peak": round(float(eq.max()), 2),
        "trough": round(float(eq.min()), 2),
        "max_dd_pct": round(float(((peak - np.append(eq, final)) / peak).max()), 4),
        "trades": x.height,
    }


def main() -> None:
    work = S.load_work()
    stability(work, CONFIGS)
    wf = wf_capacity(work)
    cost_ledger(work)

    T.hdr("I. THE CANDIDATE CONFIGS — scorecards and compounded $500 curves")
    for nm, cfg in CONFIGS:
        for sel in ("shipped", "pool"):
            r = scorecard(work, cfg, sel)
            print(f"  {nm:<34} {sel:<8} " + S.line(r))
            for sp, b in r["splits"].items():
                if b["trades"]:
                    print(f"      {sp:<38} " + S.line(b))
        c = compounded(work, cfg, "shipped")
        print(
            f"      compounded $500 -> ${c['final_equity']:.2f} ({c['return_pct']:+.1%}), "
            f"max drawdown {c['max_dd_pct']:.0%} over {c['trades']} trades\n"
        )

    eq_rows = []
    for eq in (250, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000, 25000, 1000000):
        r = scorecard(work, replace(PROPOSED, equity=float(eq)), "shipped")
        eq_rows.append(
            {
                "equity": eq,
                "trades": r["trades"],
                "net_usd": r["net_usd"],
                "net_r_per_trade": r["net_r_per_trade"],
                "cost_r_per_trade": r["cost_r_per_trade"],
                "cap_bound_pct": r["cap_bound_pct"],
                "mean_risk_usd": r["mean_risk_usd"],
            }
        )

    def card(cfg: S.RiskConfig) -> dict[str, Any]:
        d: dict[str, Any] = {"config": cfg.as_dict()}
        for sel in ("shipped", "pool"):
            sc = scorecard(work, cfg, sel)
            sc.pop("_trades", None)
            d[sel] = sc
        d["compounded_500_shipped"] = compounded(work, cfg, "shipped")
        return d

    out: dict[str, Any] = {
        "agent": "B / risk",
        "population": {
            "rows": work.height,
            "sessions": work["dt"].n_unique(),
            "splits": "DEV 2025-10-30..2026-04-30 (125 sessions), VAL 2026-05-01..2026-06-30 "
            "(41 sessions). HOLDOUT was never loaded.",
            "source_note": "DEV+VAL is 100% recon; live == holdout, so a recon-vs-live split is "
            "impossible here. Substituted DEV/VAL and odd/even session splits.",
        },
        "proposed": PROPOSED.as_dict(),
        "constraint": {
            "settled_cash_invariant": "position_fraction * max_per_day <= 1.0 "
            "(UK cash account, decisions.md D-15 / config.py:498)",
            "proposed_value": PROPOSED.position_fraction * PROPOSED.max_per_day,
        },
        "complexity_budget": {
            "thresholds_used": 2,
            "detail": [
                "risk_fraction 0.05 - UNCHANGED, user-fixed, not a free parameter",
                "max_per_day 1 - fitted (was 2); costs one degree of freedom",
                "position_fraction 1.00 - NOT free: forced to 1/max_per_day by the settled-cash "
                "invariant, so it is determined by max_per_day",
                "max_cost_r 0.10 - optional guardrail, one further threshold; recommended only if "
                "selection loosens",
            ],
        },
        "cost_model": {
            "drag_formula": "cost_R = 0.70/risk_usd + (0.0064 + 0.02_if_loser)/(entry-stop)",
            "fixed_part": "the $0.35/side commission minimum - dies as the account grows",
            "variable_part": "2-tick stop slippage per share - NEVER dies, it is proportional",
            "shipped_selection_drag_at_500": 0.102,
            "shipped_selection_drag_floor_infinite_equity": 0.063,
            "shipped_selection_gross_edge_r_per_trade": 0.087,
            "pool_drag_at_500": 0.242,
            "pool_drag_floor_infinite_equity": 0.211,
        },
        "cards": {nm: card(cfg) for nm, cfg in CONFIGS},
        "walk_forward": wf,
        "equity_curve_shipped_proposed": eq_rows,
        "rejected": {
            "daily_loss_limit": "NO - touches 7 of 80 shipped trades; per-trade quality unchanged "
            "on the pool (-0.525 vs -0.510 net R); a no-op at 1 trade/day.",
            "risk_ladder": "NO - a ladder cannot change R expectancy at all, only the dollar "
            "weighting of trades already taken. On a ~0R book that is noise, and on "
            "this record it is negative noise (-$20 to -$35 on shipped).",
            "one_per_symbol": "NO - the book took the same ticker twice on 1 day "
            "(shipped) / 3 days (pool). No measurable effect; do not add the rule.",
            "capacity_3_4_6_unlimited": "NO - monotonically worse on both selections, at the "
            "settled-cash-legal cap and at a fixed 50% cap.",
            "risk_fraction_above_5pct": "NO effect once the notional cap binds; at cap 100% it "
            "raises dollar volatility for no measured gain.",
            "position_fraction_below_50pct": "NO - cap 25% drives 94-100% of trades cap-bound and "
            "lifts drag to 15.4%.",
        },
        "cross_dependencies": [
            "The cost guardrail's value is CONDITIONAL ON SELECTION (agent A). On the shipped "
            "selection its $3 price floor and 2.5% stop floor already remove most high-drag names, "
            "so the guardrail is roughly a wash. On the raw pool it is worth ~$650 over 166 "
            "sessions. If A loosens price or stop-width, the guardrail becomes mandatory.",
            "The whole cost picture depends on the EXIT (agent C). A 2-tick stop slip is charged "
            "once per loser; a wider stop cuts the slip's share of R proportionally, and a target "
            "that trades more often multiplies the fixed commission. Any exit change re-prices "
            "everything here.",
            "At 1 trade/day a cost filter SUBSTITUTES rather than removes: the skipped name frees "
            "the slot for the next trigger. That is why the guardrail's effect is non-monotone at "
            "1/day and cleaner at 2/day.",
        ],
    }
    S.OUT.mkdir(parents=True, exist_ok=True)
    p = S.OUT / "result.json"
    p.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
