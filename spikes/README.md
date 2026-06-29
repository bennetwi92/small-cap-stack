# Spikes

Time-boxed, throwaway experiments that de-risk decisions before we build. Spike code is not
production code and is exempt from the package's mypy strictness (it lives outside
`src/small_cap_stack`), but it is still ruff-linted.

## `api_scanner_vs_mosaic.py` — issue #8 (highest-priority unknown)

**Question:** Can the IBKR **API** scanner (`reqScannerData`) reproduce the small-cap gainer
scan you run in the TWS **Mosaic** GUI? The headless system can only use the API, so if the
API scan can't surface the same candidates, the whole approach needs rethinking.

### How to run
1. Start TWS or IB Gateway and log in; enable API connections (Configure → API → Settings,
   "Enable ActiveX and Socket Clients").
2. In the project venv:
   ```bash
   pip install -e ".[dev]"
   python spikes/api_scanner_vs_mosaic.py --port 4002 --quotes   # Gateway paper (7497 = TWS paper)
   ```
3. **Discover the right filters first** (one-off):
   ```bash
   python spikes/api_scanner_vs_mosaic.py --dump-params
   ```
   This writes `data/spikes/scanner_parameters.xml` — search it for the `scanCode` and
   `<AbstractField>` tags that match the columns/filters you use in Mosaic.

### Volume: use the native short-term filter, not day volume
The strategy wants **trailing 5-min volume > 100k**, NOT cumulative day volume. The scanner's
`volumeAbove` filter and the snapshot `dayVol` are both day-cumulative. IBKR exposes the
short-term window natively, so we filter on it directly (`--vol-window 5min` → `stVolume5minAbove`):

```bash
# rank by % gain, require a 5-min volume spike (closest to the Mosaic "top gainers" view)
python spikes/api_scanner_vs_mosaic.py --port 4002 --vol-window 5min --min-volume 100000
# or rank by the 5-min spike itself
python spikes/api_scanner_vs_mosaic.py --port 4002 --scan-code HIGH_STVOLUME_5MIN
```

Related native codes worth trying: `stVolume3min/10minAbove`, `stVolumeVsAvg5minAbove`
(relative-volume spike), `volumeRateAbove`, scan code `TOP_VOLUME_RATE`.

### The actual experiment
Run this **pre-market (≈07:00–09:00 ET)** on a few active mornings, and at the same moment
screenshot your Mosaic scan. Then compare:

- Does the API scan return the **same tickers** Mosaic shows (ignoring float, which is a
  post-filter)?
- Does the API scan **update pre-market**, or only during regular hours? (Suspected weak spot.)
- Does the **50-row cap** hide names Mosaic surfaces?
- Which Mosaic filters have **no API equivalent** (→ must become post-filters)?

Results are saved to `data/spikes/api_scan_<timestamp>_ET.{csv,json}` (gitignored).

### What to record on issue #8
- Go / no-go: can the API scan stand in for Mosaic?
- The achievable `ScannerSubscription` definition (scanCode, location, filters).
- Which criteria must be post-filters (expected: float, short interest, news, bull-flag).
- Any pre-market coverage gap and the fallback if the API scanner is inadequate.
