#!/usr/bin/env python3
"""SPIKE (issue #10): does the IBKR news feed deliver per-symbol breaking news?

The strategy needs "breaking news on the stock". Before paying for a feed (e.g. Benzinga Pro),
check what news IBKR provides on this account: which providers are entitled, and whether
recent per-symbol headlines are retrievable (and their article bodies). Free providers
(BRFG/BRFUPDN/DJNL) tend to be commentary; real per-symbol breaking news may need a paid sub.

    python spikes/ibkr_news_check.py --port 4002 --symbols NNBR,AZI --days 7

Ports: TWS paper 7497 / live 7496 · Gateway paper 4002 / live 4001.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ib_async import IB, Stock

ET = ZoneInfo("America/New_York")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497, help="TWS 7497/7496, Gateway 4002/4001")
    p.add_argument("--client-id", type=int, default=79)
    p.add_argument("--symbols", default="NNBR,AZI", help="comma-separated tickers")
    p.add_argument("--days", type=int, default=7, help="look-back window in days")
    p.add_argument("--max", type=int, default=10, help="max headlines per symbol")
    p.add_argument("--body", action="store_true", help="also fetch the latest article body")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    ib = IB()
    ib.connect(a.host, a.port, clientId=a.client_id, timeout=15)
    try:
        providers = ib.reqNewsProviders()
        codes = [p.code for p in providers]
        print(
            f"Entitled news providers ({len(codes)}): "
            + ", ".join(f"{p.code} ({p.name})" for p in providers)
        )
        if not codes:
            print("\n!! No news providers entitled — per-symbol IBKR news is unavailable here.")
            return 0
        provider_str = "+".join(codes)

        end = datetime.now(ET)
        start = end - timedelta(days=a.days)
        fmt = "%Y-%m-%d %H:%M:%S.0"

        for sym in symbols:
            contract = Stock(sym, "SMART", "USD")
            ib.qualifyContracts(contract)
            ib.sleep(0.2)
            headlines = ib.reqHistoricalNews(
                contract.conId, provider_str, start.strftime(fmt), end.strftime(fmt), a.max
            )
            print(f"\n{sym}: {len(headlines)} headline(s) in last {a.days}d")
            for h in headlines:
                print(f"  {h.time}  [{h.providerCode}]  {h.headline}")
            if a.body and headlines:
                h0 = headlines[0]
                art = ib.reqNewsArticle(h0.providerCode, h0.articleId)
                text = (art.articleText or "").strip()
                print(f"  --- body of latest [{h0.providerCode}] ({len(text)} chars) ---")
                print("  " + (text[:400].replace("\n", " ") + ("…" if len(text) > 400 else "")))

        print(
            "\nAssess: are there per-symbol headlines close to the spike time, and is the body "
            "retrievable? If free providers only give stale commentary, scope a paid feed (#10)."
        )
        return 0
    finally:
        ib.disconnect()


if __name__ == "__main__":
    sys.exit(main())
