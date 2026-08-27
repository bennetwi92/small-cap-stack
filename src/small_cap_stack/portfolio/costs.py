"""Sizing and IBKR cost model for the virtual book (#232, #237, #694/D-45).

Full-buying-power whole-share sizing, plus the tiered commission + pass-through fees. Sizing was
risk-based/notional-capped until the full-buying-power rewrite (matching the one-trade-a-day,
whole-account Warrior-style sizing the trader actually uses): the whole account's opening equity is
deployable on the single position the book takes each day, so there is no risk fraction or notional
cap left to bind — just ``floor(equity / entry_price)``.

Split out of the old single-file ``portfolio.py`` (#259) with no behaviour change (at the time).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings


@dataclass(frozen=True)
class SizedPosition:
    """A sized position, full-buying-power sized (qty = floor(equity / entry_price)).

    ``risk_usd`` / ``risk_pct`` are reported post-hoc — what this size *actually* puts at risk given
    the stop — for the dashboard; they no longer bound the size (there is no risk-fraction target
    any more, only the account's buying power)."""

    qty: int
    risk_usd: float  # qty × (entry − stop): the dollars actually at risk, post-hoc
    risk_pct: float  # risk_usd / equity — the realised fraction of equity at risk


def size_position(
    equity: float,
    entry_price: float,
    stop: float,
) -> SizedPosition:
    """Full-buying-power whole-share quantity: ``qty = floor(equity / entry_price)`` (#694/D-45).

    One position a day (the book's capacity cap, ``portfolio_max_trades_per_day``), sized to as many
    shares as the account's opening equity can buy — no risk-fraction target, no notional cap. This
    matches how the trader (and Ross Cameron's "Warrior" small-account-challenge style) actually
    sizes: the whole account is deployable on the one trade of the day.

    ``risk_usd`` / ``risk_pct`` are still reported, computed post-hoc from the resulting ``qty`` and
    the stop, purely for display — they do not feed back into sizing.

    Non-positive ``entry_price`` or non-positive equity size to zero. ``risk = entry − stop`` is
    guaranteed positive by the caller (candidates are pre-filtered on ``risk > 0``); a non-positive
    risk reports zero realised risk rather than raising."""
    if entry_price <= 0 or equity <= 0:
        return SizedPosition(0, 0.0, 0.0)
    qty = int(equity // entry_price)
    risk_per_share = entry_price - stop
    if risk_per_share <= 0:  # degenerate; caller guarantees risk > 0
        return SizedPosition(qty, 0.0, 0.0)
    risk_usd = round(qty * risk_per_share, 4)
    return SizedPosition(
        qty=qty,
        risk_usd=risk_usd,
        risk_pct=round(risk_usd / equity, 6),
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


def trade_costs(
    qty: int,
    entry_price: float,  # noqa: ARG001 — see the note on the 1% commission cap below
    exit_price: float,
    s: Settings,
) -> TradeCosts:
    """Full IBKR tiered round-trip cost for ``qty`` shares (#232 §1).

    Both sides pay commission + exchange removal + clearing; only the sell pays TAF and SEC. The
    book is always liquidity-removing (stop-triggered entries, stop/market exits), so no
    add-liquidity rebate is ever credited.

    ``entry_price`` is not read yet, which is why it carries a ``noqa`` — but it is a *missing
    feature* rather than a dead parameter (#519). IBKR tiered commission is capped at **1% of
    trade value**, and ``commission()`` doesn't model that cap. On this book's cheapest names the
    cap can bind: 100 shares of a $1.20 stock is $1.20 of value against a $1.00 minimum. Modelling
    it needs both prices, so the signature is already the right shape."""
    if qty < 1:
        return TradeCosts(0.0, 0.0)
    comm = 2 * commission(qty, s.portfolio_commission_per_share, s.portfolio_commission_min)
    per_share_both = (
        2 * qty * (s.portfolio_exchange_fee_per_share + s.portfolio_clearing_fee_per_share)
    )
    taf = min(qty * s.portfolio_taf_per_share, s.portfolio_taf_max)
    sec = max(0.0, qty * exit_price) * s.portfolio_sec_fee_rate
    return TradeCosts(round(comm, 4), round(per_share_both + taf + sec, 4))
