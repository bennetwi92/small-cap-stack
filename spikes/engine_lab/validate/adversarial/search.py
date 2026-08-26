"""A numpy re-implementation of *the search that could have produced the in-play rule*.

The point is not to find a better rule. It is to have a **procedure** I can (a) refit inside a
walk-forward and (b) run thousands of times on scrambled outcomes, so I can ask the only question
that matters: does a search like this invent +0.478R/trade rules out of noise?

Semantics are identical to the shared harness (verified: `lab.verify_fast`, and
`Pop.book_stats` is checked against `common.score` in step2).
"""

from __future__ import annotations

from dataclasses import dataclass

import lab as L
import numpy as np
import polars as pl
from lab import C

# Numeric features a search may cut on: every numeric column in TRIGGER_TIME_SAFE, minus the two
# that `rules/rules_def.py` demonstrated are leaky in practice (first_rank, n_scanner_hits — both
# already excluded here because they are not in this list).
NUMERIC_FEATURES = [
    "first_hit_et_min",
    "entry_fill",
    "breakout_level",
    "stop",
    "planned_risk",
    "stop_pct",
    "pole_len",
    "cons_len",
    "retracement",
    "cycle_num",
    "untraded_cons_bars",
    "float_shares",
    "short_percent",
    "shares_outstanding",
    "trigger_et_min",
    "staleness_delay_min",
    "pole_pct",
    "pole_volume",
    "day_open",
    "ext_at_peak",
    "ext_at_trigger",
    "bars_before_pole",
    "runup_pre_appearance",
    "rvol_pole",
    "vol_share_pole",
    "range_before_pole_pct",
    "cum_volume_to_trigger",
    "cum_dollar_vol_to_trigger",
    "hits_before_trigger",
]
BOOL_FEATURES = ["cons_vol_reducing", "pole_has_big_green", "halted_consolidation", "passed"]

DECILES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


@dataclass(frozen=True)
class Clause:
    col: str
    op: str  # "ge" | "le" | "eq" | "notnull"
    cut: float

    def __str__(self) -> str:
        if self.op == "notnull":
            return f"{self.col} is not null"
        if self.op == "eq":
            return f"{self.col} == {bool(self.cut)}"
        return f"{self.col} {'>=' if self.op == 'ge' else '<='} {self.cut:g}"


class Pop:
    """A population as numpy arrays, with vectorised book-building and net-R scoring."""

    def __init__(self, df: pl.DataFrame, *, target: float = 2.0, max_per_day: int = 2):
        d = df.sort(["dt", "trigger_et_min", "symbol"])
        self.df = d
        self.n = d.height
        self.target = target
        self.max_per_day = max_per_day
        dts = d["dt"].to_numpy()
        _, self.day_idx = np.unique(dts, return_inverse=True)
        self.n_sessions = int(self.day_idx.max()) + 1 if self.n else 0
        self.entry = d["entry_fill"].to_numpy().astype(np.float64)
        self.stop = d["stop"].to_numpy().astype(np.float64)
        self.max_r = d["max_r"].to_numpy().astype(np.float64)
        self.symbol = d["symbol"].to_numpy()
        self.feat: dict[str, np.ndarray] = {}
        for c in NUMERIC_FEATURES:
            if c in d.columns:
                self.feat[c] = d[c].cast(pl.Float64).to_numpy().astype(np.float64)
        for c in BOOL_FEATURES:
            if c in d.columns:
                self.feat[c] = d[c].cast(pl.Float64).to_numpy().astype(np.float64)

    # -------------------------------------------------------------------------------- masks
    def clause_mask(self, cl: Clause) -> np.ndarray:
        x = self.feat[cl.col]
        if cl.op == "notnull":
            return ~np.isnan(x)
        with np.errstate(invalid="ignore"):
            if cl.op == "ge":
                m = x >= cl.cut
            elif cl.op == "le":
                m = x <= cl.cut
            else:
                m = x == cl.cut
        return np.where(np.isnan(x), False, m)

    def menu(self, base: np.ndarray) -> list[Clause]:
        """Decile cuts of each feature, measured on the rows still alive under `base`."""
        out: list[Clause] = []
        for col in self.feat:
            x = self.feat[col][base]
            x = x[~np.isnan(x)]
            if len(x) < 20:
                continue
            if col in BOOL_FEATURES:
                if len(np.unique(x)) > 1:
                    out += [Clause(col, "eq", 1.0), Clause(col, "eq", 0.0)]
                continue
            for q in DECILES:
                c = float(np.quantile(x, q))
                out += [Clause(col, "ge", c), Clause(col, "le", c)]
        seen, uniq = set(), []
        for cl in out:
            k = (cl.col, cl.op, round(cl.cut, 8))
            if k not in seen:
                seen.add(k)
                uniq.append(cl)
        return uniq

    # -------------------------------------------------------------------------------- booking
    def book_mask(self, mask: np.ndarray) -> np.ndarray:
        """Earliest `max_per_day` selected rows of each day. Rows are already in time order."""
        idx = np.flatnonzero(mask)
        if not len(idx):
            return idx
        days = self.day_idx[idx]
        newday = np.empty(len(days), dtype=bool)
        newday[0] = True
        newday[1:] = days[1:] != days[:-1]
        start = np.maximum.accumulate(np.where(newday, np.arange(len(days)), 0))
        rank = np.arange(len(days)) - start
        return idx[rank < self.max_per_day]

    def net_r(self, idx: np.ndarray, max_r: np.ndarray | None = None) -> np.ndarray:
        mr = self.max_r if max_r is None else max_r
        r = np.where(mr[idx] >= self.target, self.target, -1.0)
        out = L.fast_net_r(self.entry[idx], self.stop[idx], r)
        return out[~np.isnan(out)]

    def stats(self, mask: np.ndarray, max_r: np.ndarray | None = None) -> tuple[float, int, float]:
        """(mean net R per trade, trades, total net R) of the booked subset."""
        idx = self.book_mask(mask)
        if not len(idx):
            return -np.inf, 0, 0.0
        v = self.net_r(idx, max_r)
        if not len(v):
            return -np.inf, 0, 0.0
        return float(v.mean()), int(len(v)), float(v.sum())


def greedy(
    pop: Pop,
    base: np.ndarray,
    *,
    menu: list[Clause] | None = None,
    max_clauses: int = 3,
    min_trades: int = 25,
    max_r: np.ndarray | None = None,
) -> tuple[list[Clause], float, int]:
    """Forward-select up to `max_clauses` clauses, maximising booked mean net R."""
    pool = pop.menu(base) if menu is None else menu
    cur = base.copy()
    chosen: list[Clause] = []
    best, n, _ = pop.stats(cur, max_r)
    if n < min_trades:
        best = -np.inf
    for _ in range(max_clauses):
        cand_v, cand_cl, cand_n = best, None, n
        for cl in pool:
            if any(c.col == cl.col and c.op == cl.op for c in chosen):
                continue
            m = cur & pop.clause_mask(cl)
            v, k, _t = pop.stats(m, max_r)
            if k < min_trades:
                continue
            if v > cand_v + 1e-9:
                cand_v, cand_cl, cand_n = v, cl, k
        if cand_cl is None:
            break
        chosen.append(cand_cl)
        cur &= pop.clause_mask(cand_cl)
        best, n = cand_v, cand_n
    return chosen, best, n


def shipped_mask(df: pl.DataFrame) -> np.ndarray:
    d = df.sort(["dt", "trigger_et_min", "symbol"])
    keys = set(C.SHIPPED(d)["key"].to_list())
    return np.array([k in keys for k in d["key"].to_list()])
