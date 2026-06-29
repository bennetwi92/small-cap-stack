#!/usr/bin/env python3
"""SPIKE (issue #8): can the IBKR API scanner reproduce the TWS Mosaic small-cap scan?

The headless system cannot use the Mosaic GUI scanner — it must use the API
(`reqScannerData` / `ScannerSubscription`). This script runs an API scan tuned to the
strategy universe (low-priced small-cap gainers) and saves the ranked results so they can
be eyeballed against a Mosaic screenshot taken at the same moment.

Goal: a go / no-go on whether the API scanner surfaces the same candidates as Mosaic, and
the achievable scan definition. Float < 20M shares is NOT an API scan parameter — it is a
post-filter, so the API scan is expected to be broader; we are checking it is not *missing*
names Mosaic finds.

Run it against a logged-in TWS or IB Gateway, ideally pre-market (e.g. 07:00-09:00 ET) to
exercise the real-world window, and at the same time capture your Mosaic scan for comparison.

    # inside the project venv (pip install -e ".[dev]")
    python spikes/api_scanner_vs_mosaic.py --port 7497            # TWS paper
    python spikes/api_scanner_vs_mosaic.py --port 4002 --quotes   # Gateway paper + snapshots
    python spikes/api_scanner_vs_mosaic.py --dump-params          # write all valid scan tags

Ports: TWS paper 7497 / live 7496 · Gateway paper 4002 / live 4001.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ib_async import IB, ScanData, ScannerSubscription, Stock, TagValue, Ticker

ET = ZoneInfo("America/New_York")


@dataclass
class ScanRow:
    rank: int
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    con_id: int
    # populated only with --quotes
    last: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    volume: float | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497, help="TWS 7497/7496, Gateway 4002/4001")
    p.add_argument("--client-id", type=int, default=77)
    p.add_argument("--dump-params", action="store_true", help="write scan params XML and exit")
    p.add_argument("--scan-code", default="TOP_PERC_GAIN", help="e.g. TOP_PERC_GAIN, HOT_BY_VOLUME")
    p.add_argument("--location", default="STK.US.MAJOR", help="STK.US.MAJOR or STK.US (incl OTC)")
    p.add_argument("--min-price", type=float, default=2.0)
    p.add_argument("--max-price", type=float, default=10.0)
    p.add_argument("--change-pct", type=float, default=10.0, help="changePercAbove filter")
    p.add_argument("--min-volume", type=int, default=100_000, help="volumeAbove filter (shares)")
    p.add_argument("--rows", type=int, default=50, help="numberOfRows (API hard cap is 50)")
    p.add_argument("--quotes", action="store_true", help="also snapshot last/change/volume per hit")
    p.add_argument("--out-dir", default="data/spikes", help="where to write CSV/JSON (gitignored)")
    return p.parse_args()


def to_row(d: ScanData) -> ScanRow:
    c = d.contractDetails.contract
    return ScanRow(
        rank=d.rank,
        symbol=c.symbol,
        sec_type=c.secType,
        exchange=(c.primaryExchange or c.exchange),
        currency=c.currency,
        con_id=c.conId,
    )


def build_subscription(a: argparse.Namespace) -> tuple[ScannerSubscription, list[TagValue]]:
    sub = ScannerSubscription(
        instrument="STK",
        locationCode=a.location,
        scanCode=a.scan_code,
        abovePrice=a.min_price,
        belowPrice=a.max_price,
        numberOfRows=min(a.rows, 50),
    )
    # Strategy filters as TagValues (the API equivalent of Mosaic's filter rows).
    filters = [
        TagValue("priceAbove", str(a.min_price)),
        TagValue("priceBelow", str(a.max_price)),
        TagValue("changePercAbove", str(a.change_pct)),
        TagValue("volumeAbove", str(a.min_volume)),
    ]
    return sub, filters


def add_quotes(ib: IB, rows: list[ScanRow]) -> None:
    """Snapshot last/prev-close/volume so results can be compared to Mosaic columns."""
    ib.reqMarketDataType(3)  # delayed-frozen if no live entitlement; fine for the spike
    contracts = [Stock(r.symbol, r.exchange or "SMART", r.currency or "USD") for r in rows]
    ib.qualifyContracts(*contracts)
    tickers: list[Ticker] = ib.reqTickers(*contracts)
    by_symbol = {t.contract.symbol: t for t in tickers}
    for r in rows:
        t = by_symbol.get(r.symbol)
        if t is None:
            continue
        last = t.marketPrice()
        r.last = None if last != last else round(last, 4)  # NaN check
        r.prev_close = None if t.close != t.close else round(t.close, 4)
        if r.last is not None and r.prev_close:
            r.change_pct = round((r.last - r.prev_close) / r.prev_close * 100, 2)
        r.volume = None if t.volume != t.volume else t.volume


def main() -> int:
    a = parse_args()
    ib = IB()
    ib.connect(a.host, a.port, clientId=a.client_id, timeout=15)
    try:
        if a.dump_params:
            xml = ib.reqScannerParameters()
            out = Path(a.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            path = out / "scanner_parameters.xml"
            path.write_text(xml)
            print(f"Wrote {len(xml):,} bytes of scanner parameters to {path}")
            print("Search it for the scanCodes / filter tags that match your Mosaic scan.")
            return 0

        sub, filters = build_subscription(a)
        print(
            f"Scan: {a.scan_code} @ {a.location} | price {a.min_price}-{a.max_price} "
            f"| change>{a.change_pct}% | vol>{a.min_volume:,} | rows<={min(a.rows, 50)}"
        )
        data: list[ScanData] = ib.reqScannerData(sub, [], filters)

        rows = [to_row(d) for d in data]

        if a.quotes and rows:
            add_quotes(ib, rows)

        now = datetime.now(UTC)
        stamp = now.astimezone(ET).strftime("%Y%m%d_%H%M%S_ET")
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        json_path = out / f"api_scan_{stamp}.json"
        csv_path = out / f"api_scan_{stamp}.csv"

        payload = {
            "captured_utc": now.isoformat(),
            "captured_et": now.astimezone(ET).isoformat(),
            "params": {
                "scan_code": a.scan_code,
                "location": a.location,
                "min_price": a.min_price,
                "max_price": a.max_price,
                "change_pct": a.change_pct,
                "min_volume": a.min_volume,
            },
            "count": len(rows),
            "results": [asdict(r) for r in rows],
        }
        json_path.write_text(json.dumps(payload, indent=2))
        with csv_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(asdict(ScanRow(0, "", "", "", "", 0)).keys()))
            w.writeheader()
            for r in rows:
                w.writerow(asdict(r))

        print(f"\n{len(rows)} result(s):")
        for r in rows:
            extra = ""
            if a.quotes:
                extra = f"  last={r.last} chg%={r.change_pct} vol={r.volume}"
            print(f"  #{r.rank:<2} {r.symbol:<6} {r.exchange:<10}{extra}")
        if len(rows) >= 50:
            print("\n!! Hit the 50-row API cap — true universe may be larger than shown here.")
        print(f"\nSaved: {csv_path}\n       {json_path}")
        print(
            "Now compare these against a Mosaic scan captured at the same time and "
            "note any names Mosaic found that the API missed (and vice-versa)."
        )
        return 0
    finally:
        ib.disconnect()


if __name__ == "__main__":
    sys.exit(main())
