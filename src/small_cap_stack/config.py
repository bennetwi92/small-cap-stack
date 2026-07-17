"""Application configuration, loaded from environment / .env (see .env.example)."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Field names map case-insensitively to env vars (e.g. IBKR_HOST)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # IBKR connection (used by the connection supervisor — issue #11)
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002  # Gateway paper 4002 / live 4001; TWS 7497 / 7496
    ibkr_client_id: int = 1
    # After an *unclean* disconnect the Gateway can hold the old client id for tens of seconds, so a
    # reconnect on the same id fails with error 326. Rotate across a small pool of ids on successive
    # connect attempts so a reconnect sidesteps a still-held id (Phase-1 places no orders, so the id
    # need not be stable). Steady state uses ibkr_client_id; only a stuck id bumps up (#163-C2).
    ibkr_client_id_pool: int = 4
    ibkr_connect_timeout_sec: float = 15.0  # bound the connectAsync handshake
    ibkr_trading_mode: str = "paper"  # paper | live

    # Storage (DuckDB + Parquet — issue #7). DuckDB is opened in-memory over the Parquet globs.
    data_dir: Path = Path("./data")

    # Monitoring (issue #5)
    healthchecks_ping_url: str = ""
    metrics_enabled: bool = True
    metrics_port: int = 9090

    # Dashboard exporter (issue #68) — writes status.json/stats.json under data_dir/dashboard.
    dashboard_enabled: bool = True
    deployed_commit: str = ""  # optional; set via env DEPLOYED_COMMIT (baked at build, #72)

    # Logging
    log_level: str = "INFO"
    json_logs: bool = False

    # Schedule (US/Eastern; the market tz lives in clock.ET). Window 04:00–11:59 ET.
    scan_start: time = time(4, 0)
    scan_end: time = time(11, 59)
    eod_bars_fetch: time = time(16, 20)  # batch-fetch the day's 5-min bars (before the report)
    eod_report: time = time(16, 30)
    eod_backfill: time = time(3, 45)  # morning catch-up: back-fill bars a missed EOD batch dropped
    # EOD batch resilience (#100): retry a disconnect / transient failure instead of skipping.
    eod_retry_attempts: int = 3
    eod_retry_delay_sec: float = 60.0
    backfill_days: int = 3  # how many recent calendar days the morning catch-up scans
    # Daily cron jobs tolerate a brief event-loop delay before being counted as misfired/skipped
    # (APScheduler's default is 1s — too tight for once-a-day critical jobs). Kept well inside the
    # 16:20 -> 16:30 eod_bars -> eod_report gap.
    cron_misfire_grace_sec: int = 300

    # IB Gateway daily auto-restart (IBC AUTO_RESTART_TIME). Disconnects in this window are
    # treated as expected, not cold failures.
    gateway_restart: time = time(23, 45)
    gateway_restart_window_min: int = 10

    # Scanner (issue #13) — validated definition from spike #8.
    scan_code: str = "TOP_PERC_GAIN"
    scan_location: str = "STK.US.MAJOR"
    scan_min_price: float = 1.0  # widened from $2 → $1–$50 universe (#126)
    scan_max_price: float = 50.0  # widened from $10 → $1–$50 universe (#126)
    scan_change_pct: float = 10.0
    scan_min_5m_volume: int = 100_000  # trailing 5-min volume -> stVolume5minAbove
    # Collect the full scanner breadth (IBKR API hard-caps numberOfRows at 50). Phase-1 is a
    # data-collection exercise — on busy mornings there are far more than 10 low-float runners in
    # play, and store-raw/compute-on-read means we want the whole ranked list captured. We still
    # only *act* on the top few; the extra rows are dataset upside (#126 widened the universe too).
    scan_max_rows: int = 50
    # IBKR `stkTypes` codes to exclude from the STK scan. `STK.US.MAJOR` mixes common stock with
    # ETFs/ETNs (incl. leveraged single-stock products like MSTX/RKLX), which have no share float
    # and aren't Warrior-style momentum candidates — drop them server-side. Empty tuple = keep all.
    scan_exclude_stock_types: tuple[str, ...] = ("ETF", "ETN")

    # Gate thresholds (issue #15) — most reuse the scan_* values above.
    float_max_shares: int = 20_000_000  # float < 20M shares

    # Bull-flag detection (issue #16; redefined #127 from notes.md 2026-07-03). The pole is a run of
    # HIGHER HIGHS (not just "green candles"): even a SINGLE higher-high bar is a pole, up to a run
    # of many (SNDQ = 7); pole_len counts the higher highs. The flag is a pullback that makes LOWER
    # HIGHS (the trader tracks highs, not lows) and holds within max_retracement of the pole (a
    # deeper pullback retraces "back through the pole"). Volume: the pole's peak bar volume must
    # exceed the consolidation's (hard); the consolidation volume ideally reduces (soft, recorded).
    bull_flag_min_pole: int = 1  # a pole can be a single higher-high bar
    bull_flag_max_pole: int = 8  # cap on the higher highs counted as the pole
    bull_flag_max_flag: int = 6  # max consolidation (flag) candles
    bull_flag_max_retracement: float = 0.50  # reject flags retracing > this fraction of the pole
    # Pole wick quality (#132): reject a pole whose peak (highest-high) bar closed weakly — upper
    # wick > this fraction of the bar's range. A clean thrust closes near its high; a wicky one
    # (AHMA/VRXA) is a no-trade. Whether the pole holds a big green candle is recorded, not gated.
    bull_flag_max_peak_wick: float = 0.50
    tick_size: float = 0.01  # min US price increment for names ≥ $1 (penny tick)
    entry_offset_ticks: int = 5  # LEGACY entry = last complete consolidation high + 5 ticks
    # Engine-v2 entry trigger (#182/#190, validated via per-opportunity visual review): the
    # breakout is confirmed 1 tick above the last consolidation candle's high — a lower high, per
    # the trader's rule. Distinct from entry_offset_ticks (legacy, unused by v2). Often the fill is
    # this exact price; bull_flag_fill_offset_ticks below is a separate, deliberately conservative
    # slippage estimate used only for R-measurement, not for deciding whether/when triggered.
    bull_flag_trigger_offset_ticks: int = 1
    # Conservative slippage-modeled FILL price for R-measurement (#182/#190; confirmed by the
    # trader): the trigger (above) decides WHEN a setup fires; once fired, R is measured against a
    # worse, 3-tick fill to avoid overstating the edge — "often I fill at the trigger price anyway,
    # 3 ticks is being conservative." Applied downstream of the trigger, not in place of it.
    bull_flag_fill_offset_ticks: int = 3
    # Exhaustion (engine-v2 full-day detector, #102/#211): reject entry on the (cap+1)'th contiguous
    # significant pump/fade cycle of the day — "entering the third cycle is entering an exhausted
    # opportunity" (trader). A cycle counts if its pole carries a green thrust bar and a bar clears
    # scan_min_5m_volume // 2, and it abuts the run (see bullflag.cycles).
    bull_flag_exhaustion_cap: int = 2
    # Entry staleness (#130): a break more than this many minutes after the scanner appearance reads
    # as "faded" — the opportunity is no longer takeable (AHMA triggered ~1hr+ after the scan). Only
    # applies when the appearance (first_hit) is known; a large value disables the bound.
    entry_staleness_min: int = 30

    # Capture (issue #14). The intraday tick only does discovery (scanner_hits + opportunities +
    # news/fundamentals). The day's 5-min bars are fetched once in an end-of-day batch (#62) —
    # capture_end marks the last bar time we care about (regular close).
    capture_end: time = time(16, 0)
    # Chart-start bound for the review workbench's full-day series (#140/#141): charts render the
    # whole trading day `chart_start <= t < capture_end` ET (04:00–16:00), not just the run window.
    chart_start: time = time(4, 0)
    tick_interval_sec: int = 60  # how often the scan/discovery loop runs
    eod_bars_duration: str = "1 D"  # reqHistoricalData duration for the EOD 5-min bar batch
    news_providers: str = "BRFG+DJ-N+DJNL"

    # Re-entry segmentation (issue #36) — a symbol can form >1 opportunity/day. A gap of
    # >= reentry_gap_min with no scanner hits starts a NEW run (e.g. pre-market pop → fade →
    # market-open pop). Each run is analysed over its own bar window, extended back
    # reentry_lookback_min so the run's pole is included.
    reentry_gap_min: int = 60
    reentry_lookback_min: int = 30
    news_lookback_days: int = 7
    news_max: int = 10

    # Async safety — bound blocking/remote calls so a hung dependency can't wedge the loop.
    ibkr_request_timeout_sec: float = 30.0
    fundamentals_timeout_sec: float = 10.0
    heartbeat_timeout_sec: float = 10.0
    # Spacing between successive historical requests in the EOD/back-fill batch, to stay clear of
    # the IBKR pacing limit (< 60 historical requests / 10 min) on heavy days (#163-C2).
    ibkr_hist_pacing_sec: float = 0.2

    # Float source hardening (#109): FMP /shares-float, primary over yfinance on read. Unset →
    # yfinance-only, nothing breaks. Free tier is 250 req/day, US stocks — ample at ~10 flags/day.
    fmp_api_key: str = ""

    # Virtual-portfolio tracker (#230) — a pre-shadow paper book computed on-read over the captured
    # dataset. Rules locked in research/decisions.md (2026-07-15): UK cash account, capital-based
    # sizing, strict pre-market fills, engine-v2 takeable setups only, fixed-R exit + breakeven.
    portfolio_start_equity_usd: float = 500.0
    # Sizing = risk-based, capped by notional (#237). Each position targets `risk_fraction` of the
    # day's opening equity at risk (qty ≈ equity × risk_fraction / (entry − stop)) but is capped at
    # `position_fraction` of opening equity in notional (qty ≤ equity × position_fraction / entry).
    # The cap binds on wide stops, the risk target on tight ones; qty = min(risk_qty, cap_qty), so
    # the cap is always the upper bound — that is what keeps the settled-cash invariant intact.
    portfolio_risk_fraction: float = 0.05  # target risk per trade, as a fraction of opening equity
    portfolio_position_fraction: float = 0.50  # max position notional, as a fraction of opening eq.
    portfolio_max_trades_per_day: int = 2  # cap 50% × 2 = at most fully deployed → 2 concurrent
    portfolio_premarket_cutoff: time = time(9, 30)  # strict: the TRIGGER bar must open before this
    portfolio_entry_price_min: float = 1.0  # entry_fill price band (narrower than the $1–50 scan)
    portfolio_entry_price_max: float = 20.0
    # Symbols to exclude from the paper book. Before #226/#227 added the scanner's `stkTypes`
    # ETF/ETN filter, `STK.US.MAJOR` captured a handful of leveraged single-stock ETFs (no share
    # float, not Warrior-style candidates) that then flowed into this compute-on-read book. The
    # scanner no longer captures them, but the already-stored opportunities still would; drop them
    # here so the historical book is clean. Verified no-float in the captured fundamentals.
    portfolio_exclude_symbols: tuple[str, ...] = ("CCUP", "CRCG", "OKLL", "SNDQ")
    portfolio_target_r: float = 2.0  # fallback fixed R target (used until the window has samples)
    portfolio_breakeven_r: float = 0.0  # arm a breakeven stop once +Nb·R is reached; 0 disables
    # Adaptive target: each day re-fits the target to the highest-expectancy grid value over the
    # trailing window of prior candidates. Small-N overfit is guarded by the window + plateau bias.
    portfolio_target_grid: tuple[float, ...] = (1.5, 2.0, 2.5, 3.0)
    portfolio_adaptive_window_days: int = 20  # trailing lookback for the expectancy re-fit
    portfolio_adaptive_min_samples: int = 8  # need this many trailing trades before re-fitting
    # Adaptive risk throttle / kill-switch (#239): the per-trade `risk_fraction` itself walks a
    # small ladder from 0 up to `portfolio_risk_fraction`, driven by recent daily results. The
    # adaptive book starts at full risk (top rung) and steps ONE rung only after `risk_step_days`
    # net-positive days *in a row* (up) or the same run of net-negative days (down); a day's result
    # is the aggregate realised R over its qualifying setups, and a flat / no-setup day holds both
    # the rung and the streak (an info-less day carries no momentum — "in a row" counts decisive
    # days). At the 0% rung no capital is committed, but the day's *would-be* setups are still
    # scored (the signal is size-independent by design) so the switch re-arms when the tape turns.
    # Few rungs = a fast wind-up to full risk. `risk_rungs=1` disables the throttle. Only the
    # adaptive book throttles; fixed-target books stay at full `risk_fraction` as a baseline.
    portfolio_risk_rungs: int = 3  # rungs incl. the 0 floor → (0, 2.5%, 5%)
    portfolio_risk_step_days: int = 2  # consecutive same-direction days to move a rung (1 = eager)
    # Costs, netted out of every trade so the equity curve is honest at ~$250 notional. Full IBKR
    # TIERED US-stock schedule per research/broker-costs.md (#232) — tiered UNBUNDLES the exchange /
    # regulatory pass-throughs, and at these share counts they roughly equal the commission itself,
    # so charging commission alone understates a round trip by 20-50%. Rates are per ORDER SIDE.
    portfolio_commission_per_share: float = 0.0035
    portfolio_commission_min: float = 0.35
    # Exchange liquidity-REMOVAL fee. Entries are stop/stop-limit triggers above the consolidation
    # high and exits are stops/market, so this book is always marketable and never earns the
    # add-liquidity rebate. Representative lit-venue rate; varies by venue (#232 §1 caveat).
    portfolio_exchange_fee_per_share: float = 0.0030
    portfolio_clearing_fee_per_share: float = 0.0002
    # Sell-side only:
    portfolio_taf_per_share: float = 0.000166  # FINRA Trading Activity Fee
    portfolio_taf_max: float = 8.30  # per-order cap (never binds at this size; kept for fidelity)
    portfolio_sec_fee_rate: float = 0.0000278  # SEC Section 31, on proceeds
    portfolio_exit_slippage_ticks: int = 2  # slippage on stop / mark-to-close exits (limit TP = 0)
    # Market data (#232 §4). $10/mo is ~2%/mo of a $500 book — the whole point of #232 is that fixed
    # costs do NOT scale down with capital, so the curve carries it. Charged at month rollover and
    # waived when that month's IBKR commission clears the threshold.
    portfolio_market_data_usd_per_month: float = 10.0
    portfolio_market_data_waiver_usd: float = 30.0
    # Withdrawals + UK tax + running cost: the "getting paid" layer on top of the paper book. The
    # book is kept in USD (funded once from GBP, then permanently USD, broker-costs.md), so pounds
    # are derived through one assumed rate, not a daily FX series. The rate is quoted GBP/USD the
    # market way: 1 GBP = `gbpusd_rate` USD, so USD->GBP divides and GBP->USD multiplies. A single
    # rate is an approximation; per-disposal daily rates would be the accurate, heavier alternative.
    # Locked 2026-07-16, research/decisions.md.
    portfolio_gbpusd_rate: float = 1.27
    # Withdrawal policy: pay out a share of NEW profit above a high-water mark, every N months, but
    # never below the viability floor and never distributing cash reserved for tax. The HWM ratchets
    # to the post-withdrawal balance so each period only pays on genuinely new profit. At the $500
    # start the floor makes the whole layer a no-op — it only begins paying once the account clears
    # the floor, which is the honest state (broker-costs §9: $500 is plumbing validation).
    portfolio_withdraw_fraction: float = 0.5  # share of profit above the HWM paid out each period
    portfolio_withdraw_cadence_months: int = 3  # quarterly
    portfolio_withdraw_floor_usd: float = 2000.0  # never withdraw below this settled-USD balance
    # UK Capital Gains Tax on net realised gains. Higher-rate share CGT is 24% (post-30-Oct-2024) on
    # gains above the £3,000 annual exempt amount, reserved per UK tax year (6 Apr–5 Apr). The rate
    # is a knob so the income-tax "treated as a trade" scenario (~42–47% incl. NIC) can be modelled
    # without code changes — see research/decisions.md for the CGT-vs-trading-income risk.
    portfolio_cgt_rate: float = 0.24
    portfolio_cgt_annual_exempt_gbp: float = 3000.0
    # VPS running cost, charged at month rollover like the market-data fee but kept as its own line
    # (different real-world expense). The Hetzner CX22 is €6.59/mo per the console's price estimate
    # — not an invoice; none exists yet (box created 2026-07-01). Held here in GBP (€6.59 × ~0.865
    # EUR/GBP) because the whole cost model is GBP-denominated and converts to USD through the
    # single portfolio_gbpusd_rate. The EUR/GBP rate is baked into this figure rather than being its
    # own knob — revisit if the euro moves materially.
    # Unconfirmed (#284): the estimate may have included a 10 GB volume (deleted 2026-07-17), which
    # would make this ~£0.41/mo high. Reconcile against August's invoice — July's is muddied by the
    # volume's partial month.
    portfolio_vps_gbp_per_month: float = 5.70


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached)."""
    return Settings()
