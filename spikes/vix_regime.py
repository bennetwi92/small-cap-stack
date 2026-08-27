"""VIX regime-signal spike — issue #723.

Tests whether the CBOE Volatility Index (^VIX) carries a usable day-level regime signal for this
project's small-cap opportunity edge. Distinct from #690 (day-level regime built from the small-cap
universe's own tape — found dead at the day level); VIX is an *external* input never tested there.

Reuses `spikes/engine_lab/common.py` as the measuring apparatus (population, splits, `score()`,
`build_book()`, no-lookahead rules) rather than forking it. This module only adds the VIX pull and
the day-level join; all scoring, costs and capacity logic come from `common.py`.

## Population caveat

`common.py`'s own docstring documents "3,639 rows over 197 sessions" (recon 2025-10-30..2026-06-30,
live 2026-07-01..2026-08-13). As of this run, the underlying `data/spikes/regime_panel.parquet` has
been regenerated with a much wider date range for other spikes' purposes (recon back to
2024-12-30, live forward to 2026-08-25 — 9,772 rows total). `load_bounded_panel()` below re-applies
the documented date bounds so this spike's population and splits match what `common.py` describes
and what every other engine_lab result was measured against. Verified: bounding reproduces exactly
3,639 rows / 197 sessions (dev 125 / val 41 / holdout 31 sessions — a couple of session-count off
the docstring's approximate "~120 / ~40 / 31" language, which is expected slop in an "~" figure).

## VIX features (both strictly no-lookahead)

- `vix_abs`  — the **prior trading day's VIX close** (the most conservative, definitely-known value
  before this project's 04:00-09:30 ET pre-market trigger window). VIX has traded near-24h since
  2022, so same-day VIX open may also be knowable pre-market; that variant is pulled and reported
  separately, clearly labelled, but the prior-close convention is primary.
- `vix_rel`  — prior-day VIX close / trailing 45-**trading**-day average of VIX close, using only
  the 45 days strictly before today (never including today, never including a partial window at the
  start of the pull — hence pulling VIX from well before DEV's start).

## Usage

    python spikes/vix_regime.py pull      # fetch ^VIX via yfinance, cache to data/spikes/
    python spikes/vix_regime.py sweep     # coverage + distribution + DEV+VAL sweep + HOLDOUT look
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spikes.engine_lab.common import SHIPPED, build_book, fixed_target_r, load_panel, score

REPO = Path(__file__).resolve().parents[1]
VIX_PATH = REPO / "data/spikes/vix_daily.parquet"

#: documented common.py population bounds (see module docstring caveat above)
RECON_START, RECON_END = dt.date(2025, 10, 30), dt.date(2026, 6, 30)
LIVE_START, LIVE_END = dt.date(2026, 7, 1), dt.date(2026, 8, 13)

#: pull lead time so a real 45-trading-day trailing average exists from day one of DEV
VIX_PULL_START = "2025-06-01"
VIX_PULL_END = "2026-08-20"

TRAIL_N = 45


# ---------------------------------------------------------------------------------------------
def pull_vix(path: Path = VIX_PATH) -> pl.DataFrame:
    """Fetch ^VIX daily OHLC via yfinance and cache it. Re-run to refresh."""
    import yfinance as yf

    raw = yf.download("^VIX", start=VIX_PULL_START, end=VIX_PULL_END, progress=False)
    if raw.columns.nlevels > 1:
        raw.columns = raw.columns.get_level_values(0)
    df = pl.from_pandas(raw.reset_index()).select(
        pl.col("Date").cast(pl.Date).alias("vix_dt"),
        pl.col("Open").alias("vix_open"),
        pl.col("Close").alias("vix_close"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return df


def load_vix(path: Path = VIX_PATH) -> pl.DataFrame:
    if not path.exists():
        return pull_vix(path)
    return pl.read_parquet(path)


def build_vix_features(vix: pl.DataFrame) -> pl.DataFrame:
    """Day-level VIX features, all computed from data strictly before `vix_dt`'s own session.

    - `vix_prior_close` = previous VIX trading day's close (row shifted by 1)
    - `vix_trail45`     = mean of the 45 VIX closes strictly before `vix_dt` (i.e. ending at the
                          prior day, a 45-day window that itself excludes today)
    - `vix_rel`         = vix_prior_close / vix_trail45
    - `vix_open_same_day` = same-day VIX open — timing-assumption variant, reported separately
    """
    v = vix.sort("vix_dt")
    v = v.with_columns(
        pl.col("vix_close").shift(1).alias("vix_prior_close"),
        pl.col("vix_close").shift(1).rolling_mean(window_size=TRAIL_N).alias("vix_trail45"),
        pl.col("vix_open").alias("vix_open_same_day"),
    )
    v = v.with_columns((pl.col("vix_prior_close") / pl.col("vix_trail45")).alias("vix_rel"))
    return v.select(
        "vix_dt", "vix_prior_close", "vix_trail45", "vix_rel", "vix_open_same_day", "vix_close"
    )


# ---------------------------------------------------------------------------------------------
def load_bounded_panel() -> pl.DataFrame:
    """`load_panel()`, re-bounded to the date range `common.py`'s docstring describes.

    See the population caveat in this module's docstring — the live regime_panel.parquet now
    carries a wider range than 3,639/197; this reproduces that documented population exactly.
    """
    df = load_panel()
    return df.filter(
        ((pl.col("source") == "recon") & pl.col("dt").is_between(RECON_START, RECON_END))
        | ((pl.col("source") == "live") & pl.col("dt").is_between(LIVE_START, LIVE_END))
    )


def joined_panel() -> pl.DataFrame:
    panel = load_bounded_panel()
    vixf = build_vix_features(load_vix())
    return panel.join(vixf, left_on="dt", right_on="vix_dt", how="left")


# ---------------------------------------------------------------------------------------------
def coverage_report(dfj: pl.DataFrame) -> dict:
    sessions = dfj.select("dt").unique()
    missing = dfj.filter(pl.col("vix_prior_close").is_null()).select("dt").unique().sort("dt")
    return {
        "sessions_total": sessions.height,
        "sessions_missing_vix": missing.height,
        "missing_dates": missing["dt"].to_list(),
    }


def distribution_report(dfj: pl.DataFrame) -> dict:
    sess = dfj.select("dt", "vix_prior_close", "vix_rel", "vix_open_same_day").unique(subset=["dt"])
    out = {}
    for col in ("vix_prior_close", "vix_rel", "vix_open_same_day"):
        s = sess[col].drop_nulls()
        out[col] = {
            "n": s.len(),
            "min": round(float(s.min()), 4),
            "p25": round(float(s.quantile(0.25)), 4),
            "median": round(float(s.quantile(0.5)), 4),
            "p75": round(float(s.quantile(0.75)), 4),
            "max": round(float(s.max()), 4),
            "mean": round(float(s.mean()), 4),
        }
    return out


def book_score(
    dfj: pl.DataFrame, *, splits: tuple[str, ...] = ("dev", "val"), shipped_only: bool = False
) -> dict:
    sub = dfj.filter(pl.col("split").is_in(splits))
    if shipped_only:
        sub = SHIPPED(sub)
    book = build_book(fixed_target_r(sub), max_per_day=2)
    return score(book, sessions=dfj.filter(pl.col("split").is_in(splits))["dt"].n_unique())


def session_counts(
    dfj: pl.DataFrame, mask: pl.Expr, *, splits: tuple[str, ...] = ("dev", "val")
) -> dict:
    sub = dfj.filter(pl.col("split").is_in(splits))
    all_sessions = sub.select("dt").unique().height
    kept = sub.filter(mask).select("dt").unique().height
    return {
        "sessions_all": all_sessions,
        "sessions_kept": kept,
        "sessions_dropped": all_sessions - kept,
    }


def sweep(dfj: pl.DataFrame) -> None:
    print("=== coverage ===")
    cov = coverage_report(dfj)
    print(cov)

    print("\n=== distribution (197 sessions, one row/session) ===")
    for k, v in distribution_report(dfj).items():
        print(f"  {k}: {v}")

    print("\n=== unrestricted DEV+VAL baseline (all triggered rows, 2/day book) ===")
    base = book_score(dfj)
    print(
        f"  {base['trades']} trades, {base['sessions_available']} sessions, "
        f"gross {base['gross_r']:+.2f}R net {base['net_r']:+.2f}R "
        f"({base['net_r_per_trade']:+.4f}/trade)"
    )

    print("\n=== vix_abs sweep (prior-day VIX close < threshold), DEV+VAL ===")
    for th in (14.0, 15.0, 16.0, 17.0, 18.0):
        mask = pl.col("vix_prior_close") < th
        sc = book_score(dfj.filter(mask))
        sess = session_counts(dfj, mask)
        rps = sc["net_r"] / sess["sessions_kept"] if sess["sessions_kept"] else 0.0
        print(
            f"  th<{th:>5.1f}  sessions {sess['sessions_kept']:>3}/{sess['sessions_all']} kept "
            f"(dropped {sess['sessions_dropped']:>3})  trades {sc['trades']:>3}  "
            f"gross {sc['gross_r']:+7.2f}R  net {sc['net_r']:+7.2f}R  "
            f"net/trade {sc['net_r_per_trade']:+.4f}  net/session {rps:+.4f}"
        )

    print("\n=== vix_rel sweep (prior-close / trailing-45d avg < threshold), DEV+VAL ===")
    for th in (0.80, 0.85, 0.90, 0.95, 1.00, 1.05):
        mask = pl.col("vix_rel") < th
        sc = book_score(dfj.filter(mask))
        sess = session_counts(dfj, mask)
        rps = sc["net_r"] / sess["sessions_kept"] if sess["sessions_kept"] else 0.0
        print(
            f"  th<{th:>5.2f}  sessions {sess['sessions_kept']:>3}/{sess['sessions_all']} kept "
            f"(dropped {sess['sessions_dropped']:>3})  trades {sc['trades']:>3}  "
            f"gross {sc['gross_r']:+7.2f}R  net {sc['net_r']:+7.2f}R  "
            f"net/trade {sc['net_r_per_trade']:+.4f}  net/session {rps:+.4f}"
        )

    print(
        "\n=== SHIPPED book (config.py rules) x vix_abs, DEV+VAL — the number this project "
        "actually ships is the baseline, not the raw panel ==="
    )
    shipped_base = book_score(dfj, shipped_only=True)
    print(
        f"  unrestricted SHIPPED: {shipped_base['trades']} trades, "
        f"gross {shipped_base['gross_r']:+.2f}R net {shipped_base['net_r']:+.2f}R "
        f"({shipped_base['net_r_per_trade']:+.4f}/trade)"
    )
    for th in (15.0, 16.0, 17.0, 18.0):
        mask = pl.col("vix_prior_close") < th
        sc = book_score(dfj.filter(mask), shipped_only=True)
        sess = session_counts(dfj, mask)
        print(
            f"  th<{th:>5.1f}  sessions {sess['sessions_kept']:>3}/{sess['sessions_all']} kept  "
            f"trades {sc['trades']:>3}  gross {sc['gross_r']:+7.2f}R  net {sc['net_r']:+7.2f}R  "
            f"net/trade {sc['net_r_per_trade']:+.4f}"
        )
    for th in (0.85, 0.90, 0.95, 1.00):
        mask = pl.col("vix_rel") < th
        sc = book_score(dfj.filter(mask), shipped_only=True)
        sess = session_counts(dfj, mask)
        print(
            f"  vix_rel th<{th:>5.2f}  sessions {sess['sessions_kept']:>3}"
            f"/{sess['sessions_all']} kept  trades {sc['trades']:>3}  "
            f"gross {sc['gross_r']:+7.2f}R  net {sc['net_r']:+7.2f}R  "
            f"net/trade {sc['net_r_per_trade']:+.4f}"
        )

    print(
        "\n=== secondary: same-day VIX open < threshold, DEV+VAL "
        "(timing-assumption variant, informational only) ==="
    )
    for th in (14.0, 16.0, 18.0):
        mask = pl.col("vix_open_same_day") < th
        sc = book_score(dfj.filter(mask))
        sess = session_counts(dfj, mask)
        print(
            f"  th<{th:>5.1f}  sessions {sess['sessions_kept']:>3}/{sess['sessions_all']} kept  "
            f"trades {sc['trades']:>3}  gross {sc['gross_r']:+7.2f}R  net {sc['net_r']:+7.2f}R"
        )


def holdout_look(dfj: pl.DataFrame, *, abs_threshold: float, rel_threshold: float) -> None:
    """The ONE holdout look. Call only after thresholds are fixed from DEV+VAL."""
    print(f"\n=== HOLDOUT (one look) — vix_abs<{abs_threshold}, vix_rel<{rel_threshold} ===")
    for shipped_only in (False, True):
        label = "SHIPPED" if shipped_only else "raw panel"
        base = book_score(dfj, splits=("holdout",), shipped_only=shipped_only)
        print(
            f"  [{label}] unrestricted: {base['trades']} trades, {base['sessions_available']} "
            f"sessions, gross {base['gross_r']:+.2f}R net {base['net_r']:+.2f}R "
            f"({base['net_r_per_trade']:+.4f}/trade)"
        )
        for name, mask in (
            ("vix_abs", pl.col("vix_prior_close") < abs_threshold),
            ("vix_rel", pl.col("vix_rel") < rel_threshold),
        ):
            sc = book_score(dfj.filter(mask), splits=("holdout",), shipped_only=shipped_only)
            sess = session_counts(dfj, mask, splits=("holdout",))
            print(
                f"  [{label}] {name}: sessions {sess['sessions_kept']}/{sess['sessions_all']} "
                f"kept  trades {sc['trades']}  gross {sc['gross_r']:+.2f}R  "
                f"net {sc['net_r']:+.2f}R  net/trade {sc['net_r_per_trade']:+.4f}"
            )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    if cmd == "pull":
        v = pull_vix()
        print(f"pulled {v.height} VIX rows -> {VIX_PATH}")
    elif cmd == "sweep":
        dfj = joined_panel()
        sweep(dfj)
        # Thresholds fixed from the DEV+VAL sweep printed above: on the SHIPPED book (the
        # baseline that matters), vix_abs<18 (+0.0403 net/trade) and vix_rel<1.00 (+0.0091
        # net/trade) were the least-bad of the thresholds tried -- neither beat the unrestricted
        # SHIPPED baseline (+0.0762 net/trade), but they are what "most promising on DEV+VAL"
        # actually points to; every tighter cut (abs<15/16/17, rel<0.85/0.90/0.95) did worse.
        holdout_look(dfj, abs_threshold=18.0, rel_threshold=1.00)
    else:
        raise SystemExit(f"unknown command {cmd!r}")
