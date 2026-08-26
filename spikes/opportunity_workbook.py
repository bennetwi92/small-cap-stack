"""The opportunity workbook (#694) — the wide panel as a spreadsheet you can drive yourself.

    .venv/bin/python spikes/opportunity_workbook.py            # -> data/spikes/opportunities.xlsx
    .venv/bin/python spikes/opportunity_workbook.py --premarket-cut 570

## Why this exists

#690 answered "is there a regime?" with no, three times over — and pointed at the opportunity level
as the place a signal might actually live. The harnesses that followed (`regime_scan.py`,
`rule_sweep.py`) each hard-code the cuts they test and print a table, so every new question is a
code change and a round trip. This is the same population handed over as a spreadsheet: every
threshold is a cell, and the daily table and the summary recompute when you move one.

It reads `data/spikes/regime_panel.parquet` and writes an `.xlsx`. It computes almost nothing
itself — the arithmetic lives in the sheet's formulas, deliberately, so that what you see is what
Excel derived from the controls in front of you rather than something Python baked in and left you
to trust.

## The six sheets

- **Controls** — every threshold, as a named cell. Blank means "no limit".
- **Opportunities** — one row per setup, the wide feature table, plus the computed block
  (`Pass` / `Cand` / `SeqDay` / `Taken` / `TradeR`) that turns the controls into a book.
- **Daily** — one row per session: seen, passed, taken, R, cumulative R, hit and stop rates.
- **Summary** — the headline read, scored side by side against the shipped rules.
- **Buckets** — pick a feature, see the filtered population split into quintiles by it. This is the
  rule-finding surface: it is looking for a band where max R is better than the base rate.
- **Dictionary** — what every column means, where it came from, and what will mislead you.

## Three things about the population, restated here because they decide denominators

1. **The panel is triggered-only.** Every row fired. Setups that formed and never broke out are not
   in the file, so "hit rate" is per *entry*, never per *flag spotted*.
2. **`passed` is the shape verdict, not the take decision** — and only ~8% of rows clear it. A row
   with `passed=FALSE` is a malformed flag, not a rejected trade. `Shape passed only` on Controls
   defaults to Yes for that reason; turn it off to look at the whole tape.
3. **The panel was built rules-OFF** (`regime_panel.WIDE_OVERRIDES`). The price band, trigger
   window, minimum stop, exhaustion cap and staleness cutoff are all recorded as columns rather
   than applied. So the shipped book is a *filter over this file*, not a subset of it — which is
   what makes the Summary's shipped-rules baseline an honest comparison rather than a re-run.

## Two deliberate design choices you can argue with, but should know about

**Ordering is by trigger time, never by score.** `SeqDay` ranks a day's candidates by when they
broke out, so the 2-a-day cap takes the first two that pass. Ranking them against each other needs
the whole day's candidates, which you do not have at 07:00 — a filter is decidable at trigger time,
a ranking is not. Sorting by score would quietly become a lookahead result.

**Unknown values fail a threshold they cannot be checked against.** If you filter float below 20M,
a row with no float is not known to be under 20M, so it drops. That matters here because
`float_shares` is null on every recon row by design — EDGAR states shares *outstanding*, never
float. `Keep rows with unknown values` on Controls flips this globally when you would rather see
them.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import xlsxwriter
from xlsxwriter.workbook import Workbook
from xlsxwriter.worksheet import Worksheet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from small_cap_stack.config import Settings  # noqa: E402

PANEL_DEFAULT = Path("data/spikes/regime_panel.parquet")
OUT_DEFAULT = Path("data/spikes/opportunities.xlsx")
PLAIN_DEFAULT = Path("data/spikes/opportunities-plain.xlsx")

#: 09:15 ET in minutes past ET midnight — the live `select_window_end`, and the cut every #690
#: harness used to mean "pre-market". A control on the sheet, not a constant — this is its default.
PREMARKET_CUT = 555.0

TABLE = "tOpp"  # the Excel table name every formula in the workbook refers to


# --------------------------------------------------------------------------------------------
# Column spec — one row per panel column. Drives the sheet layout, the controls, and the glossary,
# so a column added to the panel needs one entry here and nothing else.
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Col:
    name: str  # the panel column
    title: str  # the sheet header
    group: str
    kind: (
        str  # num | int | price | pct | ratio | money | shares | r | time | dt | date | bool | text
    )
    desc: str
    filt: bool = False  # gets a Min/Max pair on Controls
    width: float = 12.0


IDENTITY = "Identity"
CONTEXT = "Context at trigger"
SETUP = "The setup"
SHAPE = "Shape"
SIZE = "Size & float"
TRIGGER = "Trigger"
DAYCTX = "Day context"
TAPE = "Tape"
OUTCOME = "Outcome"

COLUMNS: tuple[Col, ...] = (
    Col("dt", "Date", IDENTITY, "date", "Trading session.", width=11),
    Col(
        "source",
        "Source",
        IDENTITY,
        "text",
        "live = the tracker saw it; recon = rebuilt from vendor bars.",
        width=8,
    ),
    Col("symbol", "Symbol", IDENTITY, "text", "Ticker.", width=9),
    Col(
        "seg_id",
        "Run id",
        IDENTITY,
        "text",
        "Opportunity id, suffixed #n when the symbol ran more than once.",
        width=20,
    ),
    Col("run", "Run", IDENTITY, "int", "Which run of the day this is for the symbol.", width=6),
    Col("run_count", "Runs", IDENTITY, "int", "How many runs that symbol had that day.", width=6),
    # context
    Col(
        "first_hit_et_min",
        "Seen (min)",
        CONTEXT,
        "time",
        "First scanner appearance for THIS run, minutes past ET midnight. The 'time seen'.",
        filt=True,
        width=10,
    ),
    Col(
        "first_seen_utc",
        "First seen UTC",
        CONTEXT,
        "dt",
        "First appearance of the symbol that day (the whole day, not this run).",
        width=17,
    ),
    # ⚠️ Both of these are day aggregates and neither gets a Min/Max pair on Controls (#690
    # engine-lab). They read as context and are not: a filter on either is a rule that needs the
    # day to have finished, which is the one mistake this workbook exists to make impossible.
    Col(
        "n_scanner_hits_day",
        "Hits (day)",
        DAYCTX,
        "int",
        "Scanner appearances across the WHOLE day, hits after the break included. "
        "Not filterable — use 'Hits before' instead.",
        width=9,
    ),
    Col(
        "first_rank",
        "First rank",
        DAYCTX,
        "int",
        "Live rows only: the scanner's rank at first appearance. Null on recon, where the "
        "old value was derived from the whole day's move. Not filterable, and not a "
        "tradability test either — the 50-row cap never binds.",
        width=9,
    ),
    Col("n_day_bars", "Day bars", CONTEXT, "int", "5-minute bars in the run's window.", width=9),
    # setup
    Col(
        "entry_fill",
        "Target entry",
        SETUP,
        "price",
        "Breakout + 3 ticks — the conservative fill R is measured from. The 'target entry'.",
        filt=True,
        width=11,
    ),
    Col(
        "entry_trigger",
        "Trigger px",
        SETUP,
        "price",
        "Breakout + 1 tick — the mechanical fire. Not the R basis.",
        width=10,
    ),
    Col(
        "breakout_level",
        "Breakout",
        SETUP,
        "price",
        "High of the last consolidation candle.",
        width=10,
    ),
    Col("stop", "Stop", SETUP, "price", "The consolidation low.", width=10),
    Col(
        "planned_risk",
        "Risk $",
        SETUP,
        "price",
        "Target entry minus stop, in dollars per share.",
        width=9,
    ),
    Col(
        "stop_pct",
        "Stop %",
        SETUP,
        "pct",
        "Risk as a fraction of target entry. Shipped rules require >= 2.5%.",
        filt=True,
        width=9,
    ),
    # shape
    Col(
        "pole_len",
        "Pole bars",
        SHAPE,
        "int",
        "Bars in the pole (the run of strict higher highs).",
        filt=True,
        width=9,
    ),
    Col(
        "cons_len",
        "Cons bars",
        SHAPE,
        "int",
        "Bars in the consolidation (the flag).",
        filt=True,
        width=9,
    ),
    Col(
        "retracement",
        "Retrace %",
        SHAPE,
        "pct",
        "How far the flag gave back of the pole. 1.0 = all of it.",
        filt=True,
        width=10,
    ),
    Col(
        "passed",
        "Passed",
        SHAPE,
        "bool",
        "All SHAPE gates passed — a well-formed flag. NOT the take decision.",
        width=8,
    ),
    Col(
        "failing_gates",
        "Failing gates",
        SHAPE,
        "text",
        "Which shape gates it missed, when it missed any.",
        width=26,
    ),
    Col(
        "cycle_num",
        "Cycle",
        SHAPE,
        "int",
        "1 = a fresh move; N = the Nth pump of the day. Shipped rules cap at 2.",
        filt=True,
        width=7,
    ),
    Col(
        "total_significant_cycles",
        "Cycles (day)",
        SHAPE,
        "int",
        "Significant cycles across the whole day. Context, not a gate.",
        width=10,
    ),
    Col(
        "cons_vol_reducing",
        "Cons vol down",
        SHAPE,
        "bool",
        "Consolidation volume non-increasing.",
        width=11,
    ),
    Col(
        "pole_has_big_green",
        "Big green",
        SHAPE,
        "bool",
        "A strong-bodied green candle in the pole.",
        width=9,
    ),
    Col(
        "cons_has_range",
        "Cons has range",
        SHAPE,
        "bool",
        "The flag actually traded through a range, so the stop means something.",
        width=12,
    ),
    Col(
        "untraded_cons_bars",
        "Dead bars",
        SHAPE,
        "int",
        "Consolidation bars with zero volume and zero range.",
        width=9,
    ),
    Col(
        "halted_consolidation",
        "Halted",
        SHAPE,
        "bool",
        "A dead bar flanked by one that traded — the tape was halted through the level.",
        width=8,
    ),
    # size / float
    Col(
        "float_shares",
        "Float",
        SIZE,
        "shares",
        "Free float. NULL on every recon row by design — no historical float source exists.",
        filt=True,
        width=13,
    ),
    Col(
        "shares_outstanding",
        "Shares out",
        SIZE,
        "shares",
        "A CEILING on float, not float. EDGAR cover page on recon, FMP/yfinance on live.",
        filt=True,
        width=13,
    ),
    Col(
        "shares_source",
        "Shares src",
        SIZE,
        "text",
        "Which source the share count came from.",
        width=10,
    ),
    Col(
        "shares_as_of",
        "Shares as of",
        SIZE,
        "date",
        "Filing date the recon count was taken from. A wide gap from Date means a stale count.",
        width=11,
    ),
    Col(
        "short_percent",
        "Short %",
        SIZE,
        "pct",
        "Short interest as a fraction of float. yfinance only, live only.",
        width=9,
    ),
    # trigger
    Col(
        "trigger_et_min",
        "Entry (min)",
        TRIGGER,
        "time",
        "Breakout bar open, minutes past ET midnight. The 'time entry'.",
        filt=True,
        width=10,
    ),
    Col("trigger_utc", "Entry UTC", TRIGGER, "dt", "Breakout bar open, UTC.", width=17),
    Col(
        "staleness_delay_min",
        "Staleness",
        TRIGGER,
        "num",
        "Minutes from scanner appearance to the break. Shipped rules cap at 30.",
        filt=True,
        width=10,
    ),
    Col(
        "trigger_idx",
        "Trigger bar",
        TRIGGER,
        "int",
        "Index of the breakout bar within the run's window.",
        width=9,
    ),
    Col(
        "triggered",
        "Triggered",
        TRIGGER,
        "bool",
        "Always TRUE — the panel carries fired setups only.",
        width=9,
    ),
    # day context
    Col(
        "day_open",
        "Day open",
        DAYCTX,
        "price",
        "The 04:00 open — the reference every extension feature is measured from.",
        width=10,
    ),
    Col("day_high", "Day high", DAYCTX, "price", "Session high.", width=10),
    Col("day_low", "Day low", DAYCTX, "price", "Session low.", width=10),
    Col("day_volume", "Day vol", DAYCTX, "shares", "Shares traded across the session.", width=12),
    Col(
        "day_dollar_volume",
        "Day $vol",
        DAYCTX,
        "money",
        "Dollar volume across the WHOLE session. Not filterable — it is not known at 07:00. "
        "Use 'Cum $vol to trigger' as the liquidity proxy a rule may read.",
        width=14,
    ),
    Col(
        "pole_pct",
        "Pole %",
        DAYCTX,
        "pct",
        "Pole height as a fraction of its base.",
        filt=True,
        width=9,
    ),
    Col(
        "pole_volume", "Pole vol", DAYCTX, "shares", "Shares traded across the pole bars.", width=12
    ),
    # tape — every one of these is measured at or before the trigger bar (no lookahead)
    Col(
        "ext_at_trigger",
        "Ext @ entry",
        TAPE,
        "pct",
        "Target entry vs the day open. How extended you are buying.",
        filt=True,
        width=10,
    ),
    Col(
        "ext_at_peak", "Ext @ peak", TAPE, "pct", "Pole peak vs the day open.", filt=True, width=10
    ),
    Col(
        "runup_pre_appearance",
        "Runup pre-hit",
        TAPE,
        "pct",
        "Highest print before the scanner ever showed it, vs day open. How much you missed.",
        filt=True,
        width=11,
    ),
    Col(
        "rvol_pole",
        "RVOL pole",
        TAPE,
        "ratio",
        "Mean pole-bar volume over mean pre-pole-bar volume. The name's own baseline.",
        filt=True,
        width=10,
    ),
    Col(
        "vol_share_pole",
        "Pole vol share",
        TAPE,
        "pct",
        "Pole volume as a fraction of all volume to the trigger.",
        filt=True,
        width=11,
    ),
    Col(
        "range_before_pole_pct",
        "Pre-pole range",
        TAPE,
        "pct",
        "How much the name had already ranged before the pole started.",
        filt=True,
        width=11,
    ),
    Col(
        "bars_before_pole",
        "Bars pre-pole",
        TAPE,
        "int",
        "How deep into the session the pole began.",
        width=11,
    ),
    Col(
        "cum_volume_to_trigger",
        "Vol to entry",
        TAPE,
        "shares",
        "Shares traded up to and including the breakout bar.",
        width=13,
    ),
    Col(
        "cum_dollar_vol_to_trigger",
        "$vol to entry",
        TAPE,
        "money",
        "Dollar volume up to the breakout — liquidity you can actually check at entry.",
        filt=True,
        width=14,
    ),
    Col(
        "hits_before_trigger",
        "Hits pre-entry",
        TAPE,
        "int",
        "Scanner appearances at or before the break. Low = fresh attention.",
        filt=True,
        width=11,
    ),
    # outcome
    Col(
        "max_r",
        "Max R",
        OUTCOME,
        "r",
        "Best R reached before the stop. The outcome everything is judged on.",
        width=9,
    ),
    Col(
        "mae_r",
        "MAE R",
        OUTCOME,
        "r",
        "Worst excursion against you, in R, before the best.",
        width=9,
    ),
    Col("max_gain_pct", "Max gain %", OUTCOME, "pct", "Best move as a percentage.", width=10),
    Col("stopped_out", "Stopped", OUTCOME, "bool", "The stop was hit.", width=8),
    Col(
        "entry_price",
        "Actual fill",
        OUTCOME,
        "price",
        "The realised fill, gap-through included — may be worse than target entry.",
        width=10,
    ),
    Col("realised_risk", "Actual risk $", OUTCOME, "price", "Actual fill minus stop.", width=10),
    Col(
        "bars_to_max_r",
        "Bars to max",
        OUTCOME,
        "int",
        "5-minute bars from entry to the best price.",
        width=10,
    ),
    Col("stop_index", "Stop bar", OUTCOME, "int", "Bar index the stop was hit on.", width=9),
    Col(
        "same_bar_stop",
        "Same-bar stop",
        OUTCOME,
        "bool",
        "Entry and stop on the same bar — the fill is a coin toss on that one.",
        width=11,
    ),
    Col(
        "fill_above_entry_bar_high",
        "Gap fill",
        OUTCOME,
        "bool",
        "The modelled fill sits above the entry bar's high.",
        width=9,
    ),
    Col("opportunity_id", "Opp id", IDENTITY, "text", "Store key: YYYY-MM-DD:SYMBOL.", width=18),
)

BY_NAME = {c.name: c for c in COLUMNS}

#: Features offered on the Buckets sheet, in a sensible reading order.
BUCKET_FEATURES: tuple[str, ...] = (
    "stop_pct",
    "entry_fill",
    "retracement",
    "cons_len",
    "pole_len",
    "first_hit_et_min",
    "trigger_et_min",
    "staleness_delay_min",
    "cycle_num",
    "hits_before_trigger",
    "float_shares",
    "shares_outstanding",
    "cum_dollar_vol_to_trigger",
    "ext_at_trigger",
    "ext_at_peak",
    "runup_pre_appearance",
    "rvol_pole",
    "vol_share_pole",
    "range_before_pole_pct",
    "pole_pct",
)


# --------------------------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------------------------
NUM_FMT: dict[str, str] = {
    "date": "yyyy-mm-dd",
    "dt": "yyyy-mm-dd hh:mm",
    "time": "0",
    "price": "#,##0.0000",
    "pct": "0.0%",
    "ratio": "0.00",
    "num": "0.0",
    "int": "0",
    "r": "0.00",
    "shares": "#,##0",
    "money": "#,##0",
    "bool": None,
    "text": None,
}


def _formats(wb: Workbook) -> dict[str, Any]:
    f: dict[str, Any] = {}
    for kind, fmt in NUM_FMT.items():
        f[kind] = wb.add_format({"num_format": fmt} if fmt else {})
    f["hdr"] = wb.add_format(
        {
            "bold": True,
            "bg_color": "#1F2933",
            "font_color": "#F5F7FA",
            "border": 1,
            "text_wrap": True,
            "valign": "vcenter",
        }
    )
    f["title"] = wb.add_format({"bold": True, "font_size": 15})
    f["h2"] = wb.add_format({"bold": True, "font_size": 11, "bg_color": "#E4E7EB", "border": 1})
    f["label"] = wb.add_format({"align": "right"})
    f["note"] = wb.add_format(
        {"italic": True, "font_color": "#616E7C", "text_wrap": True, "valign": "top"}
    )
    f["wrap"] = wb.add_format({"text_wrap": True, "valign": "top"})
    f["input"] = wb.add_format({"bg_color": "#FFF9DB", "border": 1, "num_format": "General"})
    f["input_pct"] = wb.add_format({"bg_color": "#FFF9DB", "border": 1, "num_format": "0.0%"})
    f["input_date"] = wb.add_format(
        {"bg_color": "#FFF9DB", "border": 1, "num_format": "yyyy-mm-dd"}
    )
    f["clock"] = wb.add_format({"num_format": "hh:mm"})
    f["big"] = wb.add_format({"bold": True, "font_size": 12, "num_format": "0.00"})
    f["big_int"] = wb.add_format({"bold": True, "font_size": 12, "num_format": "#,##0"})
    f["big_pct"] = wb.add_format({"bold": True, "font_size": 12, "num_format": "0.0%"})
    f["pct"] = wb.add_format({"num_format": "0.0%"})
    f["r2"] = wb.add_format({"num_format": "0.00"})
    f["int0"] = wb.add_format({"num_format": "0"})
    return f


# --------------------------------------------------------------------------------------------
# Controls — every threshold as a named cell. `blank = no limit` throughout.
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Ctl:
    name: str  # the defined name formulas use
    label: str
    default: Any
    kind: str  # blank | pct | date | list | number
    help: str
    choices: tuple[str, ...] = ()


def _numeric_controls() -> list[Col]:
    return [c for c in COLUMNS if c.filt]


def build_controls(s: Settings, premarket_cut: float) -> list[tuple[str, list[Ctl]]]:
    """The Controls sheet, as ordered sections. Defaults reproduce the shipped population."""
    population = [
        Ctl(
            "ctl_source",
            "Source",
            "both",
            "list",
            "both / live / recon.",
            ("both", "live", "recon"),
        ),
        Ctl("ctl_date_from", "Date from", "", "date", "Blank = from the beginning of the record."),
        Ctl("ctl_date_to", "Date to", "", "date", "Blank = to the end of the record."),
        Ctl(
            "ctl_premarket",
            "Pre-market only",
            "Yes",
            "list",
            "Keep only setups first seen before the cut below.",
            ("Yes", "No"),
        ),
        Ctl(
            "ctl_premarket_cut",
            "  cut (ET minutes)",
            premarket_cut,
            "number",
            "555 = 09:15, the live select_window_end. 570 = 09:30, the bell.",
        ),
        Ctl(
            "ctl_passed_only",
            "Shape passed only",
            "Yes",
            "list",
            "Keep only well-formed flags. Off shows the whole tape — ~92% of rows are malformed.",
            ("Yes", "No"),
        ),
        Ctl(
            "ctl_require_range",
            "Require cons range",
            "Yes",
            "list",
            "Drop flags whose consolidation traded no range at all (the stop is an artifact).",
            ("Yes", "No"),
        ),
        Ctl(
            "ctl_excl_halted",
            "Exclude halted flags",
            "No",
            "list",
            "Drop flags with a halt through the level.",
            ("Yes", "No"),
        ),
        Ctl(
            "ctl_keep_unknown",
            "Keep rows with unknown values",
            "No",
            "list",
            "A blank feature cannot be checked against a threshold. No = it fails; Yes = a "
            "threshold ignores blanks. Matters most for Float, blank on every recon row.",
            ("Yes", "No"),
        ),
    ]
    book = [
        Ctl(
            "ctl_max_per_day",
            "Max trades per day",
            s.portfolio_max_trades_per_day,
            "number",
            "The capacity cap. Taken = the first N that pass, in trigger-time order.",
        ),
        Ctl(
            "ctl_one_per_symbol",
            "One entry per symbol",
            "No",
            "list",
            "Only the earliest passing run of a symbol that day is takeable. Defaults to No "
            "because the shipped book has no such rule — it fills both slots by trigger time and "
            "will take the same ticker twice. Turn it on to ask whether it should.",
            ("Yes", "No"),
        ),
        Ctl(
            "ctl_target_r",
            "Target R",
            2.0,
            "number",
            "The R a winner banks. TradeR = +Target if Max R reached it, else Stop R.",
        ),
        Ctl("ctl_stop_r", "Stop R", -1.0, "number", "The R a loser costs. Negative."),
    ]
    return [("Population", population), ("Book", book)]


def write_controls(
    ws: Worksheet, wb: Workbook, f: dict[str, Any], s: Settings, premarket_cut: float
) -> dict[str, str]:
    """Write the Controls sheet; return {defined-name: 'Controls'!$C$n} for every control."""
    ws.set_column("A:A", 2)
    ws.set_column("B:B", 30)
    ws.set_column("C:D", 14)
    ws.set_column("E:E", 78)
    ws.write("B2", "Controls", f["title"])
    ws.write(
        "B3",
        "Yellow cells are yours to change. Everything else in the workbook follows them. "
        "A blank Min or Max means no limit on that side.",
        f["note"],
    )
    ws.set_row(2, 30)

    refs: dict[str, str] = {}
    row = 4

    def define(name: str, r: int, c: int) -> None:
        ref = f"='Controls'!${chr(65 + c)}${r + 1}"
        wb.define_name(name, ref)
        refs[name] = ref

    for section, ctls in build_controls(s, premarket_cut):
        ws.write(row, 1, section, f["h2"])
        ws.write(row, 2, "", f["h2"])
        ws.write(row, 3, "", f["h2"])
        ws.write(row, 4, "", f["h2"])
        row += 1
        for c in ctls:
            ws.write(row, 1, c.label, f["label"])
            fmt = f["input_date"] if c.kind == "date" else f["input"]
            if c.default == "":
                ws.write_blank(row, 2, None, fmt)
            elif c.kind == "date" and isinstance(c.default, date):
                ws.write_datetime(row, 2, c.default, fmt)
            else:
                ws.write(row, 2, c.default, fmt)
            if c.kind == "list":
                ws.data_validation(row, 2, row, 2, {"validate": "list", "source": list(c.choices)})
            ws.write(row, 4, c.help, f["note"])
            define(c.name, row, 2)
            row += 1
        row += 1

    # Per-feature Min/Max. Two columns, so a threshold reads as a range rather than two settings.
    ws.write(row, 1, "Feature thresholds", f["h2"])
    ws.write(row, 2, "Min", f["h2"])
    ws.write(row, 3, "Max", f["h2"])
    ws.write(row, 4, "", f["h2"])
    row += 1
    for col in _numeric_controls():
        ws.write(row, 1, col.title, f["label"])
        pct_like = col.kind in {"pct"}
        fmt = f["input_pct"] if pct_like else f["input"]
        ws.write_blank(row, 2, None, fmt)
        ws.write_blank(row, 3, None, fmt)
        hint = col.desc
        if col.kind == "time":
            hint += "  (240 = 04:00, 555 = 09:15, 570 = 09:30)"
        if pct_like:
            hint += "  (enter as a percentage, e.g. 2.5%)"
        ws.write(row, 4, hint, f["note"])
        define(f"ctl_{col.name}_min", row, 2)
        define(f"ctl_{col.name}_max", row, 3)
        row += 1

    ws.freeze_panes(4, 0)
    return refs


# --------------------------------------------------------------------------------------------
# The filter formula
# --------------------------------------------------------------------------------------------
def _pass_formula() -> str:
    """One AND() over every control. Long, but it is the whole contract in one readable place.

    Each numeric bound is written so that a blank *control* is no limit and a blank *value* is a
    failure unless `Keep rows with unknown values` says otherwise — see the module docstring.
    """
    terms = [
        'OR(ctl_source="both",[@Source]=ctl_source)',
        'OR(ctl_date_from="",[@Date]>=ctl_date_from)',
        'OR(ctl_date_to="",[@Date]<=ctl_date_to)',
        'OR(ctl_premarket<>"Yes",[@[Seen (min)]]<ctl_premarket_cut)',
        'OR(ctl_passed_only<>"Yes",[@Passed])',
        'OR(ctl_require_range<>"Yes",[@[Cons has range]])',
        'OR(ctl_excl_halted<>"Yes",NOT([@Halted]))',
    ]
    for col in _numeric_controls():
        v = f"[@[{col.title}]]"
        lo, hi = f"ctl_{col.name}_min", f"ctl_{col.name}_max"
        terms.append(
            f'IF(ISNUMBER({v}),AND(OR({lo}="",{v}>={lo}),OR({hi}="",{v}<={hi})),'
            f'OR(AND({lo}="",{hi}=""),ctl_keep_unknown="Yes"))'
        )
    return "=IF(AND(" + ",".join(terms) + "),1,0)"


def _shipped_formula(s: Settings) -> str:
    """The shipped `takeable` verdict, rebuilt from panel columns.

    Mirrors ``regime_panel._shipped_takeable``.

    The thresholds are baked in at build time rather than read from a cell, on purpose: this column
    is the *baseline* your filter is scored against, so it must not move when you move a control.
    Rebuild the workbook after changing config.py and it picks up the new values.
    """
    win_start = s.select_window_start.hour * 60 + s.select_window_start.minute
    win_end = s.select_window_end.hour * 60 + s.select_window_end.minute
    return (
        "=IF(AND([@Triggered],[@Passed],[@[Cons has range]],"
        f"[@Cycle]<={s.bull_flag_exhaustion_cap},"
        f"[@Staleness]<={s.entry_staleness_min},"
        f"[@[Target entry]]>={s.select_price_min},[@[Target entry]]<={s.select_price_max},"
        f"[@[Entry (min)]]>={win_start},[@[Entry (min)]]<{win_end},"
        f"[@[Stop %]]>={s.select_min_stop_pct}),1,0)"
    )


def _book_columns(prefix: str, gate: str, cap: str, *, dedup: bool) -> list[tuple[str, str, str]]:
    """The (header, formula, kind) chain that turns a pass/fail column into a book.

    Shared by your filter and the shipped baseline so the two are scored the same way. Every
    COUNTIFS is guarded by the cheap column before it — most rows fail the filter, so the guard
    keeps a 5,000-row full-table scan off ~90% of them and the recalc stays quick.

    ``dedup`` says whether this chain honours the one-entry-per-symbol control. The shipped baseline
    passes ``False`` and means it: ``portfolio/sim.py`` has **no** per-symbol rule — the real book
    fills its two slots from the earliest candidates by trigger time and does not care whether both
    are the same ticker. Letting the baseline follow the control would have quietly re-scored the
    thing your filter is being compared against, every time you toggled it.
    """
    cand = f"{prefix}Cand"
    seq = f"{prefix}Seq"
    taken = f"{prefix}Taken"
    out: list[tuple[str, str, str]] = []
    if dedup:
        first = f"{prefix}First"
        out.append(
            (
                first,
                f"=IF([@{gate}]=0,0,IF(COUNTIFS({TABLE}[Date],[@Date],{TABLE}[Symbol],[@Symbol],"
                f'{TABLE}[{gate}],1,{TABLE}[Entry (min)],"<"&[@[Entry (min)]])=0,1,0))',
                "int",
            )
        )
        cand_f = f'=IF([@{gate}]=0,0,IF(ctl_one_per_symbol<>"Yes",1,[@{first}]))'
    else:
        cand_f = f"=IF([@{gate}]=0,0,1)"
    out += [
        (cand, cand_f, "int"),
        (
            seq,
            f'=IF([@{cand}]=0,"",COUNTIFS({TABLE}[Date],[@Date],{TABLE}[{cand}],1,'
            f'{TABLE}[Entry (min)],"<"&[@[Entry (min)]])+1)',
            "int",
        ),
        (taken, f"=IF(AND([@{cand}]=1,[@{seq}]<={cap}),1,0)", "int"),
        (
            f"{prefix}R",
            f'=IF([@{taken}]=0,"",IF([@[Max R]]>=ctl_target_r,ctl_target_r,ctl_stop_r))',
            "r",
        ),
    ]
    return out


# --------------------------------------------------------------------------------------------
# Sheet writers
# --------------------------------------------------------------------------------------------
def _cell_value(col: Col, v: Any) -> Any:
    if v is None:
        return None
    if col.kind == "dt" and isinstance(v, datetime):
        return v.replace(tzinfo=None)  # xlsxwriter wants naive datetimes; these are all UTC
    if col.kind == "time" and isinstance(v, int | float):
        return float(v)
    return v


def write_opportunities(
    ws: Worksheet, wb: Workbook, f: dict[str, Any], df: pl.DataFrame, s: Settings
) -> list[str]:
    """The wide table plus the computed block. Returns the final header list."""
    present = [c for c in COLUMNS if c.name in df.columns]
    headers = [c.title for c in present]

    computed: list[tuple[str, str, str]] = [
        ("Seen ET", '=IFERROR([@[Seen (min)]]/1440,"")', "clock"),
        ("Entry ET", '=IFERROR([@[Entry (min)]]/1440,"")', "clock"),
        ("Pass", _pass_formula(), "int"),
    ]
    computed += _book_columns("", "Pass", "ctl_max_per_day", dedup=True)
    computed += [("Shipped", _shipped_formula(s), "int")]
    computed += _book_columns("Ship", "Shipped", str(s.portfolio_max_trades_per_day), dedup=False)

    all_headers = headers + [h for h, _, _ in computed]
    n_rows = df.height
    last_col = len(all_headers) - 1

    # Data first, then add_table over it — xlsxwriter's documented order.
    rows = df.select([c.name for c in present]).rows()
    for r, rec in enumerate(rows, start=1):
        for c_idx, (col, val) in enumerate(zip(present, rec, strict=True)):
            v = _cell_value(col, val)
            if v is None:
                ws.write_blank(r, c_idx, None, f[col.kind])
            elif col.kind == "date" and isinstance(v, date):
                ws.write_datetime(r, c_idx, v, f["date"])
            elif col.kind == "dt" and isinstance(v, datetime):
                ws.write_datetime(r, c_idx, v, f["dt"])
            else:
                ws.write(r, c_idx, v, f[col.kind])

    table_cols = [{"header": c.title, "format": f[c.kind]} for c in present]
    table_cols += [{"header": h, "formula": fx, "format": f[kind]} for h, fx, kind in computed]
    ws.add_table(
        0,
        0,
        n_rows,
        last_col,
        {
            "name": TABLE,
            "columns": table_cols,
            "style": "Table Style Medium 1",
            "autofilter": True,
        },
    )

    for i, col in enumerate(present):
        ws.set_column(i, i, col.width)
    for j in range(len(present), len(all_headers)):
        ws.set_column(j, j, 9)
    ws.set_row(0, 32, f["hdr"])
    ws.freeze_panes(1, 4)

    # A taken row should be findable by eye, not only by filtering.
    taken_col = all_headers.index("Taken")
    letter = xlsxwriter.utility.xl_col_to_name(taken_col)
    ws.conditional_format(
        1,
        0,
        n_rows,
        last_col,
        {
            "type": "formula",
            "criteria": f"=${letter}2=1",
            "format": wb.add_format({"bg_color": "#E3FCEC"}),
        },
    )
    r_col = xlsxwriter.utility.xl_col_to_name(all_headers.index("R"))
    ws.conditional_format(
        f"{r_col}2:{r_col}{n_rows + 1}",
        {
            "type": "cell",
            "criteria": "<",
            "value": 0,
            "format": wb.add_format({"font_color": "#B91C1C", "num_format": "0.00"}),
        },
    )
    return all_headers


@dataclass(frozen=True)
class DayCol:
    key: str  # how other formulas refer to this column
    title: str
    formula: str  # `{r}` = this row; `{KEY}` = another Daily column's letter
    kind: str
    width: float


#: The per-session table. Written as keys rather than cell letters on purpose: an earlier draft
#: hand-indexed the columns and had Win rate dividing by the *Pass* count and Drawdown reading the
#: Peak column, neither of which any test could have caught — the file opens fine and quietly
#: reports the wrong numbers. Letters are now derived from position, once.
DAY_COLS: tuple[DayCol, ...] = (
    DayCol("seen", "Seen", f"=COUNTIFS({TABLE}[Date],$A{{r}},{TABLE}[Source],$B{{r}})", "int0", 8),
    DayCol(
        "pass",
        "Pass",
        f"=COUNTIFS({TABLE}[Date],$A{{r}},{TABLE}[Source],$B{{r}},{TABLE}[Pass],1)",
        "int0",
        8,
    ),
    DayCol(
        "taken",
        "Taken",
        f"=COUNTIFS({TABLE}[Date],$A{{r}},{TABLE}[Source],$B{{r}},{TABLE}[Taken],1)",
        "int0",
        8,
    ),
    DayCol(
        "r",
        "R",
        f"=SUMIFS({TABLE}[R],{TABLE}[Date],$A{{r}},{TABLE}[Source],$B{{r}},{TABLE}[Taken],1)",
        "r2",
        9,
    ),
    DayCol("cum", "Cum R", "=IF(ROW()=2,{r_}2,{cum}1+{r_}{r})", "r2", 10),
    DayCol("peak", "Peak", "=IF(ROW()=2,{cum}2,MAX({peak}1,{cum}{r}))", "r2", 9),
    DayCol("dd", "Drawdown", "={cum}{r}-{peak}{r}", "r2", 10),
    DayCol(
        "win",
        "Win rate",
        f"=IFERROR(COUNTIFS({TABLE}[Date],$A{{r}},{TABLE}[Source],$B{{r}},"
        f'{TABLE}[Taken],1,{TABLE}[R],">0")/{{taken}}{{r}},"")',
        "pct",
        9,
    ),
    DayCol(
        "stop",
        "Stop rate",
        f"=IFERROR(COUNTIFS({TABLE}[Date],$A{{r}},{TABLE}[Source],$B{{r}},"
        f'{TABLE}[Taken],1,{TABLE}[Stopped],TRUE)/{{taken}}{{r}},"")',
        "pct",
        9,
    ),
    DayCol(
        "mean_r",
        "Mean max R",
        f"=IFERROR(AVERAGEIFS({TABLE}[Max R],{TABLE}[Date],$A{{r}},"
        f'{TABLE}[Source],$B{{r}},{TABLE}[Taken],1),"")',
        "r2",
        11,
    ),
    DayCol(
        "best_r",
        "Best max R",
        f"=IFERROR(_xlfn.MAXIFS({TABLE}[Max R],{TABLE}[Date],$A{{r}},"
        f'{TABLE}[Source],$B{{r}},{TABLE}[Taken],1),"")',
        "r2",
        11,
    ),
    DayCol(
        "ship_taken",
        "Shipped taken",
        f"=COUNTIFS({TABLE}[Date],$A{{r}},{TABLE}[Source],$B{{r}},{TABLE}[ShipTaken],1)",
        "int0",
        11,
    ),
    DayCol(
        "ship_r",
        "Shipped R",
        f"=SUMIFS({TABLE}[ShipR],{TABLE}[Date],$A{{r}},{TABLE}[Source],$B{{r}},"
        f"{TABLE}[ShipTaken],1)",
        "r2",
        10,
    ),
)


def write_daily(ws: Worksheet, f: dict[str, Any], df: pl.DataFrame) -> tuple[int, dict[str, str]]:
    """One row per (session, source). Returns (n sessions, {key: column letter}) for Summary."""
    days = df.select("dt", "source").unique().sort(["dt", "source"])
    ws.write("A1", "Date", f["hdr"])
    ws.write("B1", "Source", f["hdr"])

    letters = {c.key: xlsxwriter.utility.xl_col_to_name(i) for i, c in enumerate(DAY_COLS, start=2)}
    # A formula template already spends `{r}` on the row number, so the R column is `{r_}` when it
    # is being formatted. Callers of the returned map still ask for it by its real key.
    fmt_letters = {k: v for k, v in letters.items() if k != "r"} | {"r_": letters["r"]}

    for i, c in enumerate(DAY_COLS, start=2):
        ws.write(0, i, c.title, f["hdr"])
        ws.set_column(i, i, c.width)
    ws.set_column("A:A", 11)
    ws.set_column("B:B", 8)
    ws.set_row(0, 30)

    for r, (d, src) in enumerate(days.rows(), start=2):
        ws.write_datetime(r - 1, 0, d, f["date"])
        ws.write(r - 1, 1, src)
        for i, c in enumerate(DAY_COLS, start=2):
            ws.write_formula(r - 1, i, c.formula.format(r=r, **fmt_letters), f[c.kind])
    ws.freeze_panes(1, 2)
    return days.height, letters


class _Ref:
    """Attribute access into a flat cell map, so a template can say ``{c.total_r}``.

    The point is that Summary's arithmetic names the row it means instead of counting rows to it.
    """

    def __init__(self, cells: dict[str, str], prefix: str) -> None:
        self._cells, self._prefix = cells, prefix

    def __getattr__(self, name: str) -> str:
        return self._cells[f"{self._prefix}.{name}"]


def write_summary(
    ws: Worksheet,
    wb: Workbook,
    f: dict[str, Any],
    n_days: int,
    n_rows: int,
    day: dict[str, str],
) -> None:
    last = n_days + 1
    ws.set_column("A:A", 2)
    ws.set_column("B:B", 30)
    ws.set_column("C:D", 15)
    ws.set_column("E:E", 62)
    ws.write("B2", "Summary", f["title"])
    ws.write(
        "B3",
        "Everything here follows the Controls sheet. The right-hand column is the "
        "rules the system ships today, computed the same way — that is the number a "
        "filter has to beat to be worth anything.",
        f["note"],
    )
    ws.set_row(2, 30)

    ws.write("B5", "", f["h2"])
    ws.write("C5", "Your filter", f["h2"])
    ws.write("D5", "Shipped rules", f["h2"])
    ws.write("E5", "", f["h2"])

    def rng(key: str) -> str:
        return f"Daily!${day[key]}$2:${day[key]}${last}"

    # Keyed, not hand-numbered: `{c.total_r}` resolves to the cell holding Total R wherever it ends
    # up. Reordering this list used to silently re-point half the arithmetic at the wrong row.
    rows: list[tuple[str, str, str, str, str, str]] = [
        (
            "sessions",
            "Sessions",
            f'=COUNTIF({rng("taken")},">0")',
            f'=COUNTIF({rng("ship_taken")},">0")',
            "big_int",
            "Sessions that produced at least one trade.",
        ),
        (
            "trades",
            "Trades",
            f"=SUM({rng('taken')})",
            f"=SUM({rng('ship_taken')})",
            "big_int",
            "Entries taken, after the per-day cap and the one-per-symbol rule.",
        ),
        (
            "tps",
            "Trades / session",
            f'=IFERROR({{c.trades}}/{n_days},"")',
            f'=IFERROR({{d.trades}}/{n_days},"")',
            "big",
            f"Over all {n_days} sessions in the file, not only the ones that traded.",
        ),
        (
            "total_r",
            "Total R",
            f"=SUM({rng('r')})",
            f"=SUM({rng('ship_r')})",
            "big",
            "Sum of the R column: +Target for a winner, Stop R for a loser.",
        ),
        (
            "rpt",
            "R / trade",
            '=IFERROR({c.total_r}/{c.trades},"")',
            '=IFERROR({d.total_r}/{d.trades},"")',
            "big",
            "The expectancy. Break-even is 0 — this is the number that matters.",
        ),
        (
            "rps",
            "R / session",
            f'=IFERROR({{c.total_r}}/{n_days},"")',
            f'=IFERROR({{d.total_r}}/{n_days},"")',
            "big",
            "What a session is worth on average.",
        ),
        (
            "win",
            "Win rate",
            f'=IFERROR(COUNTIFS({TABLE}[Taken],1,{TABLE}[R],">0")/{{c.trades}},"")',
            f'=IFERROR(COUNTIFS({TABLE}[ShipTaken],1,{TABLE}[ShipR],">0")/{{d.trades}},"")',
            "big_pct",
            "Fraction of entries that reached Target R. At a 2R target, break-even is 33%.",
        ),
        (
            "stop",
            "Stop rate",
            f'=IFERROR(COUNTIFS({TABLE}[Taken],1,{TABLE}[Stopped],TRUE)/{{c.trades}},"")',
            f'=IFERROR(COUNTIFS({TABLE}[ShipTaken],1,{TABLE}[Stopped],TRUE)/{{d.trades}},"")',
            "big_pct",
            "Fraction that hit the stop.",
        ),
        (
            "mean_r",
            "Mean max R",
            f'=IFERROR(AVERAGEIFS({TABLE}[Max R],{TABLE}[Taken],1),"")',
            f'=IFERROR(AVERAGEIFS({TABLE}[Max R],{TABLE}[ShipTaken],1),"")',
            "big",
            "Average best-R reached. Not bankable — you cannot sell the high.",
        ),
        (
            "best",
            "Best session",
            f'=IFERROR(MAX({rng("r")}),"")',
            f'=IFERROR(MAX({rng("ship_r")}),"")',
            "big",
            "",
        ),
        (
            "worst",
            "Worst session",
            f'=IFERROR(MIN({rng("r")}),"")',
            f'=IFERROR(MIN({rng("ship_r")}),"")',
            "big",
            "",
        ),
        (
            "dd",
            "Max drawdown",
            f'=IFERROR(MIN({rng("dd")}),"")',
            "",
            "big",
            "Deepest fall from a peak on the cumulative-R curve. Your filter only.",
        ),
        (
            "setups",
            "Setups in file",
            f"={n_rows}",
            "",
            "big_int",
            "Every fired setup in the record, before any filter.",
        ),
        (
            "passing",
            "Passing the filter",
            f"=COUNTIF({TABLE}[Pass],1)",
            f"=COUNTIF({TABLE}[Shipped],1)",
            "big_int",
            "Before the per-day cap and one-per-symbol — selection, not capacity.",
        ),
    ]
    first_row = 6  # 0-based; Excel row 7
    cells = {f"c.{key}": f"C{first_row + i + 1}" for i, (key, *_) in enumerate(rows)}
    cells |= {f"d.{key}": f"D{first_row + i + 1}" for i, (key, *_) in enumerate(rows)}
    for i, (_key, label, c1, c2, kind, note) in enumerate(rows, start=first_row):
        ws.write(i, 1, label, f["label"])
        ws.write_formula(i, 2, c1.format(**{"c": _Ref(cells, "c"), "d": _Ref(cells, "d")}), f[kind])
        if c2:
            ws.write_formula(
                i, 3, c2.format(**{"c": _Ref(cells, "c"), "d": _Ref(cells, "d")}), f[kind]
            )
        ws.write(i, 4, note, f["note"])

    chart = wb.add_chart({"type": "line"})
    chart.add_series(
        {
            "name": "Your filter",
            "categories": f"=Daily!$A$2:$A${last}",
            "values": f"=Daily!${day['cum']}$2:${day['cum']}${last}",
            "line": {"color": "#B8860B", "width": 1.5},
        }
    )
    chart.set_title({"name": "Cumulative R"})
    chart.set_legend({"position": "bottom"})
    chart.set_size({"width": 760, "height": 300})
    ws.insert_chart("B23", chart)


def write_buckets(ws: Worksheet, wb: Workbook, f: dict[str, Any]) -> None:
    """Split the filtered population into quintiles of one feature. The rule-finding surface."""
    ws.set_column("A:A", 2)
    ws.set_column("B:B", 18)
    ws.set_column("C:H", 13)
    ws.set_column("I:I", 54)
    ws.write("B2", "Buckets", f["title"])
    ws.write(
        "B3",
        "Pick a feature. The rows below split everything currently passing your filter "
        "into five equal-sized bands of it, so you can see whether the outcome actually "
        "changes across the range. A band worth acting on beats the All row by enough "
        "to survive costs — and does it in both halves of the record, which the Source "
        "control on Controls lets you check.",
        f["note"],
    )
    ws.set_row(2, 42)

    ws.write("B5", "Feature", f["label"])
    ws.write("C5", "stop_pct", f["input"])
    wb.define_name("ctl_bucket_feature", "='Buckets'!$C$5")
    # A range, not an inline list: Excel caps an inline validation list at 255 characters and these
    # names run past 800, which silently produces a dropdown with nothing in it.
    ws.data_validation("C5", {"validate": "list", "source": f"=$B$8:$B${7 + len(BUCKET_FEATURES)}"})
    ws.write(
        "E5",
        "Set 'Shape passed only' and the rest on Controls first — this only ever looks "
        "at rows with Pass = 1.",
        f["note"],
    )

    # The chosen column, as a reference the formulas below reuse.
    titles = [BY_NAME[n].title for n in BUCKET_FEATURES]
    ws.write_row("B7", ["feature", "title"], f["note"])
    for i, (n, t) in enumerate(zip(BUCKET_FEATURES, titles, strict=True)):
        ws.write(7 + i, 1, n)
        ws.write(7 + i, 2, t)
    ws.set_row(6, None, None, {"hidden": True, "level": 1})
    for i in range(len(BUCKET_FEATURES)):
        ws.set_row(7 + i, None, None, {"hidden": True, "level": 1})

    n = len(BUCKET_FEATURES)
    chosen = f"INDEX($C$8:$C${7 + n},MATCH(ctl_bucket_feature,$B$8:$B${7 + n},0))"
    headers = f"{TABLE}[[#Headers],[Date]:[Opp id]]"
    lookup = f"INDEX({TABLE},0,MATCH({chosen},{headers},0))"
    vals = f'_xlfn._xlws.FILTER({lookup},({TABLE}[Pass]=1)*ISNUMBER({lookup}),"")'
    rvals = f'_xlfn._xlws.FILTER({TABLE}[Max R],({TABLE}[Pass]=1)*ISNUMBER({lookup}),"")'

    hdr = ["Band", "From", "To", "n", "Mean max R", "Median max R", "Hit rate", "Stop rate"]
    for i, h in enumerate(hdr):
        ws.write(24, 1 + i, h, f["hdr"])
    ws.set_row(24, 30)

    # Quintile edges over the filtered population, then one row per band.
    for b in range(5):
        r = 25 + b
        lo_q, hi_q = b / 5, (b + 1) / 5
        ws.write(r, 1, f"Q{b + 1}")
        ws.write_dynamic_array_formula(
            r, 2, r, 2, f'=IFERROR(_xlfn.PERCENTILE.INC({vals},{lo_q}),"")', f["r2"]
        )
        ws.write_dynamic_array_formula(
            r, 3, r, 3, f'=IFERROR(_xlfn.PERCENTILE.INC({vals},{hi_q}),"")', f["r2"]
        )
        lo, hi = f"$C${r + 1}", f"$D${r + 1}"
        last = "" if b == 4 else "<"
        mask = (
            f"({vals}>={lo})*({vals}<{'=' if b == 4 else ''}{hi})"
            if last == "<"
            else f"({vals}>={lo})*({vals}<={hi})"
        )
        band = f'_xlfn._xlws.FILTER({rvals},{mask},"")'
        ws.write_dynamic_array_formula(r, 4, r, 4, f"=IFERROR(COUNT({band}),0)", f["int0"])
        ws.write_dynamic_array_formula(r, 5, r, 5, f'=IFERROR(AVERAGE({band}),"")', f["r2"])
        ws.write_dynamic_array_formula(r, 6, r, 6, f'=IFERROR(MEDIAN({band}),"")', f["r2"])
        ws.write_dynamic_array_formula(
            r, 7, r, 7, f'=IFERROR(SUM(--({band}>=ctl_target_r))/COUNT({band}),"")', f["pct"]
        )
        ws.write_dynamic_array_formula(
            r, 8, r, 8, f'=IFERROR(1-SUM(--({band}>=ctl_target_r))/COUNT({band}),"")', f["pct"]
        )

    r = 31
    ws.write(r, 1, "All", f["label"])
    ws.write_dynamic_array_formula(r, 4, r, 4, f"=IFERROR(COUNT({rvals}),0)", f["int0"])
    ws.write_dynamic_array_formula(r, 5, r, 5, f'=IFERROR(AVERAGE({rvals}),"")', f["r2"])
    ws.write_dynamic_array_formula(r, 6, r, 6, f'=IFERROR(MEDIAN({rvals}),"")', f["r2"])
    ws.write_dynamic_array_formula(
        r, 7, r, 7, f'=IFERROR(SUM(--({rvals}>=ctl_target_r))/COUNT({rvals}),"")', f["pct"]
    )
    ws.write(r, 8, "", f["pct"])
    ws.write(r, 9, "The base rate your bands have to beat.", f["note"])


def write_dictionary(
    ws: Worksheet, f: dict[str, Any], df: pl.DataFrame, s: Settings, premarket_cut: float
) -> None:
    ws.set_column("A:A", 2)
    ws.set_column("B:B", 18)
    ws.set_column("C:C", 16)
    ws.set_column("D:D", 92)
    ws.write("B2", "Dictionary", f["title"])

    traps = [
        (
            "Triggered only",
            "Every row in this file fired. Setups that formed and never broke out are not "
            "here, so a hit rate is per entry taken — never per flag spotted.",
        ),
        (
            "passed is shape, not the take decision",
            "`Passed` means the bull flag is well-formed. Roughly 8% of rows clear it. A row with "
            "Passed = FALSE is a malformed shape, not a trade we turned down.",
        ),
        (
            "The panel is built rules-OFF",
            "Price band, trigger window, minimum stop, exhaustion cap and staleness were "
            "all switched off when this data was generated, and recorded as columns "
            "instead. The shipped book is therefore a filter over this file. That is "
            "what makes the Summary's shipped column a like-for-like comparison.",
        ),
        (
            "Float is blank on recon, by design",
            "No historical float source exists to buy. Recon rows carry shares OUTSTANDING "
            "from the SEC cover page instead — a ceiling on float, not float. Do not "
            "treat the two as one column, and remember a threshold on Float drops every "
            "recon row unless 'Keep rows with unknown values' is Yes.",
        ),
        (
            "Ordering is by time, never by score",
            "Seq ranks a day's candidates by when they broke out. Ranking them against each other "
            "needs the whole day, which you do not have at the moment of entry — so a "
            "score-ordered cap would be a lookahead result dressed up as a rule.",
        ),
        (
            "Max R is not what you would have banked",
            "It is the best price reached before the stop. R is the bankable reading: "
            "+Target R if the move got there, Stop R if it did not.",
        ),
        (
            "Recalculation",
            "The Taken columns scan the whole table per row, so a control change takes a moment on "
            "5,000 rows. That is expected, not a hang.",
        ),
    ]
    ws.write(3, 1, "Read this first", f["h2"])
    ws.write(3, 2, "", f["h2"])
    ws.write(3, 3, "", f["h2"])
    r = 4
    for title, body in traps:
        ws.write(r, 1, title, f["label"])
        ws.write(r, 3, body, f["wrap"])
        ws.set_row(r, 30)
        r += 1

    r += 1
    ws.write(r, 1, "Shipped rules snapshot", f["h2"])
    ws.write(r, 2, "", f["h2"])
    ws.write(r, 3, "", f["h2"])
    r += 1
    snapshot = [
        ("Price band", f"${s.select_price_min:g} – ${s.select_price_max:g}"),
        ("Trigger window", f"{s.select_window_start:%H:%M} – {s.select_window_end:%H:%M} ET"),
        ("Minimum stop", f"{s.select_min_stop_pct:.1%} of entry"),
        ("Exhaustion cap", f"cycle <= {s.bull_flag_exhaustion_cap}"),
        ("Staleness cap", f"{s.entry_staleness_min} min from appearance to break"),
        ("Trades per day", str(s.portfolio_max_trades_per_day)),
        ("Pre-market cut", f"{int(premarket_cut) // 60:02d}:{int(premarket_cut) % 60:02d} ET"),
    ]
    for label, val in snapshot:
        ws.write(r, 1, label, f["label"])
        ws.write(r, 2, val)
        r += 1
    ws.write(r, 1, "", f["note"])
    ws.write(
        r,
        3,
        "Baked into the Shipped column when this workbook was built. Rebuild after "
        "changing config.py to pick up new values.",
        f["note"],
    )

    r += 2
    ws.write(r, 1, "Column", f["h2"])
    ws.write(r, 2, "Group", f["h2"])
    ws.write(r, 3, "Meaning", f["h2"])
    r += 1
    for col in COLUMNS:
        if col.name not in df.columns:
            continue
        ws.write(r, 1, col.title, f["label"])
        ws.write(r, 2, col.group)
        ws.write(r, 3, col.desc, f["wrap"])
        r += 1

    computed_docs = [
        (
            "Seen ET / Entry ET",
            "Computed",
            "The ET-minute columns as a clock, for reading. Filters use the minute columns.",
        ),
        ("Pass", "Computed", "1 when the row satisfies every control on the Controls sheet."),
        ("First", "Computed", "1 when this is the earliest passing run of that symbol that day."),
        ("Cand", "Computed", "Pass, and first-for-symbol when that rule is on."),
        ("Seq", "Computed", "Position of this candidate within its day, ordered by break time."),
        ("Taken", "Computed", "A candidate within the per-day cap. This is the book."),
        (
            "R",
            "Computed",
            "+Target R if Max R reached the target, else Stop R. Blank when not taken.",
        ),
        (
            "Shipped / Ship*",
            "Computed",
            "The same chain under the rules the system ships today — the baseline on Summary.",
        ),
    ]
    for name, group, body in computed_docs:
        ws.write(r, 1, name, f["label"])
        ws.write(r, 2, group)
        ws.write(r, 3, body, f["wrap"])
        r += 1


# --------------------------------------------------------------------------------------------
# Cross-check — the same book, computed in polars, so a formula bug is visible on the way out.
# --------------------------------------------------------------------------------------------
def crosscheck(
    df: pl.DataFrame, s: Settings, premarket_cut: float, target_r: float, stop_r: float
) -> None:
    """Recompute the DEFAULT controls here and print the answer for the sheet to be checked against.

    Deliberately an independent implementation rather than a shared helper: the point is to catch a
    wrong formula, and a shared code path would agree with itself.
    """
    d = df.filter(
        pl.col("first_hit_et_min").lt(premarket_cut) & pl.col("passed") & pl.col("cons_has_range")
    )
    d = d.sort(["dt", "trigger_et_min", "symbol"])
    # No per-symbol dedup: the default of that control is No, because the shipped book has no such
    # rule. Change the control and this number stops being the one to check against.
    d = d.filter(
        pl.col("trigger_et_min").rank("ordinal").over("dt").le(s.portfolio_max_trades_per_day)
    )
    r = pl.when(pl.col("max_r") >= target_r).then(target_r).otherwise(stop_r)
    d = d.with_columns(r.alias("trade_r"))
    total = float(d["trade_r"].sum())
    print(
        f"cross-check (default controls): taken={d.height}  total R={total:.2f}  "
        f"R/trade={total / d.height if d.height else 0:.3f}  sessions={d['dt'].n_unique()}",
        file=sys.stderr,
    )
    print("  ^ Summary must show these three. If it does not, a formula is wrong.", file=sys.stderr)


# --------------------------------------------------------------------------------------------
# Structural self-check. Nothing here recalculates the sheet — that needs Excel — but it does catch
# the failure mode this file actually hit: a formula naming a column or a control that does not
# exist. Excel does not refuse such a file, it opens it and shows #REF!/#NAME? in the one cell you
# were least likely to look at, so a typo in a table reference is otherwise silent all the way to
# the trader. Runs on every build; there is no flag to skip it.
# --------------------------------------------------------------------------------------------
#: Every structured reference in the file is a bracketed token — `tOpp[Max R]`, `[@[Stop %]]`, or
#: the `[[#This Row],Cand]` form xlsxwriter rewrites `[@Cand]` into. Brackets have no other use in
#: these formulas, so pulling every innermost `[...]` and dropping the `#`-prefixed keywords leaves
#: exactly the column names. Matching the reference *shapes* instead turned out to backtrack into
#: nonsense captures and report 4,000 failures on a sound file.
_BRACKETED = re.compile(r"\[([^\[\]]+)\]")
_NAME_REF = re.compile(r"\bctl_[a-z0-9_]+\b")
_KEYWORDS = ("#This Row", "#Headers", "#Data", "#All", "#Totals")


def verify(path: Path) -> None:
    """Re-open the written file and check every formula reference resolves. Raises on a bad one."""
    with zipfile.ZipFile(path) as z:
        book = z.read("xl/workbook.xml").decode()
        table = z.read("xl/tables/table1.xml").decode()
        sheets = [z.read(n).decode() for n in z.namelist() if n.startswith("xl/worksheets/sheet")]

    headers = set(re.findall(r'<tableColumn[^>]*\bname="([^"]+)"', table))
    defined = set(re.findall(r'<definedName name="([^"]+)"', book))
    problems: list[str] = []

    for xml in [table, *sheets]:
        for formula in re.findall(r"<f[^>]*>([^<]*)</f>", xml):
            text = (
                formula.replace("&quot;", '"')
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
            )
            for col in _BRACKETED.findall(text):
                if col in _KEYWORDS or col.startswith("#"):
                    continue
                if col not in headers:
                    problems.append(f"unknown table column {col!r} in: {text[:90]}")
            for name in _NAME_REF.findall(text):
                if name not in defined:
                    problems.append(f"undefined name {name!r} in: {text[:90]}")

    if problems:
        for p in sorted(set(problems))[:20]:
            print(f"  BROKEN: {p}", file=sys.stderr)
        raise SystemExit(f"{len(set(problems))} broken formula reference(s) in {path}")
    print(
        f"verified: every table column and control name referenced by a formula exists "
        f"({len(headers)} columns, {len(defined)} controls)",
        file=sys.stderr,
    )


# --------------------------------------------------------------------------------------------
def build(panel: Path, out: Path, premarket_cut: float) -> None:
    df = pl.read_parquet(panel)
    s = Settings()
    missing = [c.name for c in COLUMNS if c.name not in df.columns]
    if missing:
        print(
            f"note: panel has no {', '.join(missing)} — those columns are omitted", file=sys.stderr
        )
    df = df.sort(["dt", "trigger_et_min", "symbol"])

    out.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(out), {"default_date_format": "yyyy-mm-dd"})
    # Force a full recalculation the first time Excel opens the file: xlsxwriter writes formulas
    # with no cached result, so without this every computed cell reads 0 until something is edited.
    wb.set_calc_mode("auto")
    f = _formats(wb)

    ws_ctl = wb.add_worksheet("Controls")
    ws_opp = wb.add_worksheet("Opportunities")
    ws_day = wb.add_worksheet("Daily")
    ws_sum = wb.add_worksheet("Summary")
    ws_buk = wb.add_worksheet("Buckets")
    ws_dic = wb.add_worksheet("Dictionary")

    write_controls(ws_ctl, wb, f, s, premarket_cut)
    write_opportunities(ws_opp, wb, f, df, s)
    n_days, day_letters = write_daily(ws_day, f, df)
    write_summary(ws_sum, wb, f, n_days, df.height, day_letters)
    write_buckets(ws_buk, wb, f)
    write_dictionary(ws_dic, f, df, s, premarket_cut)
    ws_sum.activate()
    wb.close()

    by_src = df.group_by("source").agg(pl.len().alias("n"), pl.col("dt").n_unique().alias("days"))
    print(f"wrote {out}  ({df.height} setups, {n_days} sessions)", file=sys.stderr)
    print(by_src.sort("source"), file=sys.stderr)
    verify(out)
    crosscheck(df, s, premarket_cut, 2.0, -1.0)


def build_plain(panel: Path, out: Path) -> None:
    """Just the wide table: one sheet, one row per opportunity, no formulas and no controls.

    The whole workbook above is a strong opinion about how to explore this data. This is the same
    5,024 rows with none of that opinion attached — formatted, frozen, auto-filtered, and yours to
    pivot however you like. A CSV lands beside it for anything that is not Excel.
    """
    df = pl.read_parquet(panel).sort(["dt", "trigger_et_min", "symbol"])
    present = [c for c in COLUMNS if c.name in df.columns]

    out.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(out), {"default_date_format": "yyyy-mm-dd"})
    f = _formats(wb)
    ws = wb.add_worksheet("Opportunities")

    for r, rec in enumerate(df.select([c.name for c in present]).rows(), start=1):
        for i, (col, val) in enumerate(zip(present, rec, strict=True)):
            v = _cell_value(col, val)
            if v is None:
                ws.write_blank(r, i, None, f[col.kind])
            elif col.kind == "date" and isinstance(v, date):
                ws.write_datetime(r, i, v, f["date"])
            elif col.kind == "dt" and isinstance(v, datetime):
                ws.write_datetime(r, i, v, f["dt"])
            else:
                ws.write(r, i, v, f[col.kind])

    ws.add_table(
        0,
        0,
        df.height,
        len(present) - 1,
        {
            "name": TABLE,
            "columns": [{"header": c.title, "format": f[c.kind]} for c in present],
            "style": "Table Style Medium 1",
            "autofilter": True,
        },
    )
    for i, col in enumerate(present):
        ws.set_column(i, i, col.width)
    ws.set_row(0, 32, f["hdr"])
    ws.freeze_panes(1, 4)  # Date / Source / Symbol / Run id stay on screen
    wb.close()

    csv_out = out.with_suffix(".csv")
    df.select([c.name for c in present]).write_csv(csv_out)
    print(f"wrote {out} and {csv_out}", file=sys.stderr)
    print(
        f"  {df.height} opportunities x {len(present)} columns, {df['dt'].n_unique()} sessions",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--panel", type=Path, default=PANEL_DEFAULT)
    p.add_argument("--out", type=Path, default=OUT_DEFAULT)
    p.add_argument(
        "--plain",
        action="store_true",
        help="just the wide table — one sheet, no formulas, plus a CSV beside it",
    )
    p.add_argument(
        "--premarket-cut",
        type=float,
        default=PREMARKET_CUT,
        help="default for the pre-market control, in ET minutes (555 = 09:15)",
    )
    args = p.parse_args(argv)
    if not args.panel.exists():
        print(
            f"error: no panel at {args.panel}. Build it with:\n"
            f"  .venv/bin/python spikes/regime_panel.py build "
            f"--recon-store data/recon --store data/live",
            file=sys.stderr,
        )
        return 1
    if args.plain:
        out = args.out if args.out != OUT_DEFAULT else PLAIN_DEFAULT
        build_plain(args.panel, out)
    else:
        build(args.panel, args.out, args.premarket_cut)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
