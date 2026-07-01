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
    scan_min_price: float = 2.0
    scan_max_price: float = 10.0
    scan_change_pct: float = 10.0
    scan_min_5m_volume: int = 100_000  # trailing 5-min volume -> stVolume5minAbove
    scan_max_rows: int = 10  # we only ever act on the top few

    # Gate thresholds (issue #15) — most reuse the scan_* values above.
    float_max_shares: int = 20_000_000  # float < 20M shares

    # Bull-flag detection (issue #16). max_red raised to 6 to collect data on which consolidation
    # count is most profitable (#98) — the count + retracement are recorded, not just gated on.
    bull_flag_max_green: int = 2  # max green extension (pole) candles
    bull_flag_max_red: int = 6  # max red consolidation (flag) candles
    tick_size: float = 0.01  # min US price increment for $2-10 names
    entry_offset_ticks: int = 5  # entry = last complete consolidation high + 5 ticks ($0.05)

    # Capture (issue #14). The intraday tick only does discovery (scanner_hits + opportunities +
    # news/fundamentals). The day's 5-min bars are fetched once in an end-of-day batch (#62) —
    # capture_end marks the last bar time we care about (regular close).
    capture_end: time = time(16, 0)
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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached)."""
    return Settings()
