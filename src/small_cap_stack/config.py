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

    # Storage (DuckDB + Parquet — issue #7)
    data_dir: Path = Path("./data")
    duckdb_path: Path = Path("./data/small_cap_stack.duckdb")

    # Monitoring
    healthchecks_ping_url: str = ""

    # Logging
    log_level: str = "INFO"
    json_logs: bool = False

    # Schedule (US/Eastern). Trading window 04:00–11:59 ET; EOD report after the close.
    timezone: str = "America/New_York"
    scan_start: time = time(4, 0)
    scan_end: time = time(11, 59)
    eod_report: time = time(16, 30)

    # IB Gateway daily auto-restart (IBC AUTO_RESTART_TIME). Disconnects in this window are
    # treated as expected, not cold failures.
    gateway_restart: time = time(23, 45)
    gateway_restart_window_min: int = 10


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached)."""
    return Settings()
