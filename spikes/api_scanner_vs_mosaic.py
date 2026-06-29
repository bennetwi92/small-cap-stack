#!/usr/bin/env python3
"""SPIKE (issue #8): can the IBKR API scanner reproduce the TWS Mosaic small-cap scan?

The headless system cannot use the Mosaic GUI scanner — it must use the API
(`reqScannerData` / `ScannerSubscription`). This script runs an API scan tuned to the
strategy universe (low-priced small-cap gainers with a short-term volume spike) and saves
the ranked results so they can be eyeballed against a Mosaic screenshot.

Volume: the strategy wants TRAILING 5-MIN volume, not day volume. IBKR exposes this
natively via the `stVolume5minAbove` filter (also 3min/10min), so we filter on that
directly — no need to derive it from bars. (`--quotes` still shows cumulative day volume
for reference only.) Use `--dump-params` to write the full parameter list.

    # inside the project venv (pip install -e ".[dev]")
    python spikes/api_scanner_vs_mosaic.py --port 4002 --quotes
    python spikes/api_scanner_vs_mosaic.py --port 4002 --vol-window 5min --min-volume 100000
    python spikes/api_scanner_vs_mosaic.py --dump-params

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

# Maps a volume window to the IBKR scanner filter code (from reqScannerParameters).
VOL_FILTER = {
    "day": "volumeAbove",
    "3min": "stVolume3minAbove",
    "5min": "stVolume5minAbove",
    "10min": "stVolume10minAbove",
}


@dataclass
class ScanRow:
    rank: int
    symbol: str
    sec_type: str
    exchange: str
    currency: str
    con_id: int
    # populated only with --quotes (reference only; day-cumulative, NOT the 5-min window)
    last: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    day_volume: float | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497, help="TWS 7497/7496, Gateway 4002/4001")
    p.add_argument("--client-id", type=int, default=77)
    p.add_argument("--dump-params", action="store_true", help="write scan params XML and exit")
    p.add_argument(
        "--scan-code", default="TOP_PERC_GAIN", help="e.g. TOP_PERC_GAIN, HIGH_STVOLUME_5MIN"
    )
    p.add_argument("--location", default="STK.US.MAJOR", help="STK.US.MAJOR or STK.US (incl OTC)")
    p.add_argument("--min-price", type=float, default=2.0)
    p.add_argument("--max-price", type=float, default=10.0)
    p.add_argument("--change-pct", type=float, default=10.0, help="changePercAbove filter")
    p.add_argument("--vol-window", choices=list(VOL_FILTER), default="5min", help="volume window")
    p.add_argument(
        "--min-volume", type=int, default=100_000, help="min volume in the chosen window"
    )
    p.add_argument("--rows", type=int, default=50, help="numberOfRows (API hard cap is 50)")
    p.add_argument("--quotes", action="store_true", help="snapshot last/change/DAY volume per hit")
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
    # Volume uses the short-term-window filter so it matches "5-min volume > 100k".
    filters = [
        TagValue("priceAbove", str(a.min_price)),
        TagValue("priceBelow", str(a.max_price)),
        TagValue("changePercAbove", str(a.change_pct)),
        TagValue(VOL_FILTER[a.vol_window], str(a.min_volume)),
    ]
    return sub, filters


def add_quotes(ib: IB, rows: list[ScanRow]) -> None:
    """Snapshot last/prev-close/DAY volume — reference only (not the 5-min window)."""
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
        r.day_volume = None if t.volume != t.volume else t.volume


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
            f"| change>{a.change_pct}% | {a.vol_window} vol>{a.min_volume:,} "
            f"({VOL_FILTER[a.vol_window]}) | rows<={min(a.rows, 50)}"
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
                "vol_window": a.vol_window,
                "vol_filter": VOL_FILTER[a.vol_window],
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
            extra = f"  last={r.last} chg%={r.change_pct} dayVol={r.day_volume}" if a.quotes else ""
            print(f"  #{r.rank:<2} {r.symbol:<6} {r.exchange:<10}{extra}")
        if len(rows) >= 50:
            print("\n!! Hit the 50-row API cap — true universe may be larger than shown here.")
        print(f"\nSaved: {csv_path}\n       {json_path}")
        print(
            "Compare against a Mosaic scan captured at the same time; note any names "
            "Mosaic found that the API missed (and vice-versa)."
        )
        return 0
    finally:
        ib.disconnect()


if __name__ == "__main__":
    sys.exit(main())
