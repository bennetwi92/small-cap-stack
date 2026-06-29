#!/usr/bin/env python3
"""SPIKE (issue #9): are IBKR pre-market 5-min bars complete enough for pattern detection?

The strategy detects a bull-flag (and counts ≤2 green / ≤2 red candles) on 5-min bars during
the 04:00-11:59 ET window. Thin low-float names may trade sparsely pre-market, leaving gaps
in the 5-min series that would distort candle counting. This spike fetches today's pre-market
5-min TRADES bars (useRTH=0) for some symbols and characterises completeness: how many 5-min
slots from 04:00 ET are filled, the largest contiguous gap, and zero-ish bars.

    python spikes/premarket_bar_completeness.py --port 4002 --symbols NNBR,AZI,SKYQ,PETS,CBRG

Ports: TWS paper 7497 / live 7496 · Gateway paper 4002 / live 4001.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from ib_async import IB, Stock

ET = ZoneInfo("America/New_York")
PREMARKET_OPEN = time(4, 0)
RTH_OPEN = time(9, 30)
SLOT = timedelta(minutes=5)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497, help="TWS 7497/7496, Gateway 4002/4001")
    p.add_argument("--client-id", type=int, default=78)
    p.add_argument("--symbols", default="NNBR,AZI,SKYQ,PETS,CBRG", help="comma-separated tickers")
    return p.parse_args()


def analyse(symbol: str, bar_times_et: list[datetime]) -> str:
    """Summarise pre-market 5-min bar completeness for one symbol."""
    if not bar_times_et:
        return f"{symbol:<6} no pre-market bars returned"
    day = bar_times_et[-1].date()
    pm = [t for t in bar_times_et if t.date() == day and PREMARKET_OPEN <= t.time() < RTH_OPEN]
    if not pm:
        return f"{symbol:<6} {len(bar_times_et)} bars, but none in 04:00-09:30 ET window"

    first, last = pm[0], pm[-1]
    span_slots = int((last - first) / SLOT) + 1
    present = len(pm)
    missing = span_slots - present

    present_set = {int((t - first) / SLOT) for t in pm}
    longest_gap = cur = 0
    for i in range(span_slots):
        cur = 0 if i in present_set else cur + 1
        longest_gap = max(longest_gap, cur)

    open_anchor = datetime.combine(day, PREMARKET_OPEN, tzinfo=ET)
    from_4am = int((first - open_anchor) / SLOT)
    cov = 100 * present / span_slots
    return (
        f"{symbol:<6} bars={present:<3} window={first.strftime('%H:%M')}-{last.strftime('%H:%M')} "
        f"first_bar=+{from_4am}slot(s) past 04:00 | filled {present}/{span_slots} ({cov:.0f}%) "
        f"missing={missing} longest_gap={longest_gap} slot(s)"
    )


def main() -> int:
    a = parse_args()
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    ib = IB()
    ib.connect(a.host, a.port, clientId=a.client_id, timeout=15)
    try:
        print(f"Pre-market 5-min bar completeness (useRTH=0) for: {', '.join(symbols)}\n")
        for sym in symbols:
            contract = Stock(sym, "SMART", "USD")
            ib.qualifyContracts(contract)
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,
            )
            ib.sleep(0.25)  # gentle on historical pacing
            times_et = [b.date.astimezone(ET) for b in bars if isinstance(b.date, datetime)]
            print("  " + analyse(sym, times_et))
        print(
            "\nRead: low 'filled %' or large 'longest_gap' on thin names = sparse pre-market "
            "bars → candle counting / bull-flag detection needs a gap policy (skip/flag)."
        )
        return 0
    finally:
        ib.disconnect()


if __name__ == "__main__":
    sys.exit(main())
