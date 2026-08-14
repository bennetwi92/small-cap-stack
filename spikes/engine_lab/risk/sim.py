"""Agent B (risk) — the sizing / cost / capacity / loss-limit simulator.

This does NOT fork `common.py`. It reuses `common.Costs`, `common.Sizing`'s formula,
`common.build_book`'s time-ordering rule and `common.fixed_target_r`, and adds the things a
risk study needs that `score()` cannot express:

- a **day-open** equity curve (score()'s `compound=True` updates equity trade-by-trade in trigger
  order, which is mild lookahead: trade 2 of the day triggers at 07:15 and cannot know trade 1's
  outcome, which usually resolves after 09:30);
- **cost-drag exclusions** decided at entry from (entry, stop, equity) only;
- **capacity** and a one-per-symbol toggle;
- a **daily loss limit** that counts only losses whose stop was actually hit *before* the
  candidate's trigger bar (measured from the bars, not assumed);
- a **risk ladder** keyed on completed prior *days*, never on today.

`verify()` proves the money math is identical to `common.score()` under the default config.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine_lab import common as C  # noqa: E402

OUT = C.REPO / "data/spikes/engine-lab/risk"
BAR_MIN = 5.0  # bars are 5-minute


# ---------------------------------------------------------------------------------------------
# Population — DEV+VAL only. HOLDOUT is never loaded, never scored, never printed.
# ---------------------------------------------------------------------------------------------
def load_work(*, target: float = 2.0) -> pl.DataFrame:
    """The working population: pre-market rows on DEV+VAL, R booked at a fixed target.

    Adds `exit_et_min` (when the bracket actually resolved, from the bars) so a daily loss limit
    can ask "had this loss happened yet?" instead of assuming it had.
    """
    df = C.load_panel().filter(pl.col("split") != "holdout")
    df = C.fixed_target_r(df, target)
    paths = C.load_paths(C.load_panel())
    ex: list[float] = []
    for row in df.iter_rows(named=True):
        arr = paths.get(row["key"])
        if arr is None:
            ex.append(float("inf"))
            continue
        rep = C.replay_bracket(arr, row["entry_fill"], row["stop"], target_r=target)
        ex.append(float(row["trigger_et_min"]) + BAR_MIN * float(rep["bars_held"]))
    return df.with_columns(pl.Series("exit_et_min", ex))


def selection_all(df: pl.DataFrame) -> pl.DataFrame:
    """The loose selection: the whole pre-market pool, no shape gate, no price band."""
    return df


def selection_passed(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("passed"))


SELECTIONS: dict[str, Any] = {
    "shipped": C.SHIPPED,
    "pool": selection_all,
    "passed": selection_passed,
}


# ---------------------------------------------------------------------------------------------
# The configuration this agent owns
# ---------------------------------------------------------------------------------------------
DEFAULT_COSTS = C.Costs()


@dataclass(frozen=True)
class RiskConfig:
    equity: float = 500.0
    risk_fraction: float = 0.05
    position_fraction: float = 0.50
    compound: bool = False

    # capacity
    max_per_day: int = 2
    one_per_symbol: bool = False

    # cost-drag exclusions (all decidable at entry from entry, stop and current equity)
    max_cost_r: float = 1e9  # skip if the round-trip cost of a LOSER exceeds this fraction of R
    min_risk_usd: float = 0.0  # deployed dollar risk, qty * (entry - stop)
    min_stop_usd: float = 0.0  # per-share stop distance, entry - stop
    min_qty: int = 0
    max_entry: float = 1e9

    # loss limits
    daily_loss_limit: int | None = None  # stop for the day after N *resolved* losers
    ladder: Sequence[tuple[int, float]] = field(default_factory=tuple)  # (streak>=, risk mult)

    def risk_mult(self, streak: int) -> float:
        m = 1.0
        for lo, mult in self.ladder:
            if streak >= lo:
                m = mult
        return m

    def qty(self, entry: float, stop: float, equity: float, mult: float = 1.0) -> tuple[int, str]:
        rps = entry - stop
        if rps <= 0 or entry <= 0:
            return 0, "invalid"
        risk_qty = int(equity * self.risk_fraction * mult // rps)
        cap_qty = int(equity * self.position_fraction // entry)
        return (risk_qty, "risk") if risk_qty <= cap_qty else (cap_qty, "cap")

    def worst_case_cost_r(
        self, entry: float, stop: float, qty: int, costs: C.Costs = DEFAULT_COSTS
    ) -> float:
        """Round-trip cost as a fraction of deployed risk, ASSUMING THE TRADE LOSES.

        Deterministic at entry — it reads no outcome, only (entry, stop, qty). The loser case is
        used because that is when the slippage term applies, so it is the honest worst case and it
        needs no assumed win rate (which would be a fitted parameter).
        """
        rps = entry - stop
        if qty <= 0 or rps <= 0:
            return float("inf")
        fees, slip = costs.usd(qty, stop, won=False)
        return (fees + slip) / (qty * rps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "equity": self.equity,
            "risk_fraction": self.risk_fraction,
            "position_fraction": self.position_fraction,
            "compound": self.compound,
            "max_cost_r": None if self.max_cost_r >= 1e9 else self.max_cost_r,
            "max_per_day": self.max_per_day,
            "one_per_symbol": self.one_per_symbol,
            "min_risk_usd": self.min_risk_usd,
            "min_stop_usd": self.min_stop_usd,
            "min_qty": self.min_qty,
            "max_entry": None if self.max_entry >= 1e9 else self.max_entry,
            "daily_loss_limit": self.daily_loss_limit,
            "ladder": [list(x) for x in self.ladder],
        }


# ---------------------------------------------------------------------------------------------
# The simulator
# ---------------------------------------------------------------------------------------------
def simulate(
    selected: pl.DataFrame,
    cfg: RiskConfig | None = None,
    *,
    costs: C.Costs = DEFAULT_COSTS,
    r_col: str = "r",
) -> pl.DataFrame:
    """Walk the selected rows in strict date-then-trigger-time order and book the account.

    Order of operations per candidate: cost exclusions -> capacity -> loss limit -> size -> cost.
    Exclusions come first because they are a *filter*: an excluded name never occupied a slot.
    """
    cfg = cfg or RiskConfig()
    d = selected.sort(["dt", "trigger_et_min", "symbol"])
    equity = cfg.equity
    streak = 0  # consecutive losing trades over COMPLETED prior days
    out: list[dict[str, Any]] = []
    for (day,), grp in d.group_by(["dt"], maintain_order=True):
        day_equity = equity
        mult = cfg.risk_mult(streak)
        taken = 0
        syms: set[str] = set()
        resolved_losses: list[float] = []  # exit_et_min of losers booked today
        day_rows: list[dict[str, Any]] = []
        for t in grp.iter_rows(named=True):
            entry, stop, r = float(t["entry_fill"]), float(t["stop"]), float(t[r_col])
            rps = entry - stop
            qty, sized_by = cfg.qty(entry, stop, day_equity, mult)
            risk_usd = qty * rps
            # --- cost-drag exclusions (entry-time information only) -------------------------
            if (
                qty < max(1, cfg.min_qty)
                or rps < cfg.min_stop_usd
                or risk_usd < cfg.min_risk_usd
                or entry > cfg.max_entry
                or cfg.worst_case_cost_r(entry, stop, qty, costs) > cfg.max_cost_r
            ):
                continue
            # --- capacity -------------------------------------------------------------------
            if taken >= cfg.max_per_day:
                continue
            if cfg.one_per_symbol and t["symbol"] in syms:
                continue
            # --- daily loss limit: only losses already RESOLVED at this trigger count --------
            if cfg.daily_loss_limit is not None:
                known = sum(1 for x in resolved_losses if x <= float(t["trigger_et_min"]))
                if known >= cfg.daily_loss_limit:
                    continue
            # --- book it --------------------------------------------------------------------
            gross_usd = r * risk_usd
            exit_price = entry + r * rps
            fees, slip = costs.usd(qty, exit_price, won=r > 0)
            net_usd = gross_usd - fees - slip
            taken += 1
            syms.add(t["symbol"])
            if r <= 0:
                resolved_losses.append(float(t.get("exit_et_min", float("inf"))))
            day_rows.append(
                {
                    **{k: t[k] for k in _KEEP if k in t},
                    "r": r,
                    "qty": qty,
                    "sized_by": sized_by,
                    "risk_usd": risk_usd,
                    "gross_usd": gross_usd,
                    "cost_usd": fees + slip,
                    "fees_usd": fees,
                    "slip_usd": slip,
                    "net_usd": net_usd,
                    "net_r": net_usd / risk_usd if risk_usd else 0.0,
                    "cost_r": (fees + slip) / risk_usd if risk_usd else 0.0,
                    "risk_mult": mult,
                    "equity_before": day_equity,
                }
            )
        if day_rows:
            if cfg.compound:
                equity = day_equity + sum(x["net_usd"] for x in day_rows)
            for x in day_rows:
                streak = 0 if x["r"] > 0 else streak + 1
            _ = day
        out.extend(day_rows)
    if not out:
        return pl.DataFrame()
    return pl.DataFrame(out, infer_schema_length=None)


_KEEP = (
    "dt",
    "split",
    "source",
    "symbol",
    "trigger_et_min",
    "entry_fill",
    "stop",
    "stop_pct",
    "planned_risk",
    "max_r",
    "passed",
    "cycle_num",
    "exit_et_min",
)


def report(sim: pl.DataFrame, *, sessions: int, label: str = "") -> dict[str, Any]:
    """Scorecard. Net USD is the objective; net R is reported because it is comparable."""
    if sim.is_empty():
        return {"label": label, "trades": 0, "net_usd": 0.0, "net_r": 0.0}
    net_r = sim["net_r"].to_numpy()
    net_usd = sim["net_usd"].to_numpy()
    cum, cum_u = np.cumsum(net_r), np.cumsum(net_usd)
    return {
        "label": label,
        "trades": sim.height,
        "sessions_traded": sim["dt"].n_unique(),
        "sessions_available": sessions,
        "trades_per_session": round(sim.height / sessions, 3),
        "gross_r": round(float(sim["r"].sum()), 2),
        "net_r": round(float(net_r.sum()), 2),
        "net_r_per_trade": round(float(net_r.mean()), 4),
        "gross_usd": round(float(sim["gross_usd"].sum()), 2),
        "net_usd": round(float(net_usd.sum()), 2),
        "net_usd_per_trade": round(float(net_usd.mean()), 3),
        "cost_usd": round(float(sim["cost_usd"].sum()), 2),
        "cost_r_per_trade": round(float(sim["cost_r"].mean()), 4),
        "win_rate": round(float((sim["r"] > 0).mean()), 4),
        "mean_qty": round(float(sim["qty"].mean()), 1),
        "mean_risk_usd": round(float(sim["risk_usd"].mean()), 2),
        "cap_bound": int((sim["sized_by"] == "cap").sum()),
        "cap_bound_pct": round(float((sim["sized_by"] == "cap").mean()), 3),
        "max_dd_net_r": round(float((cum - np.maximum.accumulate(cum)).min()), 2),
        "max_dd_net_usd": round(float((cum_u - np.maximum.accumulate(cum_u)).min()), 2),
    }


def by_split(sim: pl.DataFrame, work: pl.DataFrame) -> dict[str, dict[str, Any]]:
    res = {}
    for sp in ("dev", "val"):
        s = sim.filter(pl.col("split") == sp) if not sim.is_empty() else sim
        n = work.filter(pl.col("split") == sp)["dt"].n_unique()
        res[sp] = report(s, sessions=n, label=sp)
    return res


def line(res: dict[str, Any]) -> str:
    return (
        f"{res['trades']:>4} tr ({res.get('trades_per_session', 0):.2f}/s)  "
        f"net {res['net_r']:+7.1f}R ({res['net_r_per_trade']:+.3f})  "
        f"${res['net_usd']:+8.2f} (${res['net_usd_per_trade']:+.3f}/tr)  "
        f"gross {res['gross_r']:+7.1f}R  win {res['win_rate'] * 100:4.1f}%  "
        f"drag {res['cost_r_per_trade'] * 100:4.1f}%  cap {res['cap_bound_pct'] * 100:3.0f}%  "
        f"ddR {res['max_dd_net_r']:6.1f}"
    )


def run(
    work: pl.DataFrame,
    cfg: RiskConfig | None = None,
    *,
    selection: Any = C.SHIPPED,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    cfg = cfg or RiskConfig()
    sel = selection(work)
    sim = simulate(sel, cfg)
    res = report(sim, sessions=work["dt"].n_unique(), label="all")
    res["splits"] = by_split(sim, work)
    return sim, res


# ---------------------------------------------------------------------------------------------
# Proof the money math is common.score()'s money math
# ---------------------------------------------------------------------------------------------
def verify() -> dict[str, Any]:
    """Default config on SHIPPED must reproduce `common.score()` trade for trade."""
    panel = C.load_panel()
    book = C.build_book(C.fixed_target_r(C.SHIPPED(panel)), max_per_day=2)
    ref = C.score(book, sessions=panel["dt"].n_unique())["_trades"]
    work = C.fixed_target_r(panel).with_columns(pl.lit(0.0).alias("exit_et_min"))
    mine = simulate(C.SHIPPED(work), RiskConfig())
    a = ref.sort(["dt", "trigger_et_min", "symbol"]).select(["qty", "net_usd", "net_r"]).to_numpy()
    b = mine.sort(["dt", "trigger_et_min", "symbol"]).select(["qty", "net_usd", "net_r"]).to_numpy()
    return {
        "ref_trades": ref.height,
        "sim_trades": mine.height,
        "shape_match": a.shape == b.shape,
        "max_abs_diff": float(np.abs(a - b).max()) if a.shape == b.shape else None,
    }


if __name__ == "__main__":
    print("verify vs common.score():", verify())
    w = load_work()
    print(f"working population (DEV+VAL): {w.height} rows, {w['dt'].n_unique()} sessions")
    for name, sel in SELECTIONS.items():
        _, r = run(w, RiskConfig(), selection=sel)
        print(f"{name:<8} " + line(r))
        for sp, b in r["splits"].items():
            if b["trades"]:
                print(f"  {sp:<6} " + line(b))
