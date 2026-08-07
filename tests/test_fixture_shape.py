"""Fixtures must seed the store shape production actually writes (#523).

Every test seeded `opportunities` by hand, and **every one of them omitted `currency` and
`exchange`** — fields `capture.opportunity_record` always writes. Several seeded `news` without
`ts_utc`. So the whole suite exercised a narrower parquet schema than the one the readers meet on
the box, and a reader that came to depend on either column would pass here and fail there.

The fix is in `tests/support.py`: thin wrappers that delegate to the production record builders, so
a column added to one reaches the fixtures for free.

⚠️ Deliberately **not** the shared `seed_day(...)` #523 originally proposed. The three seeders are
not three copies of one thing — `test_dashboard` seeds deliberate duplicates and a rank flip,
`test_report` a clean flag plus a no-setup control, `test_portfolio` a pre-market window — and
those differences are precisely what each asserts. One seeder serving all three needs enough flags
that the call site stops being readable, and the flags encode the very things each test is about.
The duplication worth removing was in the row *builders*, not the composition.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _test_files() -> list[Path]:
    return sorted(p for p in TESTS.glob("test_*.py") if p.name != Path(__file__).name)


def test_no_test_hand_writes_an_opportunities_row() -> None:
    """`"first_rank":` only appears in a hand-written literal — `opportunity_row` takes `rank=`."""
    offenders = [
        f"{p.name}:{i}"
        for p in _test_files()
        for i, ln in enumerate(p.read_text().splitlines(), 1)
        if '"first_rank":' in ln
    ]
    assert not offenders, (
        "hand-written `opportunities` rows — use `tests.support.opportunity_row`, which builds "
        "the row production actually writes:\n  " + "\n  ".join(offenders)
    )


def test_every_seeded_news_row_carries_a_parsed_timestamp() -> None:
    """`ts_utc` is what news recency is measured against (#97/#101). A row without it seeds `None`,
    which makes every recency assertion vacuously true — the test then passes whatever the code
    does.

    Rows that set `ts_utc` from a variable are fine and are left alone: `test_canary` drives its
    feed-age assertion off exactly that value, so routing it through the parser would change what
    the test measures rather than what it seeds.
    """
    offenders: list[str] = []
    for p in _test_files():
        text = p.read_text()
        for m in re.finditer(r'"article_id":', text):
            start = text.rfind("{", 0, m.start())
            if "ts_utc" not in text[start : m.end() + 200]:
                offenders.append(f"{p.name}:{text.count(chr(10), 0, m.start()) + 1}")
    assert not offenders, (
        "seeded `news` rows with no `ts_utc` — use `tests.support.news_row`, or set it explicitly "
        "if the test drives an age assertion off it:\n  " + "\n  ".join(offenders)
    )


def test_the_wrappers_produce_exactly_the_production_shape() -> None:
    """The wrappers are only worth having if they stay in step with the record functions. Compared
    as key sets against the real builders, so a column added to production fails here until the
    fixtures carry it too — which is the entire point."""
    from datetime import UTC, date, datetime

    from small_cap_stack.capture import (
        Candidate,
        NewsItem,
        news_record,
        opportunity_record,
        scanner_hit_record,
    )
    from tests.support import news_row, opportunity_row, scanner_hit_row

    ts = datetime(2026, 6, 29, 14, 0, tzinfo=UTC)
    cand = Candidate(symbol="AZI", con_id=1, exchange="NASDAQ", currency="USD", rank=0)

    assert set(opportunity_row("o", "AZI", trading_date=date(2026, 6, 29))) == set(
        opportunity_record(cand, "o", ts, date(2026, 6, 29))
    )
    assert set(scanner_hit_row("o", "AZI")) == set(scanner_hit_record("o", cand, ts))
    assert set(news_row("o", "AZI")) == set(
        news_record("o", "AZI", NewsItem(time="t", provider="p", headline="h", article_id="a"))
    )


def test_the_default_news_timestamp_actually_parses() -> None:
    """The hand-written rows used `time="t"`, which `parse_news_ts` returns None for — so `ts_utc`
    was null even where the column existed, and recency asserted nothing. The wrapper's default is
    a real IBKR timestamp string precisely so that stops being possible by accident."""
    from tests.support import news_row

    assert news_row("o", "AZI")["ts_utc"] is not None
    assert news_row("o", "AZI", time="t")["ts_utc"] is None  # still honest about an unparseable one


def test_the_scanner_finds_the_real_test_corpus() -> None:
    """A glob that matched nothing would make the two checks above pass on an empty list."""
    files = _test_files()
    assert len(files) > 30
    assert any("opportunity_row(" in p.read_text() for p in files)
