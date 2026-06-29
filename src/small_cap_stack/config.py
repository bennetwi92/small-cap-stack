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

    # Logging
    log_level: str = "INFO"
    json_logs: bool = False

    # Schedule (US/Eastern; the market tz lives in clock.ET). Window 04:00–11:59 ET.
    scan_start: time = time(4, 0)
    scan_end: time = time(11, 59)
    eod_report: time = time(16, 30)

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

    # Bull-flag detection (issue #16).
    bull_flag_max_green: int = 2  # max green extension (pole) candles
    bull_flag_max_red: int = 2  # max red consolidation (flag) candles
    entry_tick: float = 0.01  # tick added above the breakout level (US $2-10 names)

    # Capture (issue #14). Scanning stops at scan_end; we keep recording flagged
    # opportunities' bars/news until capture_end (regular close).
    capture_end: time = time(16, 0)
    tick_interval_sec: int = 60  # how often the scan/capture loop runs
    capture_bars_lookback_sec: int = 1800  # 5-min bars window fetched each tick
    news_providers: str = "BRFG+DJ-N+DJNL"
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
