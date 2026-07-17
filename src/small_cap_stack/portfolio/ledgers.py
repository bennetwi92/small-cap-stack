"""Period ledgers: market data, VPS, UK CGT reserve, withdrawals (#232, and #249's gap months).

Each settles at a period boundary and returns a USD debit the caller folds into ``equity`` *before*
the next day is sized — sizing is capital-based, so a charge that didn't compound would flatter the
book. Split out of the old single-file ``portfolio.py`` (#259) with no behaviour change.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from ..config import Settings
from .models import CashFlow, PaperTrade

# All three follow the :class:`_DataFeeLedger` shape: settle at a period boundary and return a USD
# debit the caller folds into ``equity`` *before* the next day is sized (sizing is capital-based, so
# a charge that didn't compound would flatter the book). Each records a dated :class:`CashFlow` too.
# FX: ``portfolio_gbpusd_rate`` is GBP/USD (1 GBP = rate USD) — USD→GBP divides, GBP→USD multiplies.


def _next_month(m: tuple[int, int]) -> tuple[int, int]:
    """The calendar month after ``(year, month)``."""
    y, mo = m
    return (y + 1, 1) if mo == 12 else (y, mo + 1)


class _DataFeeLedger:
    """Accrues the monthly market-data subscription, waived above a commission threshold (#232 §4).

    Charged at month rollover and applied *inline* by the callers (not as a post-pass) so it lands
    in ``equity`` before the next day is sized — sizing is capital-based, so a fee that didn't
    compound would flatter the book. Months present in the data but with no trades are still
    charged: the subscription bills whether or not you trade, which is the entire point of #232."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._month: tuple[int, int] | None = None
        self._commission = 0.0
        self.total_charged = 0.0

    def roll(self, day: date) -> float:
        """Fee due *before* trading ``day``: one per calendar month since the anchor.

        Walks month by month rather than settling once per *observed* rollover (#249). A month with
        zero collected dates — a full data outage — never produces a rollover of its own, so the
        old single-settle silently skipped it: June data, then September data, charged June and
        anchored September, dropping July and August entirely. The subscription bills whether or not
        you trade *and whether or not we collected*, which is the whole point of #232; the docstring
        claim that "months with no trades are still charged" only held for months with some day in
        them."""
        m = (day.year, day.month)
        if self._month is None:
            self._month = m
            return 0.0
        if m <= self._month:  # same month, or an out-of-order day (days arrive ascending)
            return 0.0
        total = 0.0
        while self._month != m:
            total += self._settle()  # intervening months carry no commission -> no waiver
            self._month = _next_month(self._month)
        return round(total, 4)

    def observe(self, trades: Sequence[PaperTrade]) -> None:
        self._commission += sum(t.commission_usd for t in trades)

    def close(self) -> float:
        """Fee for the final (possibly partial) month."""
        return self._settle() if self._month is not None else 0.0

    def _settle(self) -> float:
        waived = self._commission >= self._s.portfolio_market_data_waiver_usd
        fee = 0.0 if waived else self._s.portfolio_market_data_usd_per_month
        self._commission = 0.0
        self.total_charged = round(self.total_charged + fee, 4)
        return fee


class _VpsLedger:
    """The VPS running cost (~£5.70/mo — Hetzner's €6.59), charged at month rollover like the
    market-data fee.

    Kept separate from :class:`_DataFeeLedger` — it's a different real-world expense (host infra,
    not IBKR data), has no waiver, and is denominated in GBP. Every month present in the data is
    charged whether or not it traded: the box bills regardless."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._month: tuple[int, int] | None = None
        self.total_usd = 0.0
        self.total_gbp = 0.0
        self.events: list[CashFlow] = []

    def roll(self, day: date) -> float:
        """One charge per calendar month since the anchor — including months with no data (#249).

        The box bills regardless of whether we collected anything, so a data outage must not be a
        free month. Each gap month's CashFlow is dated at the start of the month it rolls into; the
        final transition keeps ``day`` itself, so a gapless run bills exactly as it did before."""
        m = (day.year, day.month)
        if self._month is None:
            self._month = m
            return 0.0
        if m <= self._month:
            return 0.0
        total = 0.0
        while self._month != m:
            nxt = _next_month(self._month)
            total += self._settle(day if nxt == m else date(nxt[0], nxt[1], 1))
            self._month = nxt
        return round(total, 4)

    def close(self, day: date) -> float:
        return self._settle(day) if self._month is not None else 0.0

    def _settle(self, day: date) -> float:
        gbp = round(self._s.portfolio_vps_gbp_per_month, 4)
        usd = round(gbp * self._s.portfolio_gbpusd_rate, 4)
        if usd <= 0:
            return 0.0
        self.total_gbp = round(self.total_gbp + gbp, 4)
        self.total_usd = round(self.total_usd + usd, 4)
        self.events.append(CashFlow(day, "vps", usd, gbp))
        return usd


class _TaxLedger:
    """UK CGT reserve on net realised gains, per tax year (6 Apr–5 Apr).

    ``observe`` accrues each day's net P&L into a running GBP gain for the current tax year;
    ``reserve_usd`` is the outstanding CGT accrued so far (used to hold cash back from withdrawals
    so the book keeps enough to pay HMRC). ``roll`` settles the prior year's bill at the 6-Apr
    boundary (debits it, records the CashFlow, resets the running gain + allowance); ``close``
    settles the final, possibly-partial year. Losses reduce the year's gain, floored at £0 within
    the year (cross-year loss carry-forward is deliberately not modelled — a documented
    simplification, and conservative since it never *lowers* the reserve). Real CGT is due the
    following 31 Jan; settling at year-end reserves earlier, the safe direction for take-home."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._year_start: date | None = None  # 6-Apr start of the tax year currently accruing
        self._ytd_gain_gbp = 0.0
        self.total_usd = 0.0
        self.total_gbp = 0.0
        self.events: list[CashFlow] = []

    @staticmethod
    def _tax_year_start(day: date) -> date:
        """The 6-April start of the UK tax year containing ``day``."""
        boundary = date(day.year, 4, 6)
        return boundary if day >= boundary else date(day.year - 1, 4, 6)

    def observe(self, trades: Sequence[PaperTrade]) -> None:
        usd = sum(t.net_pnl_usd for t in trades)
        self._ytd_gain_gbp += usd / self._s.portfolio_gbpusd_rate

    def _cgt_gbp(self) -> float:
        taxable = max(0.0, self._ytd_gain_gbp - self._s.portfolio_cgt_annual_exempt_gbp)
        return taxable * self._s.portfolio_cgt_rate

    def reserve_usd(self) -> float:
        """Outstanding CGT accrued this year, in USD — cash to keep back from withdrawals."""
        return round(self._cgt_gbp() * self._s.portfolio_gbpusd_rate, 4)

    def roll(self, day: date) -> float:
        ys = self._tax_year_start(day)
        if self._year_start is None:
            self._year_start = ys
            return 0.0
        if ys == self._year_start:
            return 0.0
        fee = self._settle(day)
        self._year_start = ys
        self._ytd_gain_gbp = 0.0
        return fee

    def close(self, day: date) -> float:
        return self._settle(day) if self._year_start is not None else 0.0

    def _settle(self, day: date) -> float:
        gbp = round(self._cgt_gbp(), 4)
        usd = round(gbp * self._s.portfolio_gbpusd_rate, 4)
        if usd <= 0:
            return 0.0
        self.total_gbp = round(self.total_gbp + gbp, 4)
        self.total_usd = round(self.total_usd + usd, 4)
        self.events.append(CashFlow(day, "tax", usd, gbp))
        return usd


class _WithdrawalLedger:
    """Quarterly profit withdrawal above a high-water mark — how the strategy actually pays you.

    Every ``withdraw_cadence_months`` it pays out ``withdraw_fraction`` of the profit above the HWM,
    but never dips below ``withdraw_floor_usd`` (base capital / account viability) and never
    distributes cash reserved for tax. The HWM then ratchets to the post-withdrawal balance, so the
    next period only pays on genuinely new profit. Pays nothing while at/under the floor or
    underwater — which is why the whole layer is a no-op at the $500 start until the account grows
    past the floor."""

    def __init__(self, s: Settings) -> None:
        self._s = s
        self._hwm = s.portfolio_start_equity_usd
        self._anchor: tuple[int, int] | None = None  # (year, month) the cadence last reset at
        self.total_usd = 0.0
        self.total_gbp = 0.0
        self.events: list[CashFlow] = []

    def roll(self, day: date, equity: float, tax_reserve_usd: float) -> float:
        ym = (day.year, day.month)
        if self._anchor is None:
            self._anchor = ym
            return 0.0
        elapsed = (ym[0] - self._anchor[0]) * 12 + (ym[1] - self._anchor[1])
        if elapsed < self._s.portfolio_withdraw_cadence_months:
            return 0.0
        self._anchor = ym
        return self._withdraw(day, equity, tax_reserve_usd)

    def _withdraw(self, day: date, equity: float, tax_reserve_usd: float) -> float:
        profit = equity - self._hwm
        if profit <= 0 or equity <= self._s.portfolio_withdraw_floor_usd:
            return 0.0
        available = equity - self._s.portfolio_withdraw_floor_usd - tax_reserve_usd
        gross = round(min(self._s.portfolio_withdraw_fraction * profit, available), 4)
        if gross <= 0:
            return 0.0
        gbp = round(gross / self._s.portfolio_gbpusd_rate, 4)
        self._hwm = round(equity - gross, 4)
        self.total_usd = round(self.total_usd + gross, 4)
        self.total_gbp = round(self.total_gbp + gbp, 4)
        self.events.append(CashFlow(day, "withdrawal", gross, gbp))
        return gross
