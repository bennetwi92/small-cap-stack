"""Data-quality canary (#346): each assertion must be TRUE on a healthy day and FAIL loudly on
the specific corruption it exists to catch — dead float source, dead news feed, glitched bars."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from small_cap_stack.canary import build_canary
from small_cap_stack.config import Settings
from small_cap_stack.storage import Store

_DAY = date(2026, 7, 17)
_NOW = datetime(2026, 7, 17, 15, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _seed_opportunity(store: Store, symbol: str, con_id: int) -> str:
    oid = f"{_DAY}:{symbol}"
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": oid,
                "symbol": symbol,
                "con_id": con_id,
                "trading_date": _DAY,
                "first_seen_utc": _NOW - timedelta(hours=3),
                "first_rank": 0,
            }
        ],
        partition_date=_DAY,
    )
    return oid


def _seed_fundamentals(store: Store, oid: str, symbol: str, float_shares: int | None) -> None:
    store.append(
        "fundamentals",
        [
            {
                "opportunity_id": oid,
                "symbol": symbol,
                "ts_utc": _NOW - timedelta(hours=3),
                "float_shares": float_shares,
                "shares_outstanding": 12_000_000,
                "short_percent": 0.1,
                "source": "yfinance",
            }
        ],
        partition_date=_DAY,
    )


def _seed_news(store: Store, oid: str, symbol: str, ts: datetime | None) -> None:
    store.append(
        "news",
        [
            {
                "opportunity_id": oid,
                "symbol": symbol,
                "time": "raw",
                "ts_utc": ts,
                "provider": "DJ-N",
                "headline": "h",
                "article_id": f"{symbol}-a1",
            }
        ],
        partition_date=_DAY,
    )


def _seed_bars(store: Store, oid: str, symbol: str, n: int, **anomaly: float) -> None:
    rows = []
    for i in range(n):
        row = {
            "opportunity_id": oid,
            "symbol": symbol,
            "bar_start_utc": _NOW - timedelta(minutes=5 * (n - i)),
            "open": 1.5,
            "high": 2.0,
            "low": 1.0,
            "close": 1.8,
            "volume": 1000.0,
        }
        if i == 0:
            row.update(anomaly)
        rows.append(row)
    store.append("bars", rows, partition_date=_DAY)


def _assertions(store: Store, **overrides: Any) -> dict[str, Any]:
    payload = build_canary(store, _settings(**overrides), _NOW, _DAY)
    result: dict[str, Any] = payload["assertions"]
    return result


def test_empty_day_passes_vacuously(tmp_path: Path) -> None:
    # 0 opportunities is #341's territory (and can be healthy); the canary asserts nothing.
    a = _assertions(Store(tmp_path))
    assert a["float_coverage"]["ok"] is True
    assert a["news_recent"]["ok"] is True
    assert a["bars_sane"]["ok"] is None  # no bars yet — no verdict, never a silent pass


def test_healthy_day_passes_everything(tmp_path: Path) -> None:
    store = Store(tmp_path)
    oid = _seed_opportunity(store, "AAA", 1)
    _seed_fundamentals(store, oid, "AAA", 9_000_000)
    _seed_news(store, oid, "AAA", _NOW - timedelta(hours=2))
    _seed_bars(store, oid, "AAA", 30)
    a = _assertions(store)
    assert a["float_coverage"] == {"ok": True, "covered": 1, "total": 1, "pct": 1.0}
    assert a["news_recent"]["ok"] is True and a["news_recent"]["newest_age_h"] == 2.0
    assert a["bars_sane"] == {"ok": True, "symbols": 1, "offenders": []}


def test_null_floats_fail_coverage(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for i, sym in enumerate(("AAA", "BBB", "CCC")):
        oid = _seed_opportunity(store, sym, i)
        _seed_fundamentals(store, oid, sym, None)  # source is up but returning nothing usable
    a = _assertions(store)
    assert a["float_coverage"]["ok"] is False
    assert a["float_coverage"]["covered"] == 0 and a["float_coverage"]["total"] == 3


def test_missing_fundamentals_rows_fail_coverage(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed_opportunity(store, "AAA", 1)  # opportunity opened, fundamentals never landed
    assert _assertions(store)["float_coverage"]["ok"] is False


def test_stale_news_fails_recency(tmp_path: Path) -> None:
    store = Store(tmp_path)
    oid = _seed_opportunity(store, "AAA", 1)
    _seed_news(store, oid, "AAA", _NOW - timedelta(hours=40))  # only old lookback stories
    a = _assertions(store)
    assert a["news_recent"]["ok"] is False
    assert a["news_recent"]["newest_age_h"] == 40.0


def test_no_news_rows_fails_recency(tmp_path: Path) -> None:
    store = Store(tmp_path)
    _seed_opportunity(store, "AAA", 1)
    assert _assertions(store)["news_recent"]["ok"] is False


def test_unparsed_news_timestamps_fail_recency(tmp_path: Path) -> None:
    # Rows exist but every ts_utc failed to parse: recency is unprovable — that must FAIL
    # (positive confirmation), not silently pass on row count.
    store = Store(tmp_path)
    oid = _seed_opportunity(store, "AAA", 1)
    _seed_news(store, oid, "AAA", None)
    a = _assertions(store)
    assert a["news_recent"]["ok"] is False and a["news_recent"]["rows"] == 1


def test_glitched_bars_fail_sanity_and_name_offenders(tmp_path: Path) -> None:
    store = Store(tmp_path)
    oid_a = _seed_opportunity(store, "AAA", 1)
    oid_b = _seed_opportunity(store, "BBB", 2)
    _seed_bars(store, oid_a, "AAA", 30, high=0.5)  # high < low: a glitched candle
    _seed_bars(store, oid_b, "BBB", 30)
    a = _assertions(store)
    assert a["bars_sane"]["ok"] is False
    assert a["bars_sane"]["offenders"] == ["AAA"]


def test_too_few_bars_fail_sanity(tmp_path: Path) -> None:
    store = Store(tmp_path)
    oid = _seed_opportunity(store, "AAA", 1)
    _seed_bars(store, oid, "AAA", 5)  # far below the session floor
    a = _assertions(store)
    assert a["bars_sane"]["ok"] is False and a["bars_sane"]["offenders"] == ["AAA"]


def test_bar_floor_is_a_setting(tmp_path: Path) -> None:
    store = Store(tmp_path)
    oid = _seed_opportunity(store, "AAA", 1)
    _seed_bars(store, oid, "AAA", 5)
    assert _assertions(store, canary_min_bars=3)["bars_sane"]["ok"] is True
