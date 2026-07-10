# Engine v2 — implementation spec (pattern-spotting bull-flag detector)

> **Companion to `bull-flag.md`** (the *feature* spec — the "what"). This is the *implementation*
> spec — the "how": module layout, data model, function signatures, gating/scoring, and the
> migration that keeps `rmetrics` + the review workbench working. Locked decisions from
> `bull-flag.md §6` are treated as fixed here (pole/cons ≤ 4/4, `E` token in the consolidation only, entry = last
> cons high + 3 ticks).

---

## 1. Goals & non-goals

**Goals**
- Replace the single anchored detector (`bullflag.py::detect`) with the **tokenize → segment →
  extract → gate+score** pipeline from `bull-flag.md §1`.
- Emit a **feature vector** per setup (not just pass/fail), so the review page can *explain* a
  ranking and so features can migrate between gate/score roles from data.
- Stay a **pure, replayable** function of raw `Bar`s (CLAUDE.md: store-raw / compute-on-read) so all
  history recomputes retroactively — **no re-capture, no data deleted**.
- Remain **drop-in** for `rmetrics.py` and the review workbench (same entry/stop semantics, same
  fields those consumers read).

**Non-goals (this pass)**
- Fitting score weights (ship hand-set weights + per-feature contributions; fit later from review
  outcomes).
- The `LOC` area's scanner-join plumbing (`bull-flag.md §6.6`) — stubbed/recorded, not gated yet.
- Multi-timeframe / non-5-min bars.

---

## 2. Module layout

Split the one `bullflag.py` into a small package so each stage is independently testable and mypy
stays honest (`--strict`, package-only):

```
src/small_cap_stack/bullflag/
  __init__.py        # public API re-exports (detect, scan, Setup, FeatureVector, BullFlag shim)
  tokens.py          # Stage 1: Bar[] -> Token[]           (pure)
  segment.py         # Stage 2: Token[] -> Segment | None  (longest-match, pure)
  features.py        # Stage 3: (bars, Segment) -> FeatureVector  (pure; the six areas)
  gates.py           # Stage 4a: FeatureVector -> GateResult[]     (hard rejects)
  score.py           # Stage 4b: FeatureVector -> (score, contributions)
  detect.py          # orchestration: detect() end-anchored + scan() whole-day
  compat.py          # Setup -> legacy BullFlag shim for rmetrics/review during migration
```

`__init__.py` keeps the current import surface alive: `from .bullflag import BullFlag,
detect_with_settings` must still resolve (re-export from `compat`/`detect`).

---

## 3. Data model

All frozen dataclasses; all indices are into the *input* `bars` list.

```python
Token = Literal["H", "L", "E"]          # tokens.py

@dataclass(frozen=True)
class Segment:                          # segment.py — the pure structural match
    base_idx: int                       # b0, the launch bar (no token)
    peak_idx: int                       # last H of the pole = pole peak
    cons_end_idx: int                   # last consolidation bar (detection fires here)
    tokens: tuple[Token, ...]           # tokens for bars[base_idx+1 .. cons_end_idx]
    pole_len: int                       # count of H in the pole (1..4)
    cons_len: int                       # count of L/E in the consolidation (1..4)

@dataclass(frozen=True)
class FeatureVector:                    # features.py — the six areas of bull-flag.md §3
    # SHAPE
    pole_len: int
    cons_len: int
    cons_strictness: float              # frac of cons steps that are strict L (pole is all-H by rule)
    token_string: str                   # "HHLLL"
    # VOL
    peak_gt_cons: bool                  # max(pole.vol) > max(cons.vol)      [gate input]
    vol_ratio: float                    # max(pole.vol) / max(cons.vol)
    cons_vol_reducing: bool             # cons volume non-increasing
    pole_vol_concentration: float       # peak.vol / sum(pole.vol)
    # WICK
    peak_upper_wick: float              # upper-wick frac of the peak bar      [gate input]
    pole_has_big_green: bool
    pole_avg_body: float                # mean body frac across pole bars
    cons_indecision: float              # frac of cons bars small-bodied/doji
    # POLE
    pole_height_pct: float              # (pole_high - pole_base) / pole_base  [gate input]
    pole_height_abs: float
    pole_velocity: float                # pole_height_pct / pole_len
    pole_extension_atr: float | None    # height / trailing ATR (None if no baseline)
    # CONS
    retracement: float                  # (pole_high - cons_low)/(pole_high - pole_base) [gate input]
    holds_base: bool                    # cons_low > pole_base                  [gate input]
    cons_tightness: float               # (max cons high - min cons low)/pole_high
    cons_drift_slope: float             # slope of cons highs (<=0 preferred)
    # LOC (recorded only this pass)
    trigger_in_window: bool             # 04:00-11:59 ET                        [gate input]
    bars_before_scan: int | None        # None until scanner join lands

@dataclass(frozen=True)
class GateResult:                       # gates.py
    name: str
    passed: bool
    value: float | bool                 # the measured feature, for the review page

@dataclass(frozen=True)
class Setup:                            # detect.py — the full result
    segment: Segment
    features: FeatureVector
    entry_trigger: float                # last cons high + entry_offset (1 tick, #182/#190)
    entry_fill: float                   # last cons high + fill_offset (3 ticks, R-measurement only)
    breakout_level: float               # last cons high
    stop: float                         # cons low
    gates: tuple[GateResult, ...]
    passed: bool                        # all gates passed
    score: float                        # 0..1 quality (only meaningful if passed)
    contributions: Mapping[str, float]  # per-feature score contribution (explainability)
```

---

## 4. Stage 1 — tokenizer (`tokens.py`)

```python
def tokenize(bars: Sequence[Bar], *, eps: float) -> list[Token]:
    """One token per bar after the first, comparing high[i] to high[i-1] within eps."""
```

- `eps` = flatness tolerance = `tick_size` (1 tick) by default; passed down from settings.
- `H` if `high[i] > high[i-1] + eps`; `L` if `high[i] < high[i-1] - eps`; else `E`.
- Length invariant: `len(tokenize(bars)) == max(0, len(bars) - 1)`.

## 5. Stage 2 — segmenter (`segment.py`)

```python
def segment_at_end(bars: Sequence[Bar], tokens: Sequence[Token], *,
                   max_pole: int, max_cons: int) -> Segment | None:
    """Longest valid base→POLE→CONSOLIDATION ending at the LAST bar (no trigger H yet).
    Returns None if no valid shape ends here. tokens must be tokenize(bars)."""
```

Rules (from `bull-flag.md §2.2`):
- **Peak = the dominant (highest) high of the trailing `max_cons + 1` bars** (ties → earliest), not
  the nearest local up-tick. This *is* the engine's #163 fix, and it's why the segmenter takes
  `bars` and not only `tokens`: tokens drop magnitudes, so a mid-pullback up-tick would otherwise be
  mistaken for the peak. (Original spec passed tokens only; refined during #177 — a token-only
  segmenter can't resolve the dominant high.) If the peak lands on the last bar → still extending →
  `None`.
- **Consolidation** = the bars after the peak. Its tokens must contain **no `H`** (a higher-high
  step means it ticked back up — not a clean pullback, matching legacy `_flag_makes_lower_highs`)
  and **≥1 strict `L`** (an all-`E` flat top has no net lower high).
- **Pole** = the run of **strict `H`** ending at the peak, capped at `max_pole`. `E` is **not**
  allowed in the pole (equal highs are consolidation-only), so the walk stops at the first non-`H`
  going back; `pole_len` counts the higher highs and must be ≥1. Every pole step strictly rises, so
  the base is strictly below the peak (`pole_span > 0`) — this fixes the #181 zero-span crash where
  the old `H`/`E` walk drifted the base across a flat run onto a bar at/above the peak.
- **Color/thrust rule (#182/#190, via per-opportunity visual review)**: the peak bar must be
  **green** (`close > open`, any body size); a red peak (a new high that reverses and closes weak
  within the bar — a shooting-star top) disqualifies this candidate entirely, and the caller keeps
  scanning later prefixes for a green peak (IRE). To extend the pole *past* the peak's immediate
  predecessor, each additional bar must be a genuine **thrust** — green with body ≥ half its range
  (reuses `detect._is_big_green`) — a doji-like or red bar breaks the walk and becomes the base
  instead of an intermediate pole bar (MUZ/CRCG/CONL). This has no legacy equivalent, so parity is
  additionally scoped to poles built entirely of green thrust candles (§11).
- Length gates: `pole_len ∈ [1, max_pole]`, `cons_len ∈ [1, max_cons]`, both `= 4` in v2 (an
  over-long shape simply doesn't segment).

**Why end-anchored?** Detection must fire on a **completed consolidation** (last bar) so we can set
the entry level *before* the breakout. The grammar's "trigger = first `H` after the consolidation"
(`bull-flag.md §2.2`) is exactly the forward event `rmetrics` already looks for: the first later bar
whose high reaches `entry_trigger` is, by definition, that first higher high. The two definitions
are consistent — the detector emits the *level*, the fill is the *first H*.

`scan(bars)` (whole-day, for analysis) = call `segment_at_end` on each prefix, yielding every
completed setup earliest-first — a direct replacement for `rmetrics._iter_setups`'s O(n²) prefix
loop (can be optimised to a single backward pass later; keep prefix form for a faithful port first).

## 6. Stage 3 — features (`features.py`)

```python
def extract(bars: Sequence[Bar], seg: Segment, *, atr: float | None = None,
            window_start: time = time(4, 0), window_end: time = time(11, 59)) -> FeatureVector: ...
def trailing_atr(bars: Sequence[Bar], base_idx: int, *, window: int = 14) -> float | None: ...
```

- Pure over `bars[seg.base_idx : seg.cons_end_idx+1]` (plus the bars before the base for the ATR
  baseline). Reuses the legacy `_upper_wick_frac` / `_is_big_green` / `_non_increasing`.
- `pole_base = bars[base_idx].low`, `pole_high = bars[peak_idx].high`,
  `cons_low = min(low over consolidation)` — same anchors the current engine uses, so retracement is
  numerically identical to today for a shape both detectors accept.
- `pole_extension_atr` takes an optional trailing `atr` (compute it with `trailing_atr`, a 14-bar
  Wilder true-range mean over the bars *before* the base); `None` when there aren't enough bars.
- `trigger_in_window` uses the detection bar's time (`cons_end_idx`) converted to ET via
  `clock.within_window`; `window_start`/`window_end` default to the strategy window
  (`Settings.scan_start`/`scan_end`) and are overridable so `extract` needs no `Settings`.
- LOC is recorded-only this pass: `bars_before_scan = None` until the `scanner_hits` join lands.

## 7. Stage 4 — gate + score

**`gates.py`** — one predicate per `[gate input]` feature; returns an ordered `GateResult[]` so the
review page can show *which* gate rejected a shape and by how much:

| Gate | Feature | Condition (v2 default) |
|------|---------|------------------------|
| `shape_valid` | segmentation | `Segment is not None` |
| `pole_len` | `pole_len` | `≤ 4` (enforced in segmenter) |
| `cons_len` | `cons_len` | `≤ 4` (enforced in segmenter) |
| `vol_peak_gt_cons` | `peak_gt_cons` | strict `>` |
| `wick_peak` | `peak_upper_wick` | `≤ max_peak_wick` (0.50) |
| `pole_height` | `pole_height_pct` | `≥ min_pole_pct` (**2%**) |
| `cons_retracement` | `retracement` | `≤ 0.50` |
| `cons_holds_base` | `holds_base` | `cons_low > pole_base` |
| `loc_in_window` | `trigger_in_window` | 04:00–11:59 ET |

`min_pole_pct` = **2%** (`bull-flag.md §3.4`) — a loose meaningful-move floor; the "abnormal" signal
lives in the `pole_extension_atr` score (trailing 14-bar true-range ATR), not this gate. This *is* a
new reject vs. today's engine, so the divergence report (§10) must quantify how many historical
shapes it removes; if it's surprising we revisit the floor before flipping settings.

**`score.py`** — normalise each `score` feature to 0–1, weighted sum → `score`, and return the
`contributions` map:

```python
def score(fv: FeatureVector, *, weights: Mapping[str, float]) -> tuple[float, dict[str, float]]: ...
```

Weights hand-set in `Settings` (a small frozen mapping) with a documented rationale; the point of
returning `contributions` is that ranking is auditable on the review page now and fittable later.

## 8. Public API & backward compatibility (`compat.py`, `__init__.py`)

Keep the existing consumers working unchanged on day one:

```python
def detect(bars, *, min_pole=1, max_pole=4, max_cons=4, max_retracement=0.50,
           max_peak_wick=0.50, min_pole_pct=0.02, atr_window=14,
           entry_offset=0.03, eps=0.01) -> Setup | None: ...

def detect_with_settings(bars, settings) -> Setup | None: ...   # same name rmetrics imports
```

- **Shim:** `Setup` exposes the legacy `BullFlag` fields that `rmetrics.RMetrics` /
  review read (`entry_trigger`, `stop`, `breakout_level`, `flag_len=cons_len`, `retracement`,
  `pole_len`, `cons_vol_reducing`, `pole_has_big_green`). Provide `Setup.as_bullflag() -> BullFlag`
  **and** re-export `BullFlag` so `rmetrics`'s `from .bullflag import BullFlag, detect_with_settings`
  still type-checks.

  **Sequencing (refined during #179, extended #182/#190):** the v2 entry point is a *new*
  `detect_setup` / `detect_setup_with_settings`; #179 does **not** repoint `detect_with_settings` or
  touch `rmetrics` — the legacy path stays active, so reported metrics move by **zero** in #179 or
  #182/#190. The atomic switch (repoint `detect_with_settings → detect_setup`, flip settings 8/6→
  4/4 + `min_pole_pct` 2%, **and** point `rmetrics`'s R-measurement at `Setup.entry_fill` instead of
  `entry_trigger`) lands in **#180**, with the #181 divergence spike quantifying it. This is safer
  than switching earlier, because v2 has real behavioural deltas from legacy even at equal params
  (peak-bar volume gate now requires color/thrust too; `E`-tolerant consolidation; an optional
  window gate; a genuinely new trigger-vs-fill split with no legacy equivalent) — bundling the
  switch with the flip keeps the change atomic and auditable.
  1. **Build + pin (#179):** `detect_setup(...).as_bullflag()` == legacy `detect(...)` field-for-field
     for strict, in-window shapes under legacy-equivalent params (the golden-parity test).
  1b. **Refine (#182/#190):** pole color/thrust rule, 1-tick trigger, 3-tick conservative fill —
      all validated via per-opportunity visual review; still behind the legacy path.
  2. **Switch (#180):** repoint + flip settings + wire `rmetrics` to `entry_fill` for R.
  3. **Enrich (#182):** widen `RMetrics` to carry `score` so the review page shows the ranking and
     gate-rejection reasons.

## 9. Settings changes (`config.py`)

| Setting | Old | v2 | Note |
|---------|-----|----|------|
| `bull_flag_max_pole` | 8 | **4** | locked |
| `bull_flag_max_flag` → `bull_flag_max_cons` | 6 | **4** | locked (rename for grammar parity; keep old name as alias one release) |
| `bull_flag_trigger_offset_ticks` | — | **1** | **added in #182/#190** (v2-only; supersedes the earlier `entry_offset_ticks=3` lock — that setting is legacy-only and unused by v2) |
| `bull_flag_fill_offset_ticks` | — | **3** | **added in #182/#190**: conservative slippage-modeled FILL price for R (confirmed by the trader — the "+3 ticks" idea survives, but downstream of the trigger, not as the trigger). `Setup.entry_fill`, no legacy slot; #180 must wire `rmetrics` to read it |
| `bull_flag_min_pole_pct` | — | **0.02** | new gate (2% pole height) |
| `bull_flag_atr_window` | — | **14** | trailing bars for `pole_extension_atr` |
| `bull_flag_eps_ticks` | — | **1** | `E`-token flatness tolerance |
| `bull_flag_score_weights` | — | frozen mapping | hand-set, documented |

`bull_flag_max_retracement` (0.50) and `bull_flag_max_peak_wick` (0.50) unchanged.

## 10. Migration & retroactive recompute

- **No data migration.** Detection is computed-on-read; changing the engine changes *derived*
  values only. Re-running the review/analysis replay over stored bars reprices every historical
  opportunity under v2.
- **Divergence report (do before merge):** a spike that runs both engines over all stored days and
  diffs setups/entries/stops/Max R. Expected diffs: shapes with pole >4 or cons >4 now rejected;
  entries 2 ticks tighter; `E`-tolerant shapes newly accepted. Record the diff as an issue comment
  (spikes/ + `data/spikes/`, gitignored) so the behavioural change is auditable — this is a
  strategy redefinition, so it warrants a `decisions.md` entry like #127 did.

## 11. Testing plan (trading logic = the product; exhaustive per CLAUDE.md)

- `tokens`: H/L/E boundaries at exactly `eps`; zero/one-bar inputs; length invariant.
- `segment`: longest-match beats a shorter nested match; `E` splits the pole but is fine in the
  consolidation; flat-noise never yields a zero pole span (#181); all-`E` run rejected;
  pole/cons length caps at 4; mid-pullback up-tick doesn't truncate the pole (#163 regression, moved
  to the segmenter); "still extending" (last bar is `H`) → no segment; a red peak disqualifies the
  candidate entirely (#182/#190: IRE); a doji/weak-bodied bar breaks pole extension and becomes the
  base (#182/#190: MUZ/CRCG/CONL); a weak-bodied (but still green) peak is still a valid 1-bar pole.
- `features`: each of the six areas on hand-built bar fixtures with known geometry; retracement /
  peak-wick / big-green parity with the current engine's helpers on shared fixtures.
- `gates`: each gate's boundary; `min_pole_pct=0` admits everything today's engine admits.
- `score`: monotonicity (shallower retrace / shorter pole / higher vol ratio never lowers score);
  contributions sum to `score`.
- **Golden parity:** for a corpus of fixtures both engines accept, `as_bullflag()` ==
  today's `detect()` output field-for-field, and `rmetrics` numbers are unchanged. **Scope: strict
  (non-`E`) poles, built entirely of green thrust candles, whose steps clear `eps`** — three
  intended v2 divergences from legacy, not parity violations: (1) an equal-high step re-anchors
  differently than legacy's raw `>` comparison; (2) a red or doji-like pole bar (#182/#190) has no
  legacy equivalent — legacy's strict-ascending walk doesn't check color/body at all; (3) a step
  within `eps` (1 tick) is `E` in v2 but still a legacy `H`/`L`. Fixtures use clearly separated
  highs and green, thrust-bodied pole bars so none of the three trip.
- Reuse the named real cases already in `test_bullflag.py` (AHMA/VRXA/SNDQ/ETHT/NBIZ/CLRO/CYH/DJT).

## 12. Rollout (proposed issues, Refs #1)

1. `feat: bullflag package skeleton + tokenizer/segmenter (stages 1–2) with tests`
2. `feat: feature extraction (stage 3, six areas) with tests`
3. `feat: gates + score + Setup, compat shim (built alongside; golden-parity pinned; legacy path
   stays active — zero metric change)`
4. `chore: settings (4/4, 3-tick, min_pole_pct, eps, weights) + rename max_flag→max_cons alias
   **AND** repoint detect_with_settings → detect_setup (the atomic cut-over)`
5. `spike: v2-vs-v1 divergence report over stored history` → `docs: decisions.md entry`
6. `feat: surface v2 score + gate-rejection reasons on the review page`

Land 1–3 with **zero** behavioural change (legacy detector still drives reported metrics; #179 only
adds the v2 pipeline + parity test). #4 is the atomic cut-over (repoint + settings flip); #5
quantifies it; #6 exposes it.
```
