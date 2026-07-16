"""Sizing and IBKR cost model for the virtual book (#232, #237).

Risk-based whole-share sizing capped by notional, plus the tiered commission + pass-through fees.
Split out of the old single-file ``portfolio.py`` (#259) with no behaviour change.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings


def size_position(
    equity: float,
    entry_price: float,
    stop: float,
    *,
    risk_fraction: float,
    max_position_fraction: float,
) -> int:
    """Risk-based whole-share quantity, capped at a max position notional (#237).

    Targets ``risk_fraction`` of equity at risk — ``qty × (entry − stop) ≈ equity × risk_fraction``,
    so ``risk_qty = floor(equity × risk_fraction / (entry − stop))`` — then caps the position at
    ``cap_qty = floor(equity × max_position_fraction / entry)`` so a tight stop can't size past the
    concentration / settled-cash limit. Returns ``min(risk_qty, cap_qty)``: the risk target binds
    on tight stops, the notional cap on wide ones. ``risk = entry − stop`` is guaranteed positive by
    the caller (candidates are pre-filtered on ``risk > 0``); a non-positive risk falls back to cap.
    """
    if entry_price <= 0:
        return 0
    cap_qty = int((equity * max_position_fraction) // entry_price)
    risk_per_share = entry_price - stop
    if risk_per_share <= 0:  # degenerate; caller guarantees risk > 0, cap-bound defensively
        return cap_qty
    risk_qty = int((equity * risk_fraction) // risk_per_share)
    return min(risk_qty, cap_qty)


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
