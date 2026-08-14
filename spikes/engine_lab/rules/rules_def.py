"""The feature vocabulary a selection rule may draw on — and the two features it may NOT.

## Two features in `TRIGGER_TIME_SAFE` are lookahead in practice

`common.TRIGGER_TIME_SAFE` is a list of columns *measured at or before the trigger bar*. Two of
its entries do not actually satisfy that on this population, and both are near the top of any
naive feature ranking, so they are excluded here by name:

- **`first_rank`** — in the `recon` half (which is 100% of DEV and VAL) rank is assigned in
  `harvest/prefilter.py::_ranked`, which sorts the day's candidates by
  `day_change_pct = day_high / prev_close - 1` **off the daily bar**. That is the whole session's
  high: rank 1 means "the biggest mover of the day", knowable only at the close. Measured
  within-day rank correlation against `day_high / day_open` is **-0.59**. It is also not the same
  quantity as the live `first_rank`, which is IBKR's intraday `TOP_PERC_GAIN` position at the
  moment of appearance — so a threshold fitted on recon rank could not transfer even if it were
  causal. Its raw "signal" is the strongest in the panel (top decile +0.195R gross vs -0.53R in
  the bottom), which is exactly what a leak looks like.
- **`n_scanner_hits`** — `spikes/regime_panel.py` sets it to `osub.height`, the count of *all*
  scanner hits for that opportunity over the whole capture window, including every hit after the
  trigger. The causal version is `hits_before_trigger`, which is also in the panel and carries
  much less signal (top decile +0.115R gross vs -0.065R).

`common.py` is deliberately left unedited — three agents share it and adding these to
`OUTCOME_COLS` would start raising inside their runs mid-flight.
"""

from __future__ import annotations

#: In `TRIGGER_TIME_SAFE` but demonstrably not decidable at trigger time. See module docstring.
LEAKY = frozenset({"first_rank", "n_scanner_hits"})

#: Numeric features a rule may use, with the direction that step 1 found favourable
#: (+1 = bigger is better, -1 = smaller is better, 0 = no prior).
CANDIDATE_FEATURES: dict[str, int] = {
    "stop_pct": +1,
    "entry_fill": +1,
    "planned_risk": +1,
    "pole_pct": +1,
    "ext_at_peak": +1,
    "ext_at_trigger": +1,
    "runup_pre_appearance": +1,
    "cons_len": -1,
    "pole_len": 0,
    "retracement": 0,
    "score": 0,
    "staleness_delay_min": -1,
    "trigger_et_min": 0,
    "first_hit_et_min": 0,
    "hits_before_trigger": 0,
    "vol_share_pole": 0,
    "rvol_pole": +1,
    "range_before_pole_pct": 0,
    "cum_dollar_vol_to_trigger": +1,
    "cum_volume_to_trigger": 0,
    "pole_volume": 0,
    "day_open": +1,
    "bars_before_pole": 0,
    "shares_outstanding": -1,
}

BOOL_FEATURES = ["cons_vol_reducing", "pole_has_big_green", "passed"]

#: The seven tokens that appear in `failing_gates`. `passed` == failed none of them.
SHAPE_GATES = [
    "pole_height",
    "cons_len",
    "vol_peak_gt_cons",
    "peak_green",
    "cons_retracement",
    "wick_peak",
    "cons_holds_base",
]
