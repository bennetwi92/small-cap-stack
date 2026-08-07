"""Application configuration, loaded from environment / .env (see .env.example)."""

from __future__ import annotations

from datetime import date, time
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Field names map case-insensitively to env vars (e.g. IBKR_HOST)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # IBKR connection (used by the connection supervisor — issue #11)
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002  # Gateway paper 4002 / live 4001; TWS 7497 / 7496
    ibkr_client_id: int = 1
    # After an *unclean* disconnect the Gateway can hold the old client id for tens of seconds, so a
    # reconnect on the same id fails with error 326. Rotate across a small pool of ids on successive
    # connect attempts so a reconnect sidesteps a still-held id (Phase-1 places no orders, so the id
    # need not be stable). Steady state uses ibkr_client_id; only a stuck id bumps up (#163-C2).
    ibkr_client_id_pool: int = 4
    ibkr_connect_timeout_sec: float = 15.0  # bound the connectAsync handshake
    ibkr_trading_mode: str = "paper"  # paper | live

    # Storage (DuckDB + Parquet — issue #7). DuckDB is opened in-memory over the Parquet globs.
    data_dir: Path = Path("./data")
    # Reconstructed history (#430) — a SECOND store, same dataset layout, holding pre-market days
    # rebuilt from purchased vendor minute bars rather than captured live. It is a separate root on
    # purpose: the reconstructed dates sit *before* live collection started, so the two are
    # date-disjoint, and keeping them in separate trees means no existing reader (the EOD report,
    # charts, the canary, `collected_dates`) silently starts returning vendor rows. Only the code
    # that explicitly opts in — `build_portfolio_payload(recon_store=…)` — ever sees them.
    # Empty string disables the feature; a missing directory reads as empty, so an unharvested box
    # behaves exactly as it did before.
    recon_subdir: str = "recon"
    # Ceiling on how many reconstructed CANDIDATE-TRADES splice into `books_all` (#448), walked
    # newest-first. This bounds the #273 failure mode where it can still be bounded:
    # `build_portfolio_payload` retains every day's bars because it re-simulates the same day list
    # once per selectable target, so peak memory is linear in days x candidates — which is what
    # OOM-killed the box at ~25 live days (#264). A finished harvest makes it ~500 days, and recon
    # days may run denser than live ones — though NOT for the rank-cap reason once assumed (#460:
    # the cap has never bound; pre-market peaks at 11 of 50).
    # Budgeted on candidates, not days: days are a proxy, candidates are the cost, and the density
    # of a reconstructed day is the one number nobody has measured yet. 15k x ~27 KB ~= 400 MB
    # retained, against the app container's 2 GB cap. 0 disables the cap.
    portfolio_recon_max_candidates: int = 15_000
    # How many reconstructed sessions have a PUBLISHED chart payload at once (#488). Bounded by the
    # *publish pipe*, not by memory, unlike `portfolio_recon_max_candidates` above:
    # `publish-dashboard` force-pushes a fresh single commit of the whole `data/dashboard` tree
    # every 15 minutes, and a date's payload is 1.5-3 MB of full-day bars; a finished harvest is
    # ~500 of them, which would put ~1 GB through that push every quarter of an hour and the same
    # again down every browser that opens Results with `+ History` on.
    # ⚠️ This caps the date COUNT, not the bytes — `harvest_max_candidates` is 0 (uncapped), so one
    # unusually busy reconstructed session can be several times the 1.5-3 MB a live day costs, and
    # the ~500x figure above is arithmetic rather than measurement. Check `du -sh /data/dashboard`
    # and the publish job's duration once the first window has landed (RUNBOOK §13).
    # ⚠️ Eviction is by PUBLISH ORDER, not by date — see `dashboard_recon._keep_window` before
    # changing this. A newest-date window looks reasonable and makes all but the first ~2 nights of
    # the harvest permanently invisible, because the harvest walks backwards. 0 disables the cap.
    recon_charts_max_dates: int = 30

    # --- Overnight pre-market harvest (#431) — the producer that fills the recon store above. ---
    # Ingest is #430's decision: the vendor's FREE tier, REST, no purchase. That makes a 5 calls/min
    # rate limit — not lookback, not bytes — the thing that prices the whole job, so every knob here
    # is really a knob on "how many trading days does a night buy".
    harvest_lookback_days: int = 730  # ~2 years: the free tier's measured lookback (#428)
    # Sleep between vendor calls. 13s ≈ 4.6/min, inside the free tier's 5/min with headroom. Raise
    # it to be politer; lowering it below ~12 earns 429s, and a blocked key has no second copy.
    harvest_rate_sleep_sec: float = 13.0
    # The harvest-only day-volume floor on the candidate prefilter (see harvest/prefilter.py). It is
    # a loose proxy for the real trailing-5-min gate, which a daily bar cannot evaluate — airtight
    # (a name clearing a 100k 5-min sum must trade >=100k on the day) but ~12x looser than the
    # loosest of the 25 committed review cases. It sets the ~217 candidates/day that prices the
    # harvest at ~45 nights. UNCHANGED on this issue: what a tighter floor *cuts* is unmeasured, and
    # `python -m small_cap_stack.harvest sweep` is the measurement to run before touching it.
    harvest_min_day_volume: float = 100_000.0
    harvest_max_candidates: int = 0  # per session; 0 = no cap (a smoke-test lever, not a filter)
    # Failure accounting (#446). A day the vendor refused and a day nothing traded both write zero
    # rows, so without these a truncated API key marked ~11 dates a night as harvested, forever.
    # The breaker is about SPEED — a failing symbol costs 5 calls and ~95s on the retry ladder, so
    # discovering an outage 218 times costs the night. The ratio is about DATA: scattered failures
    # never trip the breaker but still leave a session sampled from a fraction of its universe.
    harvest_max_consecutive_failures: int = 5
    harvest_max_failure_ratio: float = 0.2
    # Vendor ticker types dropped from the harvested universe (#443) — the reconstruction's
    # equivalent of the live scan's `scan_exclude_stock_types` ("ETF", "ETN") applied via IBKR's
    # `stkTypes exc:` filter. Kept as a SEPARATE setting rather than reusing that one because these
    # are two vendors' taxonomies that merely happen to share two code names: Polygon splits
    # exchange-traded products finer than IBKR does, so "ETV" (exchange-traded vehicle) has to be
    # named here to catch what IBKR files under ETF. Closed-end funds ("FUND") are deliberately
    # absent — they trade like stocks and IBKR's STK.US.MAJOR scan includes them.
    harvest_exclude_ticker_types: tuple[str, ...] = ("ETF", "ETN", "ETV")
    # How long the cached exclusion set may go unrefreshed. It is reference data and the harvest
    # walks backwards in time, so newly-listed products matter less the longer it runs; 0 disables
    # refreshing entirely.
    harvest_exclusions_max_age_days: int = 30
    # Keep the raw 1-min pre-market bars the appearance was reconstructed from (~330 rows per
    # symbol-day, ~36M rows over the harvest). Store-raw/compute-on-read has a price tag here:
    # without them a change to the reconstruction rules costs another 45 nights of API budget
    # rather than a re-read.
    # Turn off only if disk gets tight — the 5-min `bars` the engine reads are written either way.
    harvest_store_minute_bars: bool = True
    # The window the job may run in, ET. The box's day is booked: eod_backfill 03:45, scan
    # 04:00-11:59, eod_bars_fetch 16:20, eod_report 16:30. The hard stop at 03:00 leaves 45 minutes
    # of clearance. The job REFUSES to start outside this — being launched at the right time and
    # refusing the wrong one are different guarantees, and only the second survives a late timer.
    #
    # Widened from 17:00 to 12:30 (#455): 12:00-16:20 ET is the one block of the box's day nothing
    # is scheduled in, and at the free tier's 5 calls/min the harvest's calendar is set purely by
    # hours-per-day. 12:30 leaves half an hour after the 11:59 scan close. The window therefore
    # spans the two EOD jobs, and `harvest_eod_recess_et` below — not the host guard — is what
    # keeps the harvest out of them.
    harvest_start_et: time = time(12, 30)
    harvest_stop_et: time = time(3, 0)
    # The start on a day the market is SHUT — weekend or holiday, per `market_calendar` (#633).
    # Everything the 12:30 start ducks is trading-day-only: the scan window self-gates on the same
    # calendar, and `eod_bars_fetch` / `eod_report` both return immediately on a closed day (so
    # `harvest_eod_recess_et` buys nothing then either, and `effective_deadline` skips it).
    # The 03:00 STOP is unchanged and deliberately not a weekend variant: `portfolio_refresh` 03:15
    # and `eod_backfill` 03:45 run every day of the week, closed or not.
    # 05:00 rather than 04:00 for the same asymmetry the stop is built on — a run must be *finished*
    # before a heavy job, and started only once one is done. `eod_backfill` at 03:45 walks
    # `backfill_days` of trading days over IBKR and rebuilds a report per repaired day, and its
    # duration grows with the store. Starting on top of it would risk `HostGuard` tripping at the
    # first session boundary, which ends the WHOLE run — an expensive way to buy one hour.
    harvest_weekend_start_et: time = time(5, 0)
    # The afternoon run's OWN stop, 10 minutes before `eod_bars_fetch` (#455). This is what makes
    # the widened window safe, and it replaces an argument that did not survive review: the claim
    # was that `HostGuard` would stop the harvest if the EOD jobs made the box tight. It cannot.
    # The guard runs once per SESSION, and a session is ~217 candidates x 13 s = 47 minutes — so
    # with a 12:30 start the boundaries land at 15:38 and 16:25, and the harvest sits inside a
    # session, holding 1 GB with MemorySwapMax=0, straight through `build_portfolio_payload` at
    # 16:30 — the ~1.5 GB, still-growing (#273) job that OOM-killed this box in #264.
    # A deadline is enforced BETWEEN symbols and by the "don't start what you cannot finish"
    # pre-check, so unlike the guard it genuinely bounds where the container can still be running.
    harvest_eod_recess_et: time = time(16, 10)
    # Host floors checked between sessions. The in-process half of the memory story; the enforced
    # half is MemoryMax=1G on the systemd scope (deploy/scs-harvest.service). #264 is why both
    # exist: a promise is not a limit, and a limit alone kills instead of checkpointing.
    harvest_min_mem_available_mb: float = 800.0
    harvest_min_disk_free_mb: float = 2000.0

    # Monitoring (issue #5)
    healthchecks_ping_url: str = ""
    metrics_enabled: bool = True
    metrics_port: int = 9090
    # Host-headroom floors behind status.json's mem_ok/disk_ok booleans (#340). The box compares
    # locally and publishes only the verdict — raw headroom numbers never reach the public
    # dashboard payload (#344 telemetry scrub).
    health_min_mem_available_mb: float = 400.0
    health_max_disk_used_pct: float = 90.0

    # Data-quality canary (#346) — positive-confirmation assertions over today's captures,
    # written to data_dir/dashboard/canary.json on a throttle, for the dashboard and manual review.
    canary_interval_min: float = 5.0
    canary_min_float_coverage: float = 0.8  # share of today's opps with a non-null float row
    canary_news_max_age_h: float = 24.0  # newest news ts_utc must be at most this old
    canary_min_bars: int = 24  # post-EOD per-symbol 5-min bar floor (2h of session)

    # Dashboard exporter (issue #68) — writes status.json/stats.json under data_dir/dashboard.
    dashboard_enabled: bool = True
    deployed_commit: str = ""  # optional; set via env DEPLOYED_COMMIT (baked at build, #72)

    # Logging
    log_level: str = "INFO"
    json_logs: bool = False

    # Schedule (US/Eastern; the market tz lives in clock.ET). Window 04:00–11:59 ET.
    scan_start: time = time(4, 0)
    scan_end: time = time(11, 59)
    eod_bars_fetch: time = time(16, 20)  # batch-fetch the day's 5-min bars (before the report)
    eod_report: time = time(16, 30)
    eod_backfill: time = time(3, 45)  # morning catch-up: back-fill bars a missed EOD batch dropped
    # Rebuild the paper book each morning, so the overnight harvest is visible BEFORE the open
    # (#458). Until this existed `_export_portfolio` ran only at the 16:30 EOD, so a night's
    # reconstructed days sat unpublished for 13.5 hours — through the whole trading day they were
    # harvested for. 03:15 is the one slot that clears everything: the harvest hard-stops at 03:00,
    # `eod_backfill` is at 03:45, and the scan window opens at 04:00. `publish-dashboard` runs
    # every 15 min, so the page is live by ~03:30.
    portfolio_refresh_et: time = time(3, 15)
    # EOD batch resilience (#100): retry a disconnect / transient failure instead of skipping.
    eod_retry_attempts: int = 3
    eod_retry_delay_sec: float = 60.0
    backfill_days: int = 3  # how many recent calendar days the morning catch-up scans
    # Daily cron jobs tolerate a brief event-loop delay before being counted as misfired/skipped
    # (APScheduler's default is 1s — too tight for once-a-day critical jobs). Kept well inside the
    # 16:20 -> 16:30 eod_bars -> eod_report gap.
    cron_misfire_grace_sec: int = 300
    # Trading-calendar override (#137): extra NON-trading dates on top of the XNYS calendar
    # (market_calendar.py) — patches an unscheduled closure (e.g. a national day of mourning)
    # without waiting for a library release. Env: CALENDAR_CLOSED_DATES='["2026-01-09"]'.
    calendar_closed_dates: tuple[date, ...] = ()

    # IB Gateway daily auto-restart (IBC AUTO_RESTART_TIME). Disconnects in this window are
    # treated as expected, not cold failures.
    gateway_restart: time = time(23, 45)
    gateway_restart_window_min: int = 10

    # Scanner (issue #13) — validated definition from spike #8.
    scan_code: str = "TOP_PERC_GAIN"
    scan_location: str = "STK.US.MAJOR"
    scan_min_price: float = 1.0  # widened from $2 → $1–$50 universe (#126)
    scan_max_price: float = 50.0  # widened from $10 → $1–$50 universe (#126)
    scan_change_pct: float = 10.0
    scan_min_5m_volume: int = 100_000  # trailing 5-min volume -> stVolume5minAbove
    # Collect the full scanner breadth (IBKR API hard-caps numberOfRows at 50). Phase-1 is a
    # data-collection exercise — on busy mornings there are far more than 10 low-float runners in
    # play, and store-raw/compute-on-read means we want the whole ranked list captured. We still
    # only *act* on the top few; the extra rows are dataset upside (#126 widened the universe too).
    scan_max_rows: int = 50
    # IBKR `stkTypes` codes to exclude from the STK scan. `STK.US.MAJOR` mixes common stock with
    # ETFs/ETNs (incl. leveraged single-stock products like MSTX/RKLX), which have no share float
    # and aren't Warrior-style momentum candidates — drop them server-side. Empty tuple = keep all.
    scan_exclude_stock_types: tuple[str, ...] = ("ETF", "ETN")

    # Gate thresholds (issue #15) — most reuse the scan_* values above.
    # ⚠️ `float_max_shares` GATES NOTHING. `gates.py::float_gate` has exactly one caller — the EOD
    # report's `float_ok` count — so this is a reporting threshold, not a filter, and the paper book
    # takes names far above it (CLSK 246M, XRX 119M). Eight surfaces asserted otherwise before #551;
    # if float should ever gate, the check goes in `portfolio.extract._qualify` and this comment
    # comes out. Same story for `news_gate` / `with_recent_news`. See `research/strategy.md` §4.
    float_max_shares: int = 20_000_000  # float < 20M SHARES (not $), for the report count only

    # Bull-flag detection — engine v2 (#176/#182; see `research/bull-flag.md` + `engine-v2.md`).
    # The pole is a run of HIGHER HIGHS, colour-gated to green thrust bars (a red PEAK is allowed
    # and rejected by the peak_green gate, not by the walk); even a SINGLE higher-high bar is a
    # pole, and pole_len counts the higher highs. The consolidation is a pullback that makes LOWER
    # HIGHS (the trader tracks highs, not lows) and holds within max_retracement of the pole (a
    # deeper pullback retraces "back through the pole"). Volume: the pole's peak bar volume must
    # exceed the consolidation's (hard); the consolidation volume ideally reduces (soft, recorded).
    #
    # These are read by BOTH detectors: `day.detect_day_with_settings` (the live path — rmetrics /
    # charts) and `setup.detect_setup_with_settings` (end-anchored, tests / ad-hoc replay). Since
    # #302 there is no second set of caps hiding in function defaults.
    bull_flag_min_pole: int = 1  # a pole can be a single higher-high bar
    # Caps locked by the engine-v2 review (#176/#182): 4 and 4, NOT the legacy 8/6. Until #302
    # these lived only as `detect_day` defaults and the values here were stale fiction — the live
    # detector never read them. They are now the single source of truth for both detectors.
    bull_flag_max_pole: int = 4  # cap on the higher highs counted as the pole
    # Minimum share of the pole's advance each EXTENSION bar must carry (#585). A bar that ticks
    # higher but adds almost nothing to the move is a quiet pause, not thrust, and it inflates
    # pole_len while deflating pole_velocity and the score (AKAN 2026-05-22: 08:00 contributed 6.8%
    # against 32% and 61% for the real thrust bars). Deliberately a WITHIN-POLE share: on a frozen
    # pre-market tape every trailing-relative measure is inflated, and each alternative tested ranks
    # AKAN's quiet bar above the WULF extension a reviewed fixture keeps. 0.0 disables it.
    # The admissible window is only (0.0677, 0.1058) — one observation at each end, so provisional.
    bull_flag_pole_min_step_share: float = 0.08
    # Minimum body fraction for a bar to EXTEND the pole (#607), split out from the locked 0.50 in
    # `is_big_green`. A hard cut with no tolerance truncates poles on near-misses and inflates the
    # reported retracement: BNAI 2026-06-09's 06:20 bar ran +7.5% on 163k shares carrying 72% of the
    # pole's advance and was called a quiet pause on a body of 0.4861 — a 1.4-point miss. Read ONLY
    # by refine_pole's walk; `significant_cycles` and `pole_has_big_green` keep 0.50, or exhaustion
    # counts move with it. Admissible window (0.4526, 0.4861] — CIFR 2026-07-06's 11:35 bar stays
    # out, BNAI's comes in — i.e. 0.033 wide on two observations. As provisional as the step share
    # above; a reviewed case inside that window closes it.
    bull_flag_pole_extension_min_body: float = 0.47
    bull_flag_max_cons: int = 4  # max consolidation candles
    # Minimum meaningful pole move (#176, `research/bull-flag.md §3.4`): a "pole" that rises less
    # than this fraction of its base is noise, not a thrust. A loose floor — the abnormality signal
    # is the ATR ratio, this just drops the flat stuff.
    bull_flag_min_pole_pct: float = 0.02
    # Lookback for the trailing ATR baseline the pole's abnormality is measured against.
    bull_flag_atr_window: int = 14
    bull_flag_max_retracement: float = 0.50
    # `vol_peak_gt_cons` as a tolerance rather than a boolean (#606). The locked #127 rule asks
    # whether the thrust carried more conviction than the pullback, but testing
    # `peak_vol > max(cons_vol)` made a 3.7% miss on a 5-minute volume bucket reject identically to
    # a 90% one. SPRC 2026-05-28 fails at 0.9633 (peak 1,752,451 vs consolidation 1,819,266) with
    # every other gate comfortable, never stops out and runs +2.97R. 1.0 reproduces the strict rule
    # exactly. ⚠️ This admits 2 trades on 31 recon sessions — SPRC and CLPT 2026-06-17 — for +4.34R.
    # n=2 is a coin flip, not an edge; it is a judgement that a 5% band is measurement noise on a
    # volume bucket, not a change of the rule's intent.
    bull_flag_min_vol_ratio: float = 0.95
    # A consolidation bar counts as a HALT rather than a quiet tape when nothing traded in it while
    # a neighbouring bar cleared this floor (#604). Same value as `scan_min_5m_volume`, for the same
    # reason: a bar in which zero shares changed hands, next to one clearing the scanner's own 5-min
    # volume bar, is a pause. AHMA 2026-06-09 shows the LULD signature — three zero-volume,
    # zero-range bars in 25 minutes, each followed by an 8-15% gap on resumption, on a tape
    # printing 0.9-6.1M shares per bar.
    # Publishes a FLAG, never a gate: a halted bar is a well-formed candle by shape, and `passed`
    # answers shape. The unusable-stop half is handled structurally by `cons_has_range`.
    # 0.0 disables the flag. Sensitivity: 50k/100k/250k flag 17/15/8 live setups — 100k is the knee.
    data_quality_halt_neighbour_volume: float = (
        100_000.0  # reject flags retracing > this fraction of the pole
    )
    # Pole wick quality (#132): reject a pole whose peak (highest-high) bar closed weakly — upper
    # wick > this fraction of the bar's range. A clean thrust closes near its high; a wicky one
    # (AHMA/VRXA) is a no-trade. Whether the pole holds a big green candle is recorded, not gated.
    bull_flag_max_peak_wick: float = 0.50
    tick_size: float = 0.01  # min US price increment for names ≥ $1 (penny tick)
    # Engine-v2 entry trigger (#182/#190, validated via per-opportunity visual review): the
    # breakout is confirmed 1 tick above the last consolidation candle's high — a lower high, per
    # the trader's rule. Often the fill is this exact price; bull_flag_fill_offset_ticks below is a
    # separate, deliberately conservative slippage estimate used only for R-measurement, not for
    # deciding whether/when triggered. (The legacy 5-tick `entry_offset_ticks` went with the legacy
    # detector in #296/#302 — v2 has no use for it.)
    bull_flag_trigger_offset_ticks: int = 1
    # Conservative slippage-modeled FILL price for R-measurement (#182/#190; confirmed by the
    # trader): the trigger (above) decides WHEN a setup fires; once fired, R is measured against a
    # worse, 3-tick fill to avoid overstating the edge — "often I fill at the trigger price anyway,
    # 3 ticks is being conservative." Applied downstream of the trigger, not in place of it.
    bull_flag_fill_offset_ticks: int = 3
    # Exhaustion (engine-v2 full-day detector, #102/#211): reject entry on the (cap+1)'th contiguous
    # significant pump/fade cycle of the day — "entering the third cycle is entering an exhausted
    # opportunity" (trader). A cycle counts if its pole carries a green thrust bar and a bar
    # ANYWHERE IN THE CYCLE — pole or fade (#582) — clears scan_min_5m_volume // 2, and it abuts
    # the run (see bullflag.cycles).
    bull_flag_exhaustion_cap: int = 2
    # Let the session's FIRST bar anchor a single-bar pole (#587, ON since #599). A day that gaps up
    # and runs on its opening print has no prior bar to be higher than, so `segment_cycles` never
    # proposes it and the greedy walk moves on to a later, smaller pole (MTVA 2026-05-19; SBFM
    # 2026-05-18 run 1, whose 04:00 thrust broke down through its own base unnoticed).
    #
    # #587 shipped this off, costed on trades gained (+1) — the wrong measure. It is mainly a
    # REVIEW defect: over the recon record it changes 34 chosen poles, and with it off **0 of those
    # 34 shapes passed**, at a median retracement of 1.193. A retracement above 1.0 means the
    # "consolidation" fell clean through the bottom of the "pole" — the engine had latched onto a
    # fragment and published a fictional rejection reason (RGTI 2026-05-22 read 11.39). With it on
    # the median is 0.920 and 4 pass. `passed` exists to say whether the flag is well-formed, so
    # publishing a number like 11.39 breaks the one thing the review page is for.
    #
    # 28 of the 34 still fail cons_retracement — now honestly, against the right pole. A gap bar's
    # low IS its opening print, so the pole base it anchors is arguably the wrong reference; that is
    # a retracement-anchor question (#598), not a reason to keep choosing the wrong pole.
    bull_flag_gap_pole: bool = True
    # Entry staleness (#130): a break more than this many minutes after the scanner appearance reads
    # as "faded" — the opportunity is no longer takeable (AHMA triggered ~1hr+ after the scan). Only
    # applies when the appearance (first_hit) is known; a large value disables the bound.
    # The bound is INCLUSIVE (#586): a trigger bar opening at exactly +N min is still fresh, because
    # this is a duration and not a deadline. See the comment at bullflag/day.py's staleness test,
    # which contrasts it with the selection window's strict cutoff.
    entry_staleness_min: int = 30

    # --- Selection: which setups are TAKEABLE (#567) ------------------------------------------
    # The engine answers "is this a trade we would select"; the book answers "given the trades we'd
    # select, what happens to $500". These two rules decide selection, so they live here with the
    # shape gates and not in `portfolio_*` where they used to sit — the split was arbitrary and the
    # `portfolio_` prefix was lying about which question they answer.
    #
    # They bite on `DaySetup.takeable`, deliberately NOT on `passed`. `passed` means "the bull flag
    # is well-formed" — the shape grammar the review workbench and the 25 golden fixtures are
    # written against. A $1.50 name or an 11:00 break is a perfectly good flag we simply don't
    # trade, and Phase 1 needs it to stay visible and scoreable on the results page. Folding these
    # into `passed` would report it as a malformed setup and throw the data away.
    #
    # Price band, tested against `entry_fill` (the conservative 3-tick fill, so a name is judged on
    # the price the book would actually pay). **Widened to match the $1–50 scan on 2026-08-07
    # (#608)** — deliberately temporary, for the collection phase, and the owner intends to shrink
    # it again once the record can say where it belongs.
    #
    # Why: across 27 reviewed opportunities the band was the deciding rejection in 13 of them, more
    # than every shape gate combined — including setups the trader read as clean trades (MGM ran
    # +6.79R with MAE 0.48R and never stopped out; QTEX passes all eight shape gates at $1.26). A
    # narrow band during collection means the record never learns whether those names were tradable.
    #
    # It costs the virtual book, and that is the accepted trade: over 31 recon sessions the takeable
    # population goes 25 → 46 and realised R goes +0.60 → −8.96 (equity $484 → $312, max drawdown
    # 30.8% → 45.7%). The admitted names are worse on average than the ones already selected, which
    # is what a selection rule that was doing something looks like. Coverage was bought with
    # performance on purpose — see research/decisions.md and #608 before reading the published book.
    #
    # History: floor was $1 until #386 raised it to $2 on 2026-07-31 (an owner's call on the book,
    # NOT a cost argument — `research/broker-costs.md` §3 stands either way); the scan floor has
    # been $1 throughout.
    select_price_min: float = 1.0
    select_price_max: float = 50.0
    # Trigger-time window: `start` <= trigger bar open < `end` (floor inclusive, cutoff strict).
    #
    # ⚠️ This is NOT the scan window. `scan_start`/`scan_end` (04:00–11:59) bound when the scanner
    # RUNS and what gets captured; this bounds what is takeable. They are deliberately different:
    # names outside this window keep being collected and scored, which is the whole point of a
    # data-collection phase. Before #567 this was the only effective time-of-day rule in the system
    # and it lived in the book, while the engine carried a 04:00–11:59 window that gated nothing.
    #
    # ⚠️ The floor is OPEN — 04:00, i.e. the whole pre-market (#569, 2026-08-07, reversing #405's
    # 05:30). Measured before taking it: over 30 sessions the earlier floor adds 4 trades, ALL of
    # them stop-outs triggering 04:20–04:30, for −4.69R and −$74.58 (−11.5% of the book). Nothing
    # was displaced — the earlier triggers pushed no later winner out of the 2/day cap.
    #
    # Taken anyway, and the reasoning matters more than the number: n=4 is not evidence (four
    # losses at a ~43% base rate happens ~10% of the time by chance), and the floor was never a
    # measured edge either — the time-of-day report found no pre-market window statistically
    # separable from another (the 04:00–06:00 block is −0.32R over 86 triggers, but permuting
    # entry-time labels within a day reproduces that spread 68% of the time). So this collects the
    # early tape in the book rather than assuming it away. Revisit once there are more than four
    # early triggers to judge on. `spikes/window_0400.py` re-runs the comparison.
    #
    # Cutoff tightened 09:30 → 09:15 (2026-07-21): the final pre-open ramp/auction trades like the
    # open, which this strategy excludes (a VMAR entry at ~09:25 on 2026-07-20 lost). Spike
    # #379/#380 only swept relaxations (10:00–12:00), all worse. Last takeable bar opens 09:10.
    select_window_start: time = time(4, 0)
    select_window_end: time = time(9, 15)

    # Capture (issue #14). The intraday tick only does discovery (scanner_hits + opportunities +
    # news/fundamentals). The day's 5-min bars are fetched once in an end-of-day batch (#62) —
    # capture_end marks the last bar time we care about (regular close).
    capture_end: time = time(16, 0)
    # Chart-start bound for the review workbench's full-day series (#140/#141): charts render the
    # whole trading day `chart_start <= t < capture_end` ET (04:00–16:00), not just the run window.
    chart_start: time = time(4, 0)
    tick_interval_sec: int = 60  # how often the scan/discovery loop runs
    eod_bars_duration: str = "1 D"  # reqHistoricalData duration for the EOD 5-min bar batch
    news_providers: str = "BRFG+DJ-N+DJNL"

    # Re-entry segmentation (issue #36) — a symbol can form >1 opportunity/day. A gap of
    # >= reentry_gap_min with no scanner hits starts a NEW run (e.g. pre-market pop → fade →
    # market-open pop). Each run is analysed over its own bar window, extended back
    # reentry_lookback_min so the run's pole is included.
    reentry_gap_min: int = 60
    reentry_lookback_min: int = 30
    news_lookback_days: int = 7
    news_max: int = 10

    # Async safety — bound blocking/remote calls so a hung dependency can't wedge the loop.
    ibkr_request_timeout_sec: float = 30.0
    fundamentals_timeout_sec: float = 10.0
    heartbeat_timeout_sec: float = 10.0
    # Spacing between successive historical requests in the EOD/back-fill batch, to stay clear of
    # the IBKR pacing limit (< 60 historical requests / 10 min) on heavy days (#163-C2).
    ibkr_hist_pacing_sec: float = 0.2

    # Float source hardening (#109): FMP /shares-float, primary over yfinance on read. Unset →
    # yfinance-only, nothing breaks. Free tier is 250 req/day, US stocks — ample at ~10 flags/day.
    fmp_api_key: str = ""

    # Virtual-portfolio tracker (#230) — a pre-shadow paper book computed on-read over the captured
    # dataset. Rules locked in research/decisions.md (2026-07-15): UK cash account, capital-based
    # sizing, strict pre-market fills, engine-v2 takeable setups only, fixed-R exit + breakeven.
    portfolio_start_equity_usd: float = 500.0
    # Sizing = risk-based, capped by notional (#237). Each position targets `risk_fraction` of the
    # day's opening equity at risk (qty ≈ equity × risk_fraction / (entry − stop)) but is capped at
    # `position_fraction` of opening equity in notional (qty ≤ equity × position_fraction / entry).
    # The cap binds on wide stops, the risk target on tight ones; qty = min(risk_qty, cap_qty), so
    # the cap is always the upper bound — that is what keeps the settled-cash invariant intact.
    portfolio_risk_fraction: float = 0.05  # target risk per trade, as a fraction of opening equity
    portfolio_position_fraction: float = 0.50  # max position notional, as a fraction of opening eq.
    portfolio_max_trades_per_day: int = 2  # cap 50% × 2 = at most fully deployed → 2 concurrent
    # ⚠️ The entry price band and the takeable trigger window used to live here. They are
    # SELECTION rules, not execution ones, so #567 moved them to `select_price_min/max` and
    # `select_window_start/end` beside the shape gates. The book no longer decides which
    # setups are takeable — it decides what to do with the ones the engine selected.
    # Symbols to exclude from the paper book. Before #226/#227 added the scanner's `stkTypes`
    # ETF/ETN filter, `STK.US.MAJOR` captured a handful of leveraged single-stock ETFs (no share
    # float, not Warrior-style candidates) that then flowed into this compute-on-read book. The
    # scanner no longer captures them, but the already-stored opportunities still would; drop them
    # here so the historical book is clean. Verified no-float in the captured fundamentals.
    portfolio_exclude_symbols: tuple[str, ...] = ("CCUP", "CRCG", "OKLL", "SNDQ")
    portfolio_target_r: float = 2.0  # fallback fixed R target (used until the window has samples)
    portfolio_breakeven_r: float = 0.0  # arm a breakeven stop once +Nb·R is reached; 0 disables
    # Adaptive target: each day re-fits the target to the highest-expectancy grid value over the
    # trailing window of prior candidates. Small-N overfit is guarded by the window + plateau bias.
    portfolio_target_grid: tuple[float, ...] = (1.5, 2.0, 2.5, 3.0)
    # Trailing lookback for the expectancy re-fit, in CALENDAR days — or **None for all history**,
    # which is the shipped default (2026-08-06, #476).
    #
    # A trailing window is itself a regime bet: if the trade distribution were stationary you would
    # use every trade you have. Window length trades **estimation error** (longer is better) against
    # **regime staleness** (shorter is better), and at n=13 we are overwhelmingly in the
    # estimation-error-dominated half — discarding trades to stay current buys nothing when the
    # current estimate is mostly noise. History: 20 days (#239) never let the optimiser fire at all
    # because the window held at most 7 trades against `min_samples` of 8; #463 widened it to 40 as
    # a fix for that, which was a repair, not a considered choice of horizon. Shorten this again
    # only once N is large enough that regime drift is something we can *measure* rather than
    # assume. `min_samples` deliberately has not moved — firing sooner on 5 trades buys a number,
    # not evidence.
    portfolio_adaptive_window_days: int | None = None  # None = fit on every prior trade
    portfolio_adaptive_min_samples: int = 8  # need this many trailing trades before re-fitting
    # Margin the re-fit's pick must clear, in standard errors, before the book switches OFF the
    # `portfolio_target_r` fallback (#476). The comparison is **paired** — the same trades scored
    # under both exit rules, so per-trade variance largely cancels and only the difference carries
    # noise — which is both the correct test and far tighter than comparing two independent means.
    # Measured on the first 13 trades against the 2.0R fallback: 1.5R is decisively worse
    # (z=-4.38) while 2.5R (z=-0.89) and 3.0R (z=-0.84) are simply undecided. 1.0 = "the edge is at
    # least one standard error"; 1.96 is the strict two-sided 95% bar, which this sample cannot yet
    # satisfy for any target. 0 disables the gate (pure argmax, the pre-#476 behaviour).
    portfolio_target_switch_z: float = 1.0
    # Adaptive risk throttle / kill-switch (#239): the per-trade `risk_fraction` itself walks a
    # small ladder from 0 up to `portfolio_risk_fraction`, driven by recent daily results. The
    # adaptive book starts at full risk (top rung) and steps ONE rung only after `risk_step_days`
    # net-positive days *in a row* (up) or the same run of net-negative days (down); a day's result
    # is the aggregate realised R over its qualifying setups, and a flat / no-setup day holds both
    # the rung and the streak (an info-less day carries no momentum — "in a row" counts decisive
    # days). At the 0% rung no capital is committed, but the day's *would-be* setups are still
    # scored (the signal is size-independent by design) so the switch re-arms when the tape turns.
    # Few rungs = a fast wind-up to full risk. `risk_rungs=1` disables the throttle. Only the
    # adaptive book throttles; fixed-target books stay at full `risk_fraction` as a baseline.
    #
    # ⚠️ DISABLED 2026-08-06 (#474), reversing #239. The ladder is a bet on serial correlation of
    # daily results and there is none to be found: lag-1 autocorrelation +0.31 over 12 active days
    # (permutation p=0.27), and conditioning on TWO up days is worse than one — the wrong shape for
    # a momentum story. Worse, it is not neutral when that bet is absent. Over 500
    # calendar-preserving shuffles (day order permuted, trade population preserved, so serial
    # correlation is zero by construction) the ladder cost a mean $22.35, losing on 291 shuffles and
    # winning on 72. The mechanism is mechanical: fixed-fractional sizing ALREADY de-risks after a
    # loss — 5% of a smaller balance is fewer dollars — and the ladder cuts a second time on the
    # same information. On the live path it cost $32.84 (5.3% of the book) for 0.01pp of drawdown.
    # Nothing is deleted: `risk_ladder` / `step_risk_rung` / `_day_signal_r` stay tested, so this is
    # a one-line re-enable once the sample can detect the effect (~85 active days; we have 12).
    portfolio_risk_rungs: int = 1  # 1 = throttle OFF (always full risk). 3 → (0, 2.5%, 5%).
    portfolio_risk_step_days: int = 2  # consecutive same-direction days to move a rung (1 = eager)
    # Costs, netted out of every trade so the equity curve is honest at ~$250 notional. Full IBKR
    # TIERED US-stock schedule per research/broker-costs.md (#232) — tiered UNBUNDLES the exchange /
    # regulatory pass-throughs, and at these share counts they roughly equal the commission itself,
    # so charging commission alone understates a round trip by 20-50%. Rates are per ORDER SIDE.
    portfolio_commission_per_share: float = 0.0035
    portfolio_commission_min: float = 0.35
    # Exchange liquidity-REMOVAL fee. Entries are stop/stop-limit triggers above the consolidation
    # high and exits are stops/market, so this book is always marketable and never earns the
    # add-liquidity rebate. Representative lit-venue rate; varies by venue (#232 §1 caveat).
    portfolio_exchange_fee_per_share: float = 0.0030
    portfolio_clearing_fee_per_share: float = 0.0002
    # Sell-side only:
    portfolio_taf_per_share: float = 0.000166  # FINRA Trading Activity Fee
    portfolio_taf_max: float = 8.30  # per-order cap (never binds at this size; kept for fidelity)
    portfolio_sec_fee_rate: float = 0.0000278  # SEC Section 31, on proceeds
    portfolio_exit_slippage_ticks: int = 2  # slippage on stop / mark-to-close exits (limit TP = 0)
    # Market data (#232 §4). $10/mo is ~2%/mo of a $500 book — the whole point of #232 is that fixed
    # costs do NOT scale down with capital, so the curve carries it. Charged at month rollover and
    # waived when that month's IBKR commission clears the threshold.
    portfolio_market_data_usd_per_month: float = 10.0
    portfolio_market_data_waiver_usd: float = 30.0
    # Withdrawals + UK tax + running cost: the "getting paid" layer on top of the paper book. The
    # book is kept in USD (funded once from GBP, then permanently USD, broker-costs.md), so pounds
    # are derived through one assumed rate, not a daily FX series. The rate is quoted GBP/USD the
    # market way: 1 GBP = `gbpusd_rate` USD, so USD->GBP divides and GBP->USD multiplies. A single
    # rate is an approximation; per-disposal daily rates would be the accurate, heavier alternative.
    # Locked 2026-07-16, research/decisions.md.
    portfolio_gbpusd_rate: float = 1.27
    # Withdrawal policy: pay out a share of NEW profit above a high-water mark, every N months, but
    # never below the viability floor and never distributing cash reserved for tax. The HWM ratchets
    # to the post-withdrawal balance so each period only pays on genuinely new profit. At the $500
    # start the floor makes the whole layer a no-op — it only begins paying once the account clears
    # the floor, which is the honest state (broker-costs §9: $500 is plumbing validation).
    portfolio_withdraw_fraction: float = 0.5  # share of profit above the HWM paid out each period
    portfolio_withdraw_cadence_months: int = 3  # quarterly
    portfolio_withdraw_floor_usd: float = 2000.0  # never withdraw below this settled-USD balance
    # UK Capital Gains Tax on net realised gains. Higher-rate share CGT is 24% (post-30-Oct-2024) on
    # gains above the £3,000 annual exempt amount, reserved per UK tax year (6 Apr–5 Apr). The rate
    # is a knob so the income-tax "treated as a trade" scenario (~42–47% incl. NIC) can be modelled
    # without code changes — see research/decisions.md for the CGT-vs-trading-income risk.
    portfolio_cgt_rate: float = 0.24
    portfolio_cgt_annual_exempt_gbp: float = 3000.0
    # VPS running cost, charged at month rollover like the market-data fee but kept as its own line
    # (different real-world expense). The Hetzner CX23 is €6.59/mo per the console's price estimate
    # — not an invoice; none exists yet (box created 2026-07-01). Held here in GBP (€6.59 × ~0.865
    # EUR/GBP) because the whole cost model is GBP-denominated and converts to USD through the
    # single portfolio_gbpusd_rate. The EUR/GBP rate is baked into this figure rather than being its
    # own knob — revisit if the euro moves materially.
    # Unconfirmed (#284): the estimate may have included a 10 GB volume (deleted 2026-07-17), which
    # would make this ~£0.41/mo high. Reconcile against August's invoice — July's is muddied by the
    # volume's partial month.
    portfolio_vps_gbp_per_month: float = 5.70
    # --- Forward projection: what does the NEXT year look like, and when does it pay? ---
    # The book above is a backward-looking record. These drive a bootstrap Monte-Carlo forward from
    # its closing balance — resampling the book's own *trading days* (not individual trades) in
    # short blocks, so a day's two concurrent positions stay together and short streaks survive,
    # which is what makes the projected drawdowns believable rather than i.i.d.-smooth.
    portfolio_projection_days: int = 252  # trading days to project ≈ one calendar year
    portfolio_projection_paths: int = 500  # Monte-Carlo paths; the fan is percentiles across these
    # Moving-block bootstrap length. 1 = i.i.d. days, which destroys the clustering that makes a
    # drawdown deep; ~a trading week keeps losing runs (and the kill-switch's own memory) intact.
    portfolio_projection_block_days: int = 5
    # Fixed seed: publish-dashboard rebuilds every 15 min, and an unseeded fan would jitter between
    # publishes so the page looked like it was reporting news when it was reporting noise.
    portfolio_projection_seed: int = 230
    # The income the strategy is being asked to replace. Inside IR35 the assignment rate is taxed
    # as employment income, so the honest comparison against a post-CGT withdrawal is net-vs-net:
    # `net_fraction` is the share of the *assignment rate* that reaches the bank through an
    # umbrella (employer NI + apprenticeship levy off the top, then PAYE/NI with the personal
    # allowance tapered away above £100k). ~0.52 at £176k/yr; it is a knob, not a tax engine —
    # change it rather than reading the default as advice.
    portfolio_day_rate_gbp: float = 800.0
    portfolio_day_rate_days_per_year: int = 220
    portfolio_day_rate_net_fraction: float = 0.52
    # Rungs for the "capital needed to pay me £X/month" ladder, in GBP/month. The day-rate figure
    # is appended at render time, so this is the road up to it.
    portfolio_income_targets_gbp_per_month: tuple[float, ...] = (500.0, 1000.0, 2500.0, 5000.0)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached)."""
    return Settings()
