"""The canonical strategy spec: rendered from ``Settings``, spliced into ``research/strategy.md``.

Every number describing what the system *does* has exactly one home — :class:`Settings` — and this
module is the only thing allowed to restate it in prose. `research/strategy.md` carries a generated
block between two HTML comment markers; `make strategy` rewrites it and
``tests/test_strategy_doc.py`` fails when the committed copy drifts from the code.

Why generate rather than write: the 2026-08-07 audit (#551) found the same rule stated seven ways
across README, `research/`, module docstrings, the dashboard, the reports and the tests — four
live price bands, and a float gate asserted in eight places that the engine has never applied. A
number a human retypes is a number that goes stale; the only durable fix is for the doc to be
unable to disagree with the code.

Scope: this renders *what the rules are*. `research/decisions.md` remains the record of **why** a
rule is what it is and when it changed — the log to this file's state.

Usage::

    python -m small_cap_stack.strategy_doc build          # rewrite the generated block
    python -m small_cap_stack.strategy_doc build --check  # non-zero exit if it is stale
"""

from __future__ import annotations

import argparse
from datetime import time
from pathlib import Path

from .config import Settings, get_settings

#: The spec, relative to the repo root. Not under `docs/` — that is the Pages frontend, not
#: documentation (CLAUDE.md). `research/` is the documentation root.
STRATEGY_DOC = "research/strategy.md"

#: The generated block is delimited by these markers. Text outside them is hand-written prose and
#: is never touched; text inside is overwritten wholesale.
BEGIN_MARKER = "<!-- BEGIN GENERATED — edit config.py, then run `make strategy` -->"
END_MARKER = "<!-- END GENERATED -->"


class StrategyDocError(ValueError):
    """The spec file is missing, or its generated markers are absent/malformed."""


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _pct(fraction: float) -> str:
    """A fraction rendered as a percentage: ``0.02`` -> ``2%``, ``0.0035`` -> ``0.35%``."""
    return f"{fraction * 100:g}%"


def _pct_points(value: float) -> str:
    """A value already expressed in percentage points: ``10.0`` -> ``10%``."""
    return f"{value:g}%"


def _et(value: time) -> str:
    return value.strftime("%H:%M") + " ET"


def _ticks(count: int, tick_size: float) -> str:
    plural = "tick" if count == 1 else "ticks"
    return f"{count} {plural} ({_money(count * tick_size)})"


def _joined(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _ordinal(n: int) -> str:
    """``3`` -> ``3rd``. Teens are all -th, which the mod-10 rule alone gets wrong."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _row(rule: str, value: str, source: str) -> str:
    """One table row. `source` is a `Settings` field name, or free text when the rule is
    structural (a gate with no threshold to tune) — the latter is passed through unquoted."""
    return f"| {rule} | {value} | {source} |"


def _field(name: str) -> str:
    return f"`{name}`"


_HEADER = "| Rule | Value | `Settings` field |\n|---|---|---|"


def render_scan(s: Settings) -> str:
    """What the IBKR scanner subscription actually asks for — the whole universe filter."""
    rows = [
        _row(
            "Price",
            f"{_money(s.scan_min_price)} – {_money(s.scan_max_price)}",
            _field("scan_min_price / scan_max_price"),
        ),
        _row("Today's change", f"> {_pct_points(s.scan_change_pct)}", _field("scan_change_pct")),
        _row(
            "Trailing 5-min volume",
            f"> {s.scan_min_5m_volume:,} (native `stVolume5minAbove`)",
            _field("scan_min_5m_volume"),
        ),
        _row(
            "Stock types excluded",
            _joined(s.scan_exclude_stock_types),
            _field("scan_exclude_stock_types"),
        ),
        _row(
            "Scan code",
            f"`{s.scan_code}` @ `{s.scan_location}`",
            _field("scan_code / scan_location"),
        ),
        _row("Rows per tick", f"{s.scan_max_rows} (IBKR hard cap)", _field("scan_max_rows")),
        _row(
            "Scan window",
            f"{_et(s.scan_start)} – {_et(s.scan_end)}",
            _field("scan_start / scan_end"),
        ),
        _row("Tick cadence", f"every {s.tick_interval_sec}s", _field("tick_interval_sec")),
    ]
    return "\n".join([_HEADER, *rows])


def render_engine(s: Settings) -> str:
    """The bull-flag shape gates, the entry/stop levels, and the two rules that can veto a fire."""
    rows = [
        _row("Pole", f"≤ {s.bull_flag_max_pole} higher highs", _field("bull_flag_max_pole")),
        _row(
            "Pole minimum move",
            f"≥ {_pct(s.bull_flag_min_pole_pct)}",
            _field("bull_flag_min_pole_pct"),
        ),
        _row(
            "Pole thrust body",
            f"an extension bar's body ≥ {_pct(s.bull_flag_pole_extension_min_body)} of its range",
            _field("bull_flag_pole_extension_min_body"),
        ),
        _row(
            "Pole step minimum",
            (
                f"each extension bar carries ≥ {_pct(s.bull_flag_pole_min_step_share)} of the pole"
                if s.bull_flag_pole_min_step_share > 0
                else "disabled"
            ),
            _field("bull_flag_pole_min_step_share"),
        ),
        _row("Consolidation", f"≤ {s.bull_flag_max_cons} candles", _field("bull_flag_max_cons")),
        _row(
            "Retracement",
            f"≤ {_pct(s.bull_flag_max_retracement)} of the pole",
            _field("bull_flag_max_retracement"),
        ),
        _row(
            "Peak upper wick",
            f"≤ {_pct(s.bull_flag_max_peak_wick)} of the bar's range",
            _field("bull_flag_max_peak_wick"),
        ),
        _row("Peak colour", "must close green", "— the `peak_green` gate"),
        _row("Peak volume", "> the consolidation's highest bar", "— the `vol_peak_gt_cons` gate"),
        _row("Consolidation low", "> the pole base", "— the `cons_holds_base` gate"),
        _row(
            "Trigger (decides *when*)",
            f"last consolidation high + {_ticks(s.bull_flag_trigger_offset_ticks, s.tick_size)}",
            _field("bull_flag_trigger_offset_ticks"),
        ),
        _row(
            "Fill (R is measured here)",
            f"last consolidation high + {_ticks(s.bull_flag_fill_offset_ticks, s.tick_size)}",
            _field("bull_flag_fill_offset_ticks"),
        ),
        _row("Stop", "the consolidation low", "— `R = fill − stop`"),
        _row(
            "Appearance",
            "the trigger bar must open at or after the first scanner hit",
            "— structural; see `bullflag/day.py`",
        ),
        _row(
            "Staleness",
            f"the trigger bar must open ≤ {s.entry_staleness_min} min after the first scanner hit",
            _field("entry_staleness_min"),
        ),
        _row(
            "Gap pole",
            (
                "the session's first bar may anchor a single-bar pole"
                if s.bull_flag_gap_pole
                else "disabled — a pole needs a higher high into its peak"
            ),
            _field("bull_flag_gap_pole"),
        ),
        _row(
            "Exhaustion",
            f"reject the {_ordinal(s.bull_flag_exhaustion_cap + 1)}+ contiguous cycle of the day",
            _field("bull_flag_exhaustion_cap"),
        ),
        _row(
            "Cycle volume floor",
            f"{s.scan_min_5m_volume // 2:,} (any bar in the cycle, pole or fade)",
            _field("scan_min_5m_volume") + " // 2",
        ),
        _row("Tick size", _money(s.tick_size), _field("tick_size")),
        _row(
            "ATR window",
            f"{s.bull_flag_atr_window} bars (score only, gates nothing)",
            _field("bull_flag_atr_window"),
        ),
        _row(
            "**Selection** — price band",
            f"{_money(s.select_price_min)} ≤ `entry_fill` ≤ {_money(s.select_price_max)}",
            _field("select_price_min / select_price_max"),
        ),
        _row(
            "**Selection** — trigger window",
            f"{_et(s.select_window_start)} ≤ trigger open < {_et(s.select_window_end)}",
            _field("select_window_start / select_window_end"),
        ),
    ]
    return "\n".join([_HEADER, *rows])


def render_book(s: Settings) -> str:
    """Execution: given the setups the engine selected, what happens to $500."""
    throttle = (
        "off (flat risk)"
        if s.portfolio_risk_rungs <= 1
        else (
            f"{s.portfolio_risk_rungs} rungs, "
            f"{s.portfolio_risk_step_days} same-direction days a step"
        )
    )
    window_days = (
        "all history"
        if s.portfolio_adaptive_window_days is None
        else f"trailing {s.portfolio_adaptive_window_days} days"
    )
    grid = ", ".join(f"{t:g}R" for t in s.portfolio_target_grid)
    slippage = (
        f"{_ticks(s.portfolio_exit_slippage_ticks, s.tick_size)} on stop / close exits, "
        "0 on the limit target"
    )
    rows = [
        _row(
            "Starting equity",
            _money(s.portfolio_start_equity_usd),
            _field("portfolio_start_equity_usd"),
        ),
        _row(
            "Trades per day",
            f"{s.portfolio_max_trades_per_day}, taken first-by-trigger-time",
            _field("portfolio_max_trades_per_day"),
        ),
        _row(
            "Risk target",
            f"{_pct(s.portfolio_risk_fraction)} of the day's opening equity",
            _field("portfolio_risk_fraction"),
        ),
        _row(
            "Notional cap",
            f"{_pct(s.portfolio_position_fraction)} of the day's opening equity",
            _field("portfolio_position_fraction"),
        ),
        _row("Exit target", f"{s.portfolio_target_r:g}R fallback", _field("portfolio_target_r")),
        _row(
            "Adaptive target",
            f"grid {grid}, fit over {window_days}, "
            f"≥ {s.portfolio_adaptive_min_samples} prior trades, "
            f"{s.portfolio_target_switch_z:g}σ paired margin to switch",
            _field("portfolio_target_grid / _adaptive_* / _target_switch_z"),
        ),
        _row(
            "Breakeven arm",
            "disabled" if s.portfolio_breakeven_r == 0 else f"{s.portfolio_breakeven_r:g}R",
            _field("portfolio_breakeven_r"),
        ),
        _row("Risk throttle", throttle, _field("portfolio_risk_rungs / _risk_step_days")),
        _row("Exit slippage", slippage, _field("portfolio_exit_slippage_ticks")),
        _row(
            "Excluded symbols",
            _joined(s.portfolio_exclude_symbols),
            _field("portfolio_exclude_symbols"),
        ),
    ]
    return "\n".join([_HEADER, *rows])


def render_not_gated(s: Settings) -> str:
    """Collected and published, but never consulted by any selection rule.

    This table is the point of the whole file. Eight surfaces asserted a float gate that has never
    run; the engine's only float consumer is a count in the EOD report (#551).
    """
    rows = [
        "| Collected | Where it goes | Does it filter? |",
        "|---|---|---|",
        f"| Float (`float_max_shares` = {s.float_max_shares:,}) | "
        "`fundamentals` dataset; the EOD report's `float_ok` **count**; the results/portfolio "
        "pages as context | **No.** `gates.py::float_gate` has one caller, `report.py` |",
        "| News (`has_recent_news`) | `news` dataset; the EOD report's `with_recent_news` "
        "**count** | **No.** `gates.py::news_gate` has the same single caller |",
        "| Short interest | not collected in Phase 1 | **No.** No source is wired |",
        "| Quality score (0–1) | published on the results page and the inspector | "
        "**No.** It ranks passing setups; it never rejects one |",
    ]
    return "\n".join(rows)


def render_block(s: Settings) -> str:
    """The whole generated block, markers included."""
    sections = [
        "### 1. The scan universe — what IBKR returns",
        "",
        render_scan(s),
        "",
        "### 2. The engine — what counts as a setup",
        "",
        render_engine(s),
        "",
        "### 3. The book — what actually gets traded",
        "",
        render_book(s),
        "",
        "### 4. Collected, never gated",
        "",
        render_not_gated(s),
    ]
    return "\n".join([BEGIN_MARKER, "", *sections, "", END_MARKER])


def splice(doc: str, block: str) -> str:
    """Replace the marked region of `doc` with `block`, leaving the prose around it untouched."""
    start = doc.find(BEGIN_MARKER)
    end = doc.find(END_MARKER)
    if start < 0 or end < 0:
        raise StrategyDocError(
            f"{STRATEGY_DOC} is missing its generated markers "
            f"({BEGIN_MARKER!r} / {END_MARKER!r}) — restore them before running this"
        )
    if end < start:
        raise StrategyDocError(f"{STRATEGY_DOC}: the END marker precedes the BEGIN marker")
    return doc[:start] + block + doc[end + len(END_MARKER) :]


def render_doc(path: Path, s: Settings) -> str:
    """The file's full text with a freshly-rendered generated block."""
    if not path.exists():
        raise StrategyDocError(f"{path} does not exist")
    return splice(path.read_text(encoding="utf-8"), render_block(s))


def write_doc(path: Path, s: Settings) -> bool:
    """Rewrite the generated block. Returns True when the file changed."""
    rendered = render_doc(path, s)
    if rendered == path.read_text(encoding="utf-8"):
        return False
    path.write_text(rendered, encoding="utf-8")
    return True


def doc_is_current(path: Path, s: Settings) -> bool:
    """True when the committed spec matches what `Settings` would render today."""
    return render_doc(path, s) == path.read_text(encoding="utf-8")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m small_cap_stack.strategy_doc")
    parser.add_argument("--path", type=Path, default=None, help=f"default: <repo>/{STRATEGY_DOC}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="rewrite the generated block from Settings")
    build.add_argument(
        "--check", action="store_true", help="don't write; exit non-zero if the block is stale"
    )

    args = parser.parse_args(argv)
    path = args.path or (_repo_root() / STRATEGY_DOC)
    settings = get_settings()

    if args.check:
        if doc_is_current(path, settings):
            print(f"{path} is up to date")
            return 0
        print(f"{path} is STALE — run `make strategy`")
        return 1

    changed = write_doc(path, settings)
    print(f"{'rewrote' if changed else 'no change to'} {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
