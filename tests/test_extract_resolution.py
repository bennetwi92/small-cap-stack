"""The 1-min entry-bar resolution, end to end through the store (#533).

`rmetrics.resolve_entry_bar` is unit-tested, but `portfolio.extract._resolved` — the part that
reads `bars_1m` out of a Store, filters it to one opportunity, dedups, sorts, and hands the result
to the resolver — had **no coverage at all**. That wiring is where this can be wrong in ways the
resolver's own tests cannot see: wrong `dt=` scoping, wrong dedup key, wrong column names, the
wrong fallback when `entry_trigger` is absent.

It is also trading logic in the strongest sense. A 5-min bar holding both the trigger and the stop
books the conservative reading — stopped on entry, −1R, no favourable excursion — and #583 measured
that reading **wrong 38%** of the time against 1-min data. So this decides whether a published −1R
is a measurement or an assumption, which is exactly what CLAUDE.md means by "the product".

`_resolved` is private on purpose (`extract_day_trades` is the seam), but the alternative here is
asserting on it through six layers of selection and sizing, where a wiring bug would surface as a
number being slightly off rather than as a failure that names the cause. Tested directly, with the
public path exercised alongside it so the two can't drift.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from small_cap_stack.capture import Bar
from small_cap_stack.portfolio import extract_day_trades
from small_cap_stack.portfolio.extract import _qualify, _resolved
from small_cap_stack.rmetrics import RMetrics
from small_cap_stack.storage import Store
from tests.support import opportunity_row, settings

_DAY = date(2026, 6, 29)
_T0 = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)  # 08:00 ET — pre-market
_OID = "2026-06-29:AZI"

# The 5-min series. Bar 3 is the entry bar and holds BOTH the trigger (7.00) and the stop (5.60):
# its high clears the breakout and its low breaches the stop, so `_measure` cannot order them and
# books the conservative −1R. That is the only shape `_resolved` is ever called for.
_ENTRY_IDX = 3
_STOP = 5.60
_FILL = 6.13


def _day_bars() -> list[Bar]:
    def b(i: int, o: float, h: float, low: float, c: float) -> Bar:
        return Bar(
            start=_T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c, volume=1000.0
        )

    return [
        b(0, 5.0, 5.8, 4.6, 5.7),  # launch
        b(1, 5.7, 6.5, 5.6, 6.4),  # pole peak
        b(2, 6.4, 6.1, 5.6, 5.7),  # consolidation — its low is the stop
        b(3, 5.7, 7.20, 5.50, 6.90),  # entry bar: clears the trigger AND breaches the stop
        b(4, 6.9, 7.60, 6.80, 7.50),  # the bar the conservative reading never gets to measure
    ]


def _rm() -> RMetrics:
    """The metrics `_resolved` reads. Only the five fields it touches need to be real."""
    return RMetrics(
        setup_found=True,
        triggered=True,
        takeable=True,
        entry_index=_ENTRY_IDX,
        entry_price=_FILL,
        entry_trigger=6.11,
        entry_fill=_FILL,
        stop=_STOP,
        initial_risk=_FILL - _STOP,
        max_r=-1.0,
    )


def _minute(i: int, o: float, h: float, low: float, c: float) -> dict[str, object]:
    """One `bars_1m` row, `i` minutes into the entry bar."""
    return {
        "opportunity_id": _OID,
        "symbol": "AZI",
        "bar_start_utc": _T0 + timedelta(minutes=5 * _ENTRY_IDX + i),
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": 100.0,
    }


def _store_with(tmp_path: Path, rows: list[dict[str, object]]) -> Store:
    store = Store(tmp_path)
    if rows:
        store.append("bars_1m", rows, partition_date=_DAY)
    return store


def test_no_minute_data_leaves_the_conservative_reading_alone(tmp_path: Path) -> None:
    """The modal case, and the one that must not invent anything: with no `bars_1m` partition at
    all, `_resolved` returns the original bars, no measurement, and says `unresolved`."""
    store = Store(tmp_path)
    bars = _day_bars()
    out_bars, measurement, outcome = _resolved(store, _DAY, _OID, bars, _rm())
    assert out_bars is bars  # the same list, not a copy with edits
    assert measurement == {}
    assert outcome == "unresolved"


def test_minute_data_for_another_symbol_does_not_leak_in(tmp_path: Path) -> None:
    """The filter is on `opportunity_id`, and a day's `bars_1m` partition holds every symbol's
    minutes. Reading another name's tape here would re-cut this trade against prices it never
    traded at — the provenance error #430 keeps two stores to avoid, one level down."""
    other = [
        dict(_minute(i, 9.0, 9.9, 8.9, 9.5), opportunity_id="2026-06-29:OTHER") for i in range(5)
    ]
    store = _store_with(tmp_path, other)
    out_bars, measurement, outcome = _resolved(store, _DAY, _OID, _day_bars(), _rm())
    assert outcome == "unresolved"  # our oid has no minutes, so nothing resolves
    assert measurement == {}


def test_a_trade_that_ran_is_recut_and_remeasured(tmp_path: Path) -> None:
    """The one outcome that changes a number. The trigger prints in minute 0 and the stop is never
    touched inside the bar, so the −1R assumption was wrong: the entry bar is replaced by the span
    actually traded and the trade is re-measured from there — reaching bar 4's 7.60 high, which the
    conservative reading never sees."""
    rows = [
        _minute(0, 5.70, 7.20, 5.70, 7.00),  # trigger prints; low stays above the 5.60 stop
        _minute(1, 7.00, 7.10, 6.80, 6.90),
        _minute(2, 6.90, 7.00, 6.85, 6.90),
    ]
    store = _store_with(tmp_path, rows)
    out_bars, measurement, outcome = _resolved(store, _DAY, _OID, _day_bars(), _rm())

    assert outcome == "ran"
    assert out_bars is not _day_bars()  # a re-cut series
    recut = out_bars[_ENTRY_IDX]
    assert recut.low > _STOP  # the synthetic bar cannot contain the stop, or it wouldn't have run
    assert measurement, "a `ran` verdict must produce a measurement, not an empty dict"
    assert measurement["max_r"] is not None and float(measurement["max_r"]) > 0  # type: ignore[arg-type]


def test_a_stop_taken_later_in_the_bar_confirms_the_assumption(tmp_path: Path) -> None:
    """`confirmed_stop`: the trigger printed, then the stop went, inside the same 5 minutes. The
    published −1R was right — but it is now evidence rather than a default, and nothing changes."""
    rows = [
        _minute(0, 5.70, 7.20, 6.00, 7.00),  # fills, stop untouched in this minute
        _minute(1, 7.00, 7.05, 5.50, 5.55),  # then the stop goes
    ]
    store = _store_with(tmp_path, rows)
    bars = _day_bars()
    out_bars, measurement, outcome = _resolved(store, _DAY, _OID, bars, _rm())
    assert outcome == "confirmed_stop"
    assert out_bars is bars and measurement == {}  # conservative reading stands, untouched


def test_a_minute_holding_both_levels_stays_ambiguous(tmp_path: Path) -> None:
    """Resolution narrows the ambiguity, it does not remove it. When the *filling* minute's own low
    breaches the stop, 1-min bars can't order them either — and the conservative reading stands
    rather than being upgraded on a coin flip."""
    rows = [_minute(0, 5.70, 7.20, 5.50, 6.00)]  # this minute holds the trigger and the stop
    store = _store_with(tmp_path, rows)
    bars = _day_bars()
    out_bars, measurement, outcome = _resolved(store, _DAY, _OID, bars, _rm())
    assert outcome == "ambiguous_same_minute"
    assert out_bars is bars and measurement == {}


def test_minutes_outside_the_entry_bar_are_ignored(tmp_path: Path) -> None:
    """The resolver is handed the whole day's minutes and windows them itself. A minute from a
    *later* 5-min bar must not resolve this one — it would import a trigger that hadn't happened
    yet, which is look-ahead of the plainest kind."""
    rows = [
        # Nothing inside the entry bar's own five minutes...
        dict(_minute(0, 5.70, 5.80, 5.65, 5.75), bar_start_utc=_T0 + timedelta(minutes=5 * 4)),
        dict(_minute(1, 5.75, 7.20, 5.70, 7.00), bar_start_utc=_T0 + timedelta(minutes=5 * 4 + 1)),
    ]
    store = _store_with(tmp_path, rows)
    _, measurement, outcome = _resolved(store, _DAY, _OID, _day_bars(), _rm())
    assert outcome == "unresolved"
    assert measurement == {}


def test_duplicate_minute_rows_are_deduped_on_read(tmp_path: Path) -> None:
    """Raw rows are append-only and a re-run re-fetches, so the same minute can be stored twice
    (`store raw, compute derived on read`). The re-cut bar sums volume and takes its close from the
    *last* minute, so a replayed duplicate would inflate the volume of a bar the book publishes.

    Asserted on the recut bar, not on the store: my first version checked `store.read(...).height`,
    which is true whether or not `_resolved` dedups anything. The two rows below differ in volume
    and close precisely so `keep="first"` is distinguishable from keeping both or keeping the last.
    """
    first = _minute(0, 5.70, 7.20, 5.70, 7.00)
    later_refetch = dict(first, close=6.50, volume=999.0)  # same minute, different values
    store = _store_with(tmp_path, [first, later_refetch, _minute(1, 7.00, 7.10, 6.90, 7.05)])

    out_bars, _, outcome = _resolved(store, _DAY, _OID, _day_bars(), _rm())
    assert outcome == "ran"
    recut = out_bars[_ENTRY_IDX]
    # Two distinct minutes at 100.0 each — not three rows, and not 1099.0.
    assert recut.volume == 200.0
    assert recut.close == 7.05  # minute 1's close, i.e. the duplicate did not become "last"


def test_the_trigger_not_the_fill_decides_whether_we_are_in(tmp_path: Path) -> None:
    """`entry_trigger` is the 1-tick break; `entry_fill` is the pessimistic +3-tick price used for
    R. A minute that reaches the trigger but not the pessimistic fill still means we were IN — so
    the resolver must window on the trigger. Falling back to the fill would refuse real entries."""
    rows = [_minute(0, 5.70, 6.12, 5.90, 6.05)]  # clears trigger 6.11, never reaches fill 6.13
    store = _store_with(tmp_path, rows)
    _, _, outcome = _resolved(store, _DAY, _OID, _day_bars(), _rm())
    assert outcome == "ran"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("takeable", False),
        ("entry_index", None),
        ("entry_price", None),
        ("entry_fill", None),
        ("stop", None),
        ("initial_risk", None),
        ("initial_risk", 0.0),
        ("initial_risk", -0.5),
    ],
)
def test_qualify_rejects_every_incomplete_candidate(field: str, value: object) -> None:
    """`_qualify` is the selection source of truth (CLAUDE.md), and each of its guards was
    uncovered. A None slipping through here isn't a wrong trade — it's a crash in sizing, or worse
    a division by a zero risk that publishes an infinite R."""
    from dataclasses import replace

    def call(m: RMetrics) -> bool:
        return _qualify(
            m.entry_index, m.entry_price, m.entry_fill, m.stop, m.initial_risk, m.takeable
        )

    assert call(_rm()), "the control must qualify, or every case below passes for free"
    assert not call(replace(_rm(), **{field: value}))  # type: ignore[arg-type]


# --- the public path: extract_day_trades wiring resolution in (#533) -------------------------
# Everything above tests `_resolved` directly. These go through `extract_day_trades`, because the
# decision to *call* it — `resolve_store is not None and rm.same_bar_stop` — is its own uncovered
# branch, and it is the one deciding whether a published trade carries a resolution at all.

_SAME_BAR_STOP = [
    (0, 5.00, 5.80, 4.60, 5.70, 50_000),
    (1, 5.70, 6.50, 5.60, 6.40, 300_000),  # pole peak
    (2, 6.40, 6.10, 5.60, 5.70, 100_000),  # consolidation — its low 5.60 is the stop
    (3, 5.70, 6.60, 5.50, 5.60, 120_000),  # entry bar: clears breakout 6.10 AND breaks the stop
    (4, 5.60, 5.90, 5.40, 5.80, 100_000),
]


def _seed_same_bar_day(store: Store) -> None:
    """A takeable pre-market setup whose entry bar holds both the trigger and the stop."""
    store.append(
        "opportunities",
        [
            opportunity_row(
                _OID,
                "AZI",
                trading_date=_DAY,
                first_seen=_T0,
                con_id=1,
                rank=0,
            )
        ],
        partition_date=_DAY,
    )
    store.append(
        "bars",
        [
            {
                "opportunity_id": _OID,
                "symbol": "AZI",
                "bar_start_utc": _T0 + timedelta(minutes=5 * i),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": v,
            }
            for i, o, h, low, c, v in _SAME_BAR_STOP
        ],
        partition_date=_DAY,
    )
    store.append(
        "scanner_hits",
        [{"opportunity_id": _OID, "symbol": "AZI", "ts_utc": _T0, "rank": 0}],
        partition_date=_DAY,
    )


def test_extract_leaves_the_trade_alone_when_no_resolve_store_is_given(tmp_path: Path) -> None:
    """The default. Resolution is opt-in — the live store holds no 1-min bars — so passing none
    must publish the conservative reading untouched rather than erroring or half-resolving."""
    store = Store(tmp_path / "live")
    _seed_same_bar_day(store)
    trades = extract_day_trades(store, settings(), _DAY)
    assert len(trades) == 1
    assert not trades[0].entry_resolution  # nothing claimed


def test_extract_resolves_a_same_bar_trade_when_given_a_minute_store(tmp_path: Path) -> None:
    """The wiring, end to end: a same-bar-stop trade plus a `resolve_store` holding the minutes
    comes back carrying the resolver's verdict. This is the branch deciding whether a -1R on the
    published book is a measurement or an assumption (#583)."""
    store = Store(tmp_path / "live")
    _seed_same_bar_day(store)
    recon = _store_with(
        tmp_path / "recon",
        [
            _minute(0, 5.70, 6.60, 5.70, 6.50),  # trigger prints, stop untouched
            _minute(1, 6.50, 6.55, 6.20, 6.30),
        ],
    )
    trades = extract_day_trades(store, settings(), _DAY, resolve_store=recon)
    assert len(trades) == 1
    assert trades[0].entry_resolution == "ran"


def test_extract_returns_nothing_for_a_day_with_no_opportunities(tmp_path: Path) -> None:
    """An empty day is normal — a holiday, or a session the scanner surfaced nothing on — and must
    be an empty list rather than a read of a partition that isn't there."""
    assert extract_day_trades(Store(tmp_path), settings(), _DAY) == []


def test_an_opportunity_with_no_bars_is_skipped(tmp_path: Path) -> None:
    """Discovery and bar capture are separate jobs (#62), so a name flagged late in the session can
    exist with no bars stored against it. It must be skipped, not sink the whole day: one missing
    symbol cannot cost the book every other trade that session."""
    store = Store(tmp_path)
    _seed_same_bar_day(store)
    store.append(
        "opportunities",
        [
            opportunity_row(
                "2026-06-29:NOBARS",
                "NOBARS",
                trading_date=_DAY,
                first_seen=_T0,
                con_id=2,
                rank=1,
            )
        ],
        partition_date=_DAY,
    )
    assert [t.symbol for t in extract_day_trades(store, settings(), _DAY)] == ["AZI"]
