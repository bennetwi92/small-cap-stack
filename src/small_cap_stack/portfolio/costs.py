"""Sizing and IBKR cost model for the virtual book (#232, #237).

Risk-based whole-share sizing capped by notional, plus the tiered commission + pass-through fees.
Split out of the old single-file ``portfolio.py`` (#259) with no behaviour change.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings


@dataclass(frozen=True)
class SizedPosition:
    """A sized position plus *why* it came out that size (#286).

    ``qty = min(risk_qty, cap_qty)`` throws away which constraint actually bound, and that answer is
    the difference between "this trade risked what I asked it to" and "this trade risked a fifth of
    that because the stop was tight". ``sized_by`` names the binding one and ``risk_usd`` /
    ``risk_pct`` report what the position *actually* puts at risk, which is what the page shows —
    never the configured ``risk_fraction``, which is only the ceiling."""

    qty: int
    risk_qty: int  # what the risk budget alone would have bought
    cap_qty: int  # what the notional cap alone would have allowed
    sized_by: str  # "risk" | "cap" — the constraint that bound (see below for the tie)
    risk_usd: float  # qty × (entry − stop): the dollars actually at risk
    risk_pct: float  # risk_usd / equity — the *realised* fraction, ≤ the configured risk_fraction


def size_position(
    equity: float,
    entry_price: float,
    stop: float,
    *,
    risk_fraction: float,
    max_position_fraction: float,
) -> SizedPosition:
    """Risk-based whole-share quantity, capped at a max position notional (#237).

    Targets ``risk_fraction`` of equity at risk — ``qty × (entry − stop) ≈ equity × risk_fraction``,
    so ``risk_qty = floor(equity × risk_fraction / (entry − stop))`` — then caps the position at
    ``cap_qty = floor(equity × max_position_fraction / entry)`` so a tight stop can't size past the
    concentration / settled-cash limit. Takes ``min(risk_qty, cap_qty)``.

    **Which one binds is the opposite of the intuition** (and of what this module's docs claimed
    until #286): ``risk_qty < cap_qty ⟺ (entry − stop) / entry > risk_fraction /
    max_position_fraction``, so the risk target binds on a **wide** stop and the notional cap binds
    on a **tight** one — at the 5%/50% default, any stop within 10% of entry is cap-bound. Bull-flag
    stops sit a few percent below entry, so the cap is the *usual* constraint, not the edge case,
    and a cap-bound position risks ``max_position_fraction × (entry − stop) / entry`` of equity —
    well under the configured ``risk_fraction``. Hence :class:`SizedPosition`, not a bare int.

    ``sized_by`` is ``"cap"`` only when the cap strictly *reduced* the size below what the risk
    budget wanted (``cap_qty < risk_qty``). On a tie the risk target got exactly what it asked for,
    so nothing was given up and it reports ``"risk"``.

    ``risk = entry − stop`` is guaranteed positive by the caller (candidates are pre-filtered on
    ``risk > 0``); a non-positive risk falls back to cap."""
    if entry_price <= 0:
        return SizedPosition(0, 0, 0, "cap", 0.0, 0.0)
    cap_qty = int((equity * max_position_fraction) // entry_price)
    risk_per_share = entry_price - stop
    if risk_per_share <= 0:  # degenerate; caller guarantees risk > 0, cap-bound defensively
        return SizedPosition(cap_qty, cap_qty, cap_qty, "cap", 0.0, 0.0)
    risk_qty = int((equity * risk_fraction) // risk_per_share)
    qty = min(risk_qty, cap_qty)
    risk_usd = round(qty * risk_per_share, 4)
    return SizedPosition(
        qty=qty,
        risk_qty=risk_qty,
        cap_qty=cap_qty,
        sized_by="cap" if cap_qty < risk_qty else "risk",
        risk_usd=risk_usd,
        risk_pct=round(risk_usd / equity, 6) if equity > 0 else 0.0,
    )


def commission(qty: int, per_share: float, minimum: float) -> float:
    """IBKR-style per-order-side commission: ``max(minimum, qty × per_share)``.

    This is the IBKR line ONLY. Under tiered pricing the exchange/regulatory pass-throughs are
    unbundled and charged on top — see :func:`trade_costs` for the all-in figure."""
    return round(max(minimum, qty * per_share), 4)


@dataclass(frozen=True)
class TradeCosts:
    """All-in round-trip cost of one paper trade, split so the drag is visible, not buried.

    ``commission_usd`` is IBKR's own line; ``fees_usd`` is everything tiered pricing unbundles
    (exchange removal + clearing on both sides, FINRA TAF + SEC Section 31 on the sell)."""

    commission_usd: float
    fees_usd: float

    @property
    def total_usd(self) -> float:
        return round(self.commission_usd + self.fees_usd, 4)


def trade_costs(qty: int, entry_price: float, exit_price: float, s: Settings) -> TradeCosts:
    """Full IBKR tiered round-trip cost for ``qty`` shares (#232 §1).

    Both sides pay commission + exchange removal + clearing; only the sell pays TAF and SEC. The
    book is always liquidity-removing (stop-triggered entries, stop/market exits), so no
    add-liquidity rebate is ever credited."""
    if qty < 1:
        return TradeCosts(0.0, 0.0)
    comm = 2 * commission(qty, s.portfolio_commission_per_share, s.portfolio_commission_min)
    per_share_both = (
        2 * qty * (s.portfolio_exchange_fee_per_share + s.portfolio_clearing_fee_per_share)
    )
    taf = min(qty * s.portfolio_taf_per_share, s.portfolio_taf_max)
    sec = max(0.0, qty * exit_price) * s.portfolio_sec_fee_rate
    return TradeCosts(round(comm, 4), round(per_share_both + taf + sec, 4))
