#!/usr/bin/env python3
"""SPIKE (issue #25): is a symbol actually ORDERABLE on IBKR (not just un-halted)?

A symbol can be actively trading yet IBKR blocks order entry on their platform (common for
small-cap/low-float runners, foreign issuers, fresh IPOs). The live system must gate these,
or it logs/attempts entries that can never fill. This probes each symbol non-intrusively:

  1. reqContractDetails  -> does it qualify / is SMART routing available?
  2. snapshot + halted    -> prove it IS trading and is NOT halted
  3. whatIfOrder          -> margin preview of a hypothetical order (NO execution); a blocked
                             symbol errors or returns no margin -> our tradability signal

    python spikes/ibkr_tradability_check.py --port 4002 --symbols NNBR,AZI,SKYQ

Ports: TWS paper 7497 / live 7496 · Gateway paper 4002 / live 4001.
Caveat: paper accounts may not mirror live restrictions — re-validate in Phase 3 (live).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from ib_async import IB, LimitOrder, Stock

# Error codes that indicate the symbol is not orderable on IBKR (vs a transient issue).
BLOCK_CODES = {201, 202, 203, 10147, 10148, 10197, 2101}


@dataclass
class Probe:
    symbol: str
    qualified: bool = False
    last: float | None = None
    halted: float | None = None
    whatif_returned: bool = False
    init_margin: str = ""
    errors: list[tuple[int, str]] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(code in BLOCK_CODES for code, _ in self.errors)

    @property
    def verdict(self) -> str:
        if not self.qualified:
            return "NOT FOUND (no contract details)"
        if self.blocked:
            return "BLOCKED (order rejected)"
        if self.whatif_returned:
            return "TRADABLE"
        return "UNKNOWN (no preview, no block error)"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497, help="TWS 7497/7496, Gateway 4002/4001")
    p.add_argument("--client-id", type=int, default=80)
    p.add_argument("--symbols", default="NNBR,AZI,SKYQ", help="comma-separated tickers")
    return p.parse_args()


def probe_symbol(ib: IB, symbol: str) -> Probe:
    p = Probe(symbol)
    errors: list[tuple[int, str]] = []

    def on_error(reqId: int, code: int, msg: str, *_: object) -> None:
        errors.append((code, msg))

    details = ib.reqContractDetails(Stock(symbol, "SMART", "USD"))
    if not details:
        return p
    p.qualified = True
    contract = details[0].contract

    # Prove it is trading / not halted (halted tick: 1 or 2 => halted).
    ticker = ib.reqMktData(contract, "", snapshot=True)
    ib.sleep(1.5)
    mp = ticker.marketPrice()
    p.last = None if mp != mp else round(mp, 4)  # NaN check
    p.halted = ticker.halted if ticker.halted == ticker.halted else None
    ib.cancelMktData(contract)

    # Non-intrusive order preview (margin check only — never transmitted).
    ib.errorEvent += on_error
    try:
        price = round((p.last or 1.0) * 0.5, 2) or 1.0  # non-marketable BUY
        state = ib.whatIfOrder(contract, LimitOrder("BUY", 1, price))
        ib.sleep(0.6)
        p.init_margin = getattr(state, "initMarginChange", "") or ""
        p.whatif_returned = state is not None
    finally:
        ib.errorEvent -= on_error
    # Drop benign codes: data-farm status, snapshot teardown (300), TIF preset (10349).
    noise = {2104, 2106, 2158, 2107, 2119, 300, 10349}
    p.errors = [(c, m) for c, m in errors if c not in noise]
    return p


def main() -> int:
    a = parse_args()
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    ib = IB()
    ib.connect(a.host, a.port, clientId=a.client_id, timeout=15)
    try:
        ib.reqMarketDataType(3)  # delayed-frozen ok — we only need proof it trades
        print(f"IBKR tradability probe for: {', '.join(symbols)}\n")
        for sym in symbols:
            p = probe_symbol(ib, sym)
            halt = {0.0: "no", 1.0: "HALTED", 2.0: "HALTED(vol)"}.get(p.halted, "n/a")
            print(
                f"  {sym:<6} {p.verdict:<28} last={p.last} halted={halt} "
                f"whatIfMargin={p.init_margin or '-'}"
            )
            for code, msg in p.errors:
                print(f"         err {code}: {msg[:90]}")
        print(
            "\nDetection rule for the build: treat 'BLOCKED' (whatIfOrder rejected with a "
            "block error code) as a hard tradability gate, separate from the halted check."
        )
        return 0
    finally:
        ib.disconnect()


if __name__ == "__main__":
    sys.exit(main())
