"""Publishing reconstructed sessions to the dashboard's chart namespace (#488).

The properties under test are the ones that keep the live/recon separation honest and keep the
publish bounded — the two things the issue's caveats are about:

- the live index and the live chart files are never touched;
- a date the tracker captured live is never republished from the reconstruction;
- the published set is capped newest-first, older dates are pruned, and the drop is COUNTED;
- one bad date costs that date, not the run.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from small_cap_stack.config import Settings
from small_cap_stack.dashboard import index_entry
from small_cap_stack.dashboard_backfill import regenerate
from small_cap_stack.dashboard_recon import (
    RECON_SOURCE,
    publish_recon_charts,
    published_recon_dates,
    recon_charts_path,
    recon_index_path,
)
from small_cap_stack.portfolio import open_recon_store
from small_cap_stack.storage import Store

_NOW = datetime(2026, 8, 6, 14, 0, tzinfo=UTC)


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, **kw)  # type: ignore[call-arg]


def _seed_day(store: Store, day: date, symbol: str) -> None:
    """A day with one drawable opportunity — the same shape test_dashboard_backfill seeds."""
    t0 = datetime(day.year, day.month, day.day, 14, 0, tzinfo=UTC)
    oid = f"{day.isoformat()}:{symbol}"
    store.append(
        "opportunities",
        [
            {
                "opportunity_id": oid,
                "symbol": symbol,
                "con_id": 0,
                "trading_date": day,
                "first_seen_utc": t0,
                "first_rank": 0,
            }
        ],
        partition_date=day,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": oid, "symbol": symbol, "ts_utc": t0, "rank": 0}],
        partition_date=day,
    )
    bars = [(0, 5.0, 6.2, 4.9, 6.0), (1, 6.0, 6.1, 5.6, 5.7), (2, 5.7, 6.5, 5.7, 6.4)]
    store.append(
        "bars",
        [
            {
                "opportunity_id": oid,
                "symbol": symbol,
                "bar_start_utc": t0 + timedelta(minutes=5 * i),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": 1e3,
            }
            for i, o, h, low, c in bars
        ],
        partition_date=day,
    )


def _recon(s: Settings) -> Store:
    store = open_recon_store(s)
    assert store is not None
    return store


def _index(tmp_path: Path) -> dict:
    return json.loads(recon_index_path(tmp_path / "dashboard").read_text())


# --- the happy path ------------------------------------------------------------------------------


def test_publishes_a_dated_payload_and_a_tagged_index_row(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed_day(_recon(s), date(2026, 5, 4), "AZI")

    res = publish_recon_charts(s, now=_NOW)

    assert res.published == (date(2026, 5, 4),)
    payload = json.loads(recon_charts_path(tmp_path / "dashboard", date(2026, 5, 4)).read_text())
    assert payload["trading_date"] == "2026-05-04"
    assert payload["charts"] and payload["charts"][0]["symbol"] == "AZI"

    index = _index(tmp_path)
    assert index["source"] == RECON_SOURCE
    assert [d["date"] for d in index["dates"]] == ["2026-05-04"]
    # Provenance on the row itself, not only on the file it came from: an index whose rows are
    # untagged is one merge away from a reconstructed day reading as a captured one.
    assert index["dates"][0]["source"] == RECON_SOURCE
    assert index["dates"][0]["opportunities"][0]["symbol"] == "AZI"


def test_the_live_namespace_is_left_exactly_as_it_was(tmp_path: Path) -> None:
    """#430's separation restated for the publish path: `index.json` must not learn about recon."""
    s = _settings(tmp_path)
    live = Store(tmp_path)
    _seed_day(live, date(2026, 6, 29), "LIV")
    regenerate(date(2026, 6, 29), settings=s, store=live)
    before = (tmp_path / "dashboard" / "index.json").read_text()

    _seed_day(_recon(s), date(2026, 5, 4), "AZI")
    publish_recon_charts(s, now=_NOW)

    assert (tmp_path / "dashboard" / "index.json").read_text() == before
    assert not (tmp_path / "dashboard" / "charts" / "2026-05-04.json").exists()
    assert (tmp_path / "dashboard" / "charts" / "2026-06-29.json").exists()


def test_a_second_call_republishes_nothing(tmp_path: Path) -> None:
    """The ordinary call — every session already published — must be a no-op, because it runs after
    every harvested session for as long as the harvest lives."""
    s = _settings(tmp_path)
    _seed_day(_recon(s), date(2026, 5, 4), "AZI")
    publish_recon_charts(s, now=_NOW)

    res = publish_recon_charts(s, now=_NOW)
    assert res.published == ()
    assert res.indexed == (date(2026, 5, 4),)


def test_a_lost_index_rebuilds_rather_than_orphaning_the_payloads(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed_day(_recon(s), date(2026, 5, 4), "AZI")
    publish_recon_charts(s, now=_NOW)
    recon_index_path(tmp_path / "dashboard").unlink()

    res = publish_recon_charts(s, now=_NOW)
    # Keyed on the index row as well as the file: a date whose payload exists but whose row is gone
    # is invisible to the page, and "the file is there" is not the condition that matters.
    assert res.published == (date(2026, 5, 4),)
    assert [d["date"] for d in _index(tmp_path)["dates"]] == ["2026-05-04"]


# --- provenance + bounds -------------------------------------------------------------------------


def test_a_day_the_tracker_captured_live_is_never_republished(tmp_path: Path) -> None:
    """An overlap day exists (#428's calibration dates). Publishing it from the reconstruction would
    put two payloads under the same opportunity ids with nothing to tell them apart."""
    s = _settings(tmp_path)
    day = date(2026, 5, 4)
    _seed_day(Store(tmp_path), day, "AZI")
    _seed_day(_recon(s), day, "AZI")

    res = publish_recon_charts(s, now=_NOW)

    assert res.published == () and res.indexed == ()
    assert res.overlap_dates_dropped == 1
    assert not recon_charts_path(tmp_path / "dashboard", day).exists()


def test_the_first_fill_takes_the_newest_sessions_and_says_how_many_it_dropped(
    tmp_path: Path,
) -> None:
    """A call with no explicit dates fills the window newest-first, and never silently:
    `publish-dashboard` force-pushes this whole tree every 15 minutes, so an uncapped ~500-session
    harvest is a gigabyte a cycle."""
    s = _settings(tmp_path, recon_charts_max_dates=2)
    store = _recon(s)
    for i, d in enumerate([date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6)]):
        _seed_day(store, d, f"AZ{i}")

    res = publish_recon_charts(s, now=_NOW)

    assert res.published == (date(2026, 5, 6), date(2026, 5, 5))
    assert res.capped_dates_dropped == 1
    index = _index(tmp_path)
    assert index["capped_dates_dropped"] == 1 and index["max_dates"] == 2
    assert [d["date"] for d in index["dates"]] == ["2026-05-06", "2026-05-05"]


def test_the_harvest_stays_visible_after_the_window_fills(tmp_path: Path) -> None:
    """THE regression this window design exists for.

    The harvest walks BACKWARDS from the live record. Under a newest-DATE window every session
    after the budget filled would be older than everything already published, fall outside the
    window, and never publish at all — ~94% of a finished harvest permanently invisible in Results,
    with the per-session hook doing nothing but pay for two store reads after every session.

    Evicting by publish order instead means each night's sessions land and the oldest-published ones
    make room. Six sessions through a budget of three, fed newest-first exactly as the harvest feeds
    them.
    """
    s = _settings(tmp_path, recon_charts_max_dates=3)
    store = _recon(s)
    days = [date(2026, 5, 20 - i) for i in range(6)]  # 05-20 down to 05-15, the harvest's order
    for i, d in enumerate(days):
        _seed_day(store, d, f"AZ{i}")

    published: list[date] = []
    for d in days:  # one call per completed session, which is what `run` does
        published.extend(publish_recon_charts(s, dates=[d], now=_NOW).published)

    assert published == days, "a session the harvest completed was never published"
    # ...and what is resident is the three most recently harvested, not the three newest-dated.
    assert [e["date"] for e in _index(tmp_path)["dates"]] == [
        "2026-05-17",
        "2026-05-16",
        "2026-05-15",
    ]
    assert published_recon_dates(tmp_path / "dashboard") == days[-3:][::-1]


def test_an_evicted_session_can_always_be_brought_back(tmp_path: Path) -> None:
    """The cap decides how much is resident, not which half of the archive exists. Asking for an
    evicted date republishes it and moves it to the front of the window."""
    s = _settings(tmp_path, recon_charts_max_dates=2)
    store = _recon(s)
    days = [date(2026, 5, 6), date(2026, 5, 5), date(2026, 5, 4)]
    for i, d in enumerate(days):
        _seed_day(store, d, f"AZ{i}")
    for d in days:
        publish_recon_charts(s, dates=[d], now=_NOW)
    assert date(2026, 5, 6) not in published_recon_dates(tmp_path / "dashboard")

    res = publish_recon_charts(s, dates=[date(2026, 5, 6)], now=_NOW)

    assert res.published == (date(2026, 5, 6),)
    assert res.pruned == (date(2026, 5, 5),)  # the least recently published made room
    assert [e["date"] for e in _index(tmp_path)["dates"]] == ["2026-05-06", "2026-05-04"]


def test_a_date_that_falls_out_of_the_window_is_pruned_from_disk_and_the_index(
    tmp_path: Path,
) -> None:
    s = _settings(tmp_path, recon_charts_max_dates=2)
    store = _recon(s)
    _seed_day(store, date(2026, 5, 5), "AZI")
    _seed_day(store, date(2026, 5, 4), "BZI")
    publish_recon_charts(s, dates=[date(2026, 5, 5)], now=_NOW)
    publish_recon_charts(s, dates=[date(2026, 5, 4)], now=_NOW)
    assert published_recon_dates(tmp_path / "dashboard") == [date(2026, 5, 4), date(2026, 5, 5)]

    _seed_day(store, date(2026, 5, 6), "CZI")
    res = publish_recon_charts(s, dates=[date(2026, 5, 6)], now=_NOW)

    assert res.pruned == (date(2026, 5, 5),)  # published first, so evicted first
    assert published_recon_dates(tmp_path / "dashboard") == [date(2026, 5, 4), date(2026, 5, 6)]
    assert [d["date"] for d in _index(tmp_path)["dates"]] == ["2026-05-06", "2026-05-04"]


def test_zero_disables_the_cap(tmp_path: Path) -> None:
    s = _settings(tmp_path, recon_charts_max_dates=0)
    store = _recon(s)
    for i, d in enumerate([date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6)]):
        _seed_day(store, d, f"AZ{i}")

    res = publish_recon_charts(s, now=_NOW)
    assert len(res.published) == 3 and res.capped_dates_dropped == 0


def test_limit_bounds_one_call_without_leaving_a_half_written_index(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = _recon(s)
    for i, d in enumerate([date(2026, 5, 4), date(2026, 5, 5), date(2026, 5, 6)]):
        _seed_day(store, d, f"AZ{i}")

    first = publish_recon_charts(s, limit=1, now=_NOW)
    assert first.published == (date(2026, 5, 6),)  # newest-first, like the harvest itself
    assert [d["date"] for d in _index(tmp_path)["dates"]] == ["2026-05-06"]

    second = publish_recon_charts(s, limit=1, now=_NOW)
    assert second.published == (date(2026, 5, 5),)
    assert [d["date"] for d in _index(tmp_path)["dates"]] == ["2026-05-06", "2026-05-05"]


def test_a_date_the_reconstruction_does_not_hold_is_reported_as_such(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A completed session that flagged nothing writes no partition (`Store.append` skips empty
    records), so it never enters `collected_dates`. The hook must say "not harvested" rather than
    blame the budget — that log line is the only diagnostic this path emits, and with a 100k
    day-volume floor admitting thin sessions it will be a common outcome."""
    s = _settings(tmp_path)
    _seed_day(_recon(s), date(2026, 5, 5), "AZI")

    res = publish_recon_charts(s, dates=[date(2026, 5, 4)], now=_NOW)

    assert res.published == ()
    out = capsys.readouterr().out
    assert "not-harvested" in out and "2026-05-04" in out


# --- degradation ---------------------------------------------------------------------------------


def test_no_recon_store_configured_is_a_no_op(tmp_path: Path) -> None:
    s = _settings(tmp_path, recon_subdir="")
    res = publish_recon_charts(s, now=_NOW)
    assert res.published == () and res.indexed == ()
    assert not recon_index_path(tmp_path / "dashboard").exists()


def test_one_unreadable_date_costs_that_date_not_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller is a multi-week overnight job: losing the night over one bad partition would be a
    far worse trade than publishing the rest."""
    s = _settings(tmp_path)
    store = _recon(s)
    bad, good = date(2026, 5, 6), date(2026, 5, 5)
    _seed_day(store, bad, "AZI")
    _seed_day(store, good, "BZI")

    import small_cap_stack.dashboard_recon as mod

    real = mod.build_charts

    def _boom(st: Store, cfg: Settings, d: date, now: datetime) -> dict:
        if d == bad:
            raise RuntimeError("corrupt partition")
        return real(st, cfg, d, now)

    monkeypatch.setattr(mod, "build_charts", _boom)

    res = publish_recon_charts(s, now=_NOW)
    assert res.failed == (bad,) and res.published == (good,)
    # A date that never built at all is absent from the index rather than 404ing the page.
    assert [d["date"] for d in _index(tmp_path)["dates"]] == ["2026-05-05"]


def test_a_failed_REBUILD_keeps_the_payload_it_already_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first build that fails falls out of the index; a *re*-build that fails must not. The date
    still has valid published candles, and dropping them would turn a transient read error into a
    hole on the page. The two cases look identical in the code, so they are pinned separately."""
    s = _settings(tmp_path)
    day = date(2026, 5, 5)
    _seed_day(_recon(s), day, "AZI")
    publish_recon_charts(s, now=_NOW)

    import small_cap_stack.dashboard_recon as mod

    monkeypatch.setattr(
        mod, "build_charts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("transient"))
    )
    res = publish_recon_charts(s, dates=[day], now=_NOW)

    assert res.failed == (day,) and res.published == ()
    assert recon_charts_path(tmp_path / "dashboard", day).exists()
    assert [d["date"] for d in _index(tmp_path)["dates"]] == [day.isoformat()]


# --- the index row shape (the memory lesson of #261, restated for this path) ----------------------


def test_the_index_row_never_carries_the_payload(tmp_path: Path) -> None:
    """Each date is built, reduced to a row and dropped — the index must never be a list of full
    chart payloads, which is what made the archive backfill a memory bomb."""
    chart = {
        "opportunity_id": "2026-05-04:AZI",
        "symbol": "AZI",
        "run": 1,
        "run_count": 1,
        "triggered": True,
        "max_r": 2.5,
        "bars": [{"t": 1, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}] * 200,
    }
    entry = index_entry(date(2026, 5, 4), {"charts": [chart]}, source=RECON_SOURCE)
    assert set(entry) == {"date", "opportunities", "source"}
    assert set(entry["opportunities"][0]) == {
        "opportunity_id",
        "symbol",
        "run",
        "run_count",
        "triggered",
        "max_r",
    }
    # The live index keeps the shape it has always published — no `source` key appears there.
    assert set(index_entry(date(2026, 5, 4), {"charts": []})) == {"date", "opportunities"}
