"""The rule-finding *procedure*: greedy forward selection of <=K threshold clauses.

Written as a procedure, not a result, so `common.walk_forward()` can run the whole thing inside
each fold. That is the only honest test available here: "would this method of finding a rule have
made money as the record accumulated?" A rule hand-picked on all of DEV cannot answer it.

Clause menu is deliberately small and prior-directed:
- only the direction step 1 found favourable for a feature (a feature with no prior gets both),
- decile cuts only, so a threshold always has >=10% of the data on either side,
- the two demonstrably-leaky features are excluded (`rules_def.LEAKY`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl
from lab import C
from rules_def import CANDIDATE_FEATURES, SHAPE_GATES


@dataclass(frozen=True)
class Clause:
    col: str
    op: str  # "ge" | "le" | "eq" | "gate"
    cut: float

    def expr(self) -> pl.Expr:
        if self.op == "ge":
            return pl.col(self.col) >= self.cut
        if self.op == "le":
            return pl.col(self.col) <= self.cut
        if self.op == "eq":
            return pl.col(self.col) == bool(self.cut)
        if self.op == "gate":
            return ~pl.col("failing_gates").str.contains(self.col, literal=True)
        raise ValueError(self.op)

    def __str__(self) -> str:
        sym = {"ge": ">=", "le": "<=", "eq": "==", "gate": "passes"}[self.op]
        return f"{self.col} {sym} {self.cut:g}" if self.op != "gate" else f"gate:{self.col}"


def clause_menu(
    train: pl.DataFrame, *, deciles: Sequence[float] = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
) -> list[Clause]:
    out: list[Clause] = []
    for col, direction in CANDIDATE_FEATURES.items():
        x = train[col].drop_nulls().cast(pl.Float64).to_numpy()
        if len(x) < 300:
            continue
        for q in deciles:
            c = float(np.quantile(x, q))
            if direction >= 0:
                out.append(Clause(col, "ge", round(c, 5)))
            if direction <= 0:
                out.append(Clause(col, "le", round(c, 5)))
    out += [Clause("cons_vol_reducing", "eq", 1.0), Clause("pole_has_big_green", "eq", 1.0)]
    out += [Clause(g, "gate", 1.0) for g in SHAPE_GATES]
    # dedupe (a degenerate distribution can repeat a cut)
    seen, uniq = set(), []
    for cl in out:
        k = (cl.col, cl.op, cl.cut)
        if k not in seen:
            seen.add(k)
            uniq.append(cl)
    return uniq


def selector(clauses: Sequence[Clause]) -> Callable[[pl.DataFrame], pl.DataFrame]:
    def f(df: pl.DataFrame) -> pl.DataFrame:
        if not clauses:
            return df
        e = clauses[0].expr()
        for cl in clauses[1:]:
            e = e & cl.expr()
        return df.filter(e.fill_null(False))

    return f


def objective(
    df: pl.DataFrame,
    clauses: Sequence[Clause],
    *,
    min_tps: float,
    max_per_day: int,
    r_col: str = "net_r",
) -> tuple[float, int]:
    """Mean `r_col` of the booked trades, or -inf if the rule trades too rarely."""
    sel = selector(clauses)(df)
    if sel.is_empty():
        return -np.inf, 0
    book = C.build_book(sel, max_per_day=max_per_day)
    n_sessions = df["dt"].n_unique()
    if n_sessions == 0 or book.height / n_sessions < min_tps or book.height < 25:
        return -np.inf, book.height
    return float(book[r_col].mean()), book.height


def greedy(
    train: pl.DataFrame,
    *,
    max_clauses: int = 4,
    min_tps: float = 0.5,
    max_per_day: int = 2,
    r_col: str = "net_r",
    menu: Sequence[Clause] | None = None,
    verbose: bool = False,
) -> list[Clause]:
    """Forward-select clauses, each time taking the one that most improves booked mean R."""
    pool = list(menu) if menu is not None else clause_menu(train)
    chosen: list[Clause] = []
    best, _ = objective(train, chosen, min_tps=min_tps, max_per_day=max_per_day, r_col=r_col)
    for _ in range(max_clauses):
        cand_best, cand_cl = best, None
        for cl in pool:
            if any(c.col == cl.col and c.op == cl.op for c in chosen):
                continue
            v, _n = objective(
                train, [*chosen, cl], min_tps=min_tps, max_per_day=max_per_day, r_col=r_col
            )
            if v > cand_best + 1e-9:
                cand_best, cand_cl = v, cl
        if cand_cl is None:
            break
        chosen.append(cand_cl)
        best = cand_best
        if verbose:
            print(f"    + {cand_cl!s:<40} -> {best:+.4f}")
    return chosen


def make_fit(**kw: object) -> Callable[[pl.DataFrame], Callable[[pl.DataFrame], pl.DataFrame]]:
    """A `fit` for `common.walk_forward()`: runs the whole greedy search on the training block."""

    def fit(train: pl.DataFrame) -> Callable[[pl.DataFrame], pl.DataFrame]:
        return selector(greedy(train, **kw))  # type: ignore[arg-type]

    return fit
