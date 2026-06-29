"""Golden timezone tests (#4): UTC storage round-trip + ET window conversion.

These pin down the timezone contract (store UTC, reason in ET) so it can't silently drift with
the host timezone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path

from small_cap_stack.clock import ET, within_window
from small_cap_stack.storage import Store


def test_storage_preserves_utc_instant(tmp_path: Path) -> None:
    store = Store(tmp_path)
    t = datetime(2026, 6, 29, 15, 55, tzinfo=UTC)  # == 11:55 ET (EDT)
    store.append(
        "bars", [{"opportunity_id": "x", "bar_start_utc": t}], partition_date=date(2026, 6, 29)
    )
    got = store.read("bars")["bar_start_utc"].to_list()[0]
    assert got == t  # exact instant preserved, read back in UTC


def test_et_conversion_is_edt_in_summer() -> None:
    # 14:00 UTC on 2026-06-29 is 10:00 EDT (UTC-4), inside the 04:00–11:59 scan window.
    et = datetime(2026, 6, 29, 14, 0, tzinfo=UTC).astimezone(ET)
    assert et.hour == 10
    assert within_window(et, time(4, 0), time(11, 59))


def test_et_conversion_is_est_in_winter() -> None:
    # 14:00 UTC on 2026-01-15 is 09:00 EST (UTC-5), still inside the window.
    et = datetime(2026, 1, 15, 14, 0, tzinfo=UTC).astimezone(ET)
    assert et.hour == 9
    assert within_window(et, time(4, 0), time(11, 59))


def test_post_window_excluded() -> None:
    # 17:00 UTC on 2026-06-29 is 13:00 EDT, after the 11:59 scan cutoff.
    et = datetime(2026, 6, 29, 17, 0, tzinfo=UTC).astimezone(ET)
    assert not within_window(et, time(4, 0), time(11, 59))
