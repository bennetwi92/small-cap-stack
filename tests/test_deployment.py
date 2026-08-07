"""Guards for deployment artifacts (#6): presence, key wiring, and no committed secrets."""

from __future__ import annotations

import re
import tomllib
from datetime import date, datetime, time, timedelta
from pathlib import Path

from small_cap_stack.clock import ET
from small_cap_stack.harvest.guard import RunWindow
from tests.support import settings

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_runs_the_module() -> None:
    df = (ROOT / "Dockerfile").read_text()
    assert "python:3.11" in df
    assert "small_cap_stack" in df


def test_compose_wires_gateway_and_app() -> None:
    c = (ROOT / "docker-compose.yml").read_text()
    assert "ibgateway:" in c and "app:" in c
    assert "ghcr.io/gnzsnz/ib-gateway" in c
    assert "IBKR_HOST: ibgateway" in c  # app talks to the gateway container
    assert "condition: service_healthy" in c  # waits for the gateway
    assert "TWS_PASSWORD: ${TWS_PASSWORD" in c  # secret via env, not hardcoded
    # The app resolves a prebuilt GHCR image; deploys pull it instead of building on-box (#278).
    assert "image: ghcr.io/bennetwi92/small-cap-stack:${IMAGE_TAG:-latest}" in c


def test_systemd_unit() -> None:
    s = (ROOT / "deploy" / "small-cap-stack.service").read_text()
    assert "docker compose up" in s
    assert "WantedBy=multi-user.target" in s
    # Boot must not build on-box (#278) — it competes with the live tracker for 2 vCPU / 4 GB.
    assert "--no-build" in s
    assert "up -d --build" not in s


def test_deploy_action_pulls_and_never_builds_on_box() -> None:
    """The box must never build (#278). The deploy lives in one composite action (#280)."""
    a = (ROOT / ".github" / "actions" / "deploy-app" / "action.yml").read_text()
    assert "docker compose pull app" in a
    assert "docker compose up -d --no-build app" in a
    assert "docker compose up -d --build" not in a
    # The image lands via a racing workflow — deploying without waiting 404s (#278).
    assert "docker manifest inspect" in a
    # Composite inputs are strings: negating one ("false") is truthy, so `!inputs.restart_only`
    # would silently skip every guarded step. Guards must compare against 'true' (#280).
    guards = [ln.strip() for ln in a.splitlines() if ln.strip().startswith("if:")]
    assert guards, "the restart_only guards disappeared"
    for g in guards:
        assert g == "if: inputs.restart_only != 'true'", g


def test_both_deploy_workflows_use_the_shared_action() -> None:
    """Neither workflow may re-inline the deploy — that drift is what #280 removed."""
    for name in ("deploy.yml", "deploy-backfill-publish.yml"):
        w = (ROOT / ".github" / "workflows" / name).read_text()
        assert "uses: ./.github/actions/deploy-app" in w, name
        # Composite actions resolve from the workspace, so the caller must check it out.
        # Version-agnostic: the pin moves (#282), the requirement doesn't.
        assert "actions/checkout@" in w, name
        assert "docker compose" not in w, f"{name} should delegate the deploy, not inline it"


def test_delegation_loop_can_authenticate() -> None:
    """`id-token: write` is what the agent exchanges for its GitHub token, so dropping it makes
    every delegation a red X before any work happens (#499, and #370 before it). The job's
    permissions are otherwise deliberately least-privilege (#348) — this one is load-bearing."""
    w = (ROOT / ".github" / "workflows" / "claude.yml").read_text()
    assert "id-token: write" in w


def test_build_image_covers_every_main_commit() -> None:
    """deploy resolves the image by commit SHA, so a path-filtered main build would strand
    commits with no image to deploy (#265's SHA was exactly that case, #278)."""
    w = (ROOT / ".github" / "workflows" / "build-image.yml").read_text()
    push = w.split("pull_request:")[0]
    assert "paths:" not in push, "main/tags builds must not be path-filtered"


def test_ci_installs_with_uv() -> None:
    """The Install step was ~23 s of a 75 s job under pip (#493). uv does the same install in a
    handful of seconds, and `--system` is what puts it in `setup-python`'s interpreter — without
    it the bare `ruff` / `mypy` / `pytest` steps find no package."""
    w = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "uv pip install --system" in w, "CI installs with uv, not pip"
    assert "pip install -e" not in w, "the pip install path is gone; uv replaced it"


def test_ci_gates_coverage_on_main_not_on_prs() -> None:
    """PRs run the suite bare and main carries the gate (#494/#495) — the two halves only add up
    together, so dropping either the main-side trigger or the threshold would leave coverage
    ungated everywhere."""
    w = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "branches: [main]" in w, "the main-side run is where the coverage gate lives"
    # One joined substring, not three independent ones: asserting the condition and the flags
    # separately would pass an INVERTED ternary (coverage on PRs, bare on main) — exactly the
    # silent-loss mode this test exists to prevent.
    assert (
        "${{ github.event_name == 'push' "
        "&& '--cov --cov-report=term-missing --cov-fail-under=90' || '' }}"
    ) in w, "the coverage flags must hang off the push-to-main condition, in that order"


def _addopts() -> str:
    """pytest's effective addopts, parsed — not grepped.

    A line-prefix scan is evadable: pytest accepts a multi-line `addopts = \"\"\"...\"\"\"` (and a
    TOML array), so `--cov-fail-under=80` could be smuggled back onto a continuation line while a
    string search of the first line stayed clean.
    """
    with (ROOT / "pyproject.toml").open("rb") as fh:
        cfg = tomllib.load(fh)["tool"]["pytest"]["ini_options"]
    opts = cfg.get("addopts", "")
    return opts if isinstance(opts, str) else " ".join(opts)


def test_coverage_flags_are_not_in_addopts() -> None:
    """A bare `pytest tests/one_file.py` must not fail on coverage (#530).

    With `--cov-fail-under` in addopts it printed a red "Required test coverage of 80% not
    reached. Total coverage: 4.77%" and exited 1 while every test passed — which teaches you to
    read past a failing pytest, on the suite that is the product.
    """
    opts = _addopts()
    assert "--cov" not in opts, f"coverage flags belong in `make cov` / CI, not addopts: {opts}"


def test_pytest_errors_on_warnings_and_unknown_markers() -> None:
    """Both are part of the #530 arrangement and were silently deletable without this."""
    with (ROOT / "pyproject.toml").open("rb") as fh:
        cfg = tomllib.load(fh)["tool"]["pytest"]["ini_options"]
    assert "--strict-markers" in _addopts()
    assert "error" in cfg["filterwarnings"], (
        "a dep deprecation must fail the build, not scroll past"
    )


def test_make_check_mirrors_the_ci_coverage_gate() -> None:
    """`make check` is only useful if it predicts CI. Both must run the same gate at the same
    number — a local gate that is laxer than the remote one is worse than none, because it is
    trusted."""
    mk = (ROOT / "Makefile").read_text()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    # The Makefile's EFFECTIVE gate, not any line mentioning it: a stale comment quoting the right
    # number would otherwise satisfy a whole-file substring search while COV said something else.
    cov = next(ln for ln in mk.splitlines() if ln.startswith("COV :="))
    assert "--cov-fail-under=90" in cov, f"the local gate is not 90: {cov}"
    assert "--cov-fail-under=90" in ci, "CI and `make check` must enforce the same number"
    with (ROOT / "pyproject.toml").open("rb") as fh:
        assert tomllib.load(fh)["tool"]["coverage"]["run"]["branch"] is True
    check = next(ln for ln in mk.splitlines() if ln.startswith("check:"))
    assert "cov" in check.split("##")[0].split(), (
        f"`make check` must run the covered target, not bare pytest: {check}"
    )


def test_runbook_present() -> None:
    assert "Hetzner" in (ROOT / "deploy" / "RUNBOOK.md").read_text()


def test_env_example_has_no_committed_secrets() -> None:
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith(("TWS_USERID=", "TWS_PASSWORD=", "HEALTHCHECKS_PING_URL=")):
            key, _, value = line.partition("=")
            assert value.strip() == "", f"{key} must be an empty placeholder in .env.example"


def _directives(name: str) -> list[str]:
    """A unit's directives, comments stripped — the comments explain the choices and would
    otherwise match the very strings these tests assert are absent."""
    return [
        ln.strip()
        for ln in (ROOT / "deploy" / name).read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_harvest_limits_live_on_the_slice_where_they_reach_the_container() -> None:
    """The kernel-enforced half of #431's memory story, fixed in #452.

    These directives were on the SERVICE, where they bounded a ~15 MB docker client and nothing
    else: `docker run` hands container creation to the daemon, which places it in Docker's own
    scope under system.slice. Measured on the box — default placement
    `/system.slice/docker-….scope`, versus `/scs.slice/scs-harvest.slice/docker-….scope` with
    --cgroup-parent. The in-process guard is a promise; this is the limit, and #264 is what
    happens when only the promise exists."""
    slice_unit = _directives("scs-harvest.slice")
    assert any(d.startswith("MemoryMax=") for d in slice_unit)
    assert "MemorySwapMax=0" in slice_unit
    # Weights, not Nice/IOSchedulingClass: on cgroup v2 these are what reach a child cgroup.
    assert any(d.startswith("CPUWeight=") for d in slice_unit)
    assert any(d.startswith("IOWeight=") for d in slice_unit)

    unit = _directives("scs-harvest.service")
    assert "Slice=scs-harvest.slice" in unit, "the wrapper must share the container's envelope"
    # The directives that used to be here did nothing; keeping them would re-assert the fiction.
    assert not any(d.startswith(("Nice=", "IOSchedulingClass=", "MemoryMax=")) for d in unit)
    # A restart loop against a rate-limited vendor is how the API key gets blocked; a failed night
    # costs nothing because the next one resumes from the checkpoint.
    assert not any(d.startswith("Restart=") for d in unit)


def test_the_harvest_container_is_placed_in_that_slice() -> None:
    """The limits above are inert without this flag — and it must be a `.slice`, because Docker
    here uses the systemd cgroup driver on cgroup v2, which rejects a `foo.service` parent."""
    script = (ROOT / "scripts" / "harvest.sh").read_text()
    assert "--cgroup-parent=scs-harvest.slice" in script
    # `nice`/`ionice` would deprioritise this shell and the docker client, never the daemon's
    # child. Removed in #452; assert on the exec line itself so the comment explaining that cannot
    # satisfy the test.
    exec_line = next(ln for ln in script.splitlines() if ln.startswith("exec "))
    assert exec_line == 'exec "${CMD[@]}"'


def test_the_harvest_window_cannot_overlap_the_trackers_own_day() -> None:
    """The INDEPENDENT anchor. Asserting the timer sits inside the configured window is circular —
    set `harvest_start_et=04:00` and a 05:00 fire passes while the harvest runs straight through
    the scan window. This pins the window against the tracker's schedule instead, which is the
    thing that actually must not be violated."""
    s = settings()
    assert s.harvest_start_et >= s.scan_end, "the harvest may not open before the scan closes"
    assert s.harvest_stop_et <= s.eod_backfill, "the harvest must stop clear of eod_backfill"
    # ...and it must duck the EOD jobs it now spans, rather than relying on HostGuard, which is
    # checked once per ~47-minute session and so cannot fire during them (#455).
    assert s.harvest_start_et < s.harvest_eod_recess_et < s.eod_bars_fetch < s.eod_report


def test_harvest_timer_fires_inside_the_window_and_never_catches_up_at_boot() -> None:
    """A Persistent=true timer could fire at boot — potentially 04:00, with the scan window
    opening. The harvest resumes from its checkpoint whenever it next runs, so there is nothing to
    catch up on."""
    timer = (ROOT / "deploy" / "scs-harvest.timer").read_text()
    assert "America/New_York" in timer
    assert "Persistent=false" in timer

    s = settings()
    window = RunWindow(start=s.harvest_start_et, stop=s.harvest_stop_et)
    for at in _timer_fires():
        # RunWindow wraps midnight, so this cannot be a `start <= t < stop` comparison.
        assert window.is_open(datetime.combine(date(2026, 8, 5), at, tzinfo=ET)), (
            f"{at:%H:%M} ET is outside the harvest window {window.describe()}"
        )


def test_the_timer_claims_the_widened_hours_and_can_recover_a_stopped_run() -> None:
    """Two properties, both of which a weaker test let through.

    A fire near the window's START is what buys the widened hours at all — without it the 12:30
    opening is committed in config and never used, and the change silently reverts to an evening
    harvest with every test still green.

    A fire after the EOD recess is what resumes the evening, and later ones recover a run
    `HostGuard` ended at an arbitrary session boundary. Both bounds come from `Settings`, so moving
    the EOD jobs or the recess fails here rather than drifting."""
    s = settings()
    fires = _timer_fires()
    assert any(f < s.harvest_eod_recess_et for f in fires), "nothing claims the afternoon hours"
    assert min(fires) <= _plus_minutes(s.harvest_start_et, 30), (
        "the first fire is too far into the window to use the hours it opens"
    )
    assert sum(1 for f in fires if f > s.eod_report) >= 2, (
        "one post-EOD fire cannot recover a run stopped at an arbitrary session boundary"
    )


def _plus_minutes(t: time, minutes: int) -> time:
    return (datetime.combine(date(2026, 1, 1), t) + timedelta(minutes=minutes)).time()


def _timer_fires() -> list[time]:
    """Every OnCalendar fire, parsed strictly — a spec systemd would reject must not read as valid.

    Deliberately not a loose split: a typo in one of four lines silently drops that fire, and the
    only symptom is fewer harvesting hours, which nothing else would catch."""
    timer = (ROOT / "deploy" / "scs-harvest.timer").read_text()
    fires: list[time] = []
    for line in timer.splitlines():
        if not line.startswith("OnCalendar="):
            continue
        m = re.fullmatch(r"OnCalendar=\*-\*-\* (\d{2}):(\d{2}):(\d{2}) America/New_York", line)
        assert m, f"unparseable OnCalendar spec: {line!r}"
        fires.append(time(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    assert fires, "the timer must schedule at least one fire"
    return fires


def test_harvest_script_runs_its_own_container_not_the_trackers() -> None:
    """`docker exec` into the app would spend the tracker's 2 GB budget and OOM the tracker instead
    of the harvest. A separate `docker run` dies alone."""
    lines = (ROOT / "scripts" / "harvest.sh").read_text().splitlines()
    sh = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
    assert "docker run --rm" in sh
    assert "docker exec" not in sh
    # memory-swap is the COMBINED limit, so equal values mean no swap at all — swapping a background
    # job is how the CX23 thrashes past sshd (#264).
    assert '--memory="$MEM_LIMIT"' in sh and '--memory-swap="$MEM_LIMIT"' in sh
    assert "--oom-score-adj=800" in sh  # the kernel's preferred victim, box-wide


def test_harvest_script_reasserts_the_container_data_path_over_the_env_file() -> None:
    """`.env` on the box is a copy of `.env.example`, which carries the LOCAL-dev
    `DATA_DIR=./data`. Passed through `--env-file` that overrides the image's `/data`, so the whole
    harvest would land inside the container's working directory and be deleted with it on `--rm` —
    a night's API budget written to a tmpfs. `-e` takes precedence over `--env-file`, so the
    container paths are re-asserted after it."""
    sh = (ROOT / "scripts" / "harvest.sh").read_text()
    env_file_at = sh.index("--env-file")
    for override in ("-e DATA_DIR=/data", "-e DUCKDB_PATH=/data/small_cap_stack.duckdb"):
        assert override in sh, override
        assert sh.index(override) > env_file_at, f"{override} must come AFTER --env-file to win"
    # A stalled harvest must never page as a tracker outage (#431).
    assert "-e HEALTHCHECKS_PING_URL=" in sh


# ------------------------------------------------------------- every job is bounded (#544)


def _workflow_jobs() -> list[tuple[str, str, str, int | None]]:
    """(workflow, job, runs-on, timeout-minutes) for every job in `.github/workflows`.

    Hand-parsed rather than via PyYAML, which is not a dependency of this project and would be one
    added solely for a test. The shape it relies on — two-space job keys, four-space job settings —
    is pinned by `test_the_job_inventory_is_not_empty`, so a reformat that broke it fails loudly
    rather than making every check below vacuous.
    """
    out: list[tuple[str, str, str, int | None]] = []
    wf_dir = ROOT / ".github" / "workflows"
    for path in sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]):
        in_jobs = False
        job = runs_on = ""
        timeout: int | None = None
        for line in path.read_text().splitlines():
            if re.match(r"^jobs:\s*$", line):
                in_jobs = True
                continue
            if not in_jobs:
                continue
            # Trailing comments and quoted keys were a SILENT bypass: the job vanished from
            # the inventory AND its `runs-on` was then attributed to the previous job, so the
            # all(runs_on) backstop was satisfied by a stolen value. Every job in these files
            # carries comments, so this is the realistic shape, not a hypothetical one.
            if m := re.match("^  [\"']?([\\w-]+)[\"']?:\\s*(?:#.*)?$", line):
                if job:  # a new job key closes the previous one
                    out.append((path.name, job, runs_on, timeout))
                job, runs_on, timeout = m.group(1), "", None
            elif m := re.match(r"^    runs-on:\s*(.+?)\s*$", line):
                runs_on = m.group(1)
            elif m := re.match(r"^    timeout-minutes:\s*(\d+)", line):
                timeout = int(m.group(1))
        if job:  # and end-of-file closes the last — the case a sentinel line got wrong
            out.append((path.name, job, runs_on, timeout))
    return out


def test_every_workflow_contributes_to_the_job_inventory() -> None:
    """The guards below are only as good as the parse, so pin the parse itself.

    Not a `len(jobs) >= N` floor: with 13 jobs that tolerates a whole workflow being deleted
    silently (#377 deleted a batch, so it happens), and then fails spuriously on the *second*
    deletion with a message that reads like a parser break rather than an intentional change.
    Per-file coverage binds regardless of how many workflows exist.
    """
    wf_dir = ROOT / ".github" / "workflows"
    files = {p.name for p in [*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]}
    jobs = _workflow_jobs()
    parsed = {wf for wf, _, _, _ in jobs}
    assert parsed == files, (
        f"the job parser missed {sorted(files - parsed)} entirely — every workflow defines at "
        f"least one job, so a file contributing none means the parse broke, and the timeout "
        f"guards below would pass vacuously for it."
    )
    # A job whose `runs-on` is empty means the parser lost its place mid-file, which previously
    # let a following job's value be attributed to the one before it.
    assert all(runs_on for _, _, runs_on, _ in jobs), f"a job has no runs-on: {jobs}"


def test_every_job_has_a_timeout() -> None:
    """GitHub's default is 360 minutes, and there is exactly ONE self-hosted runner.

    A hung job holds it and every other workflow queues behind — which is how an OOM-killed
    backfill took CI offline for 5h37m (#264). `harvest.yml` already carried this reasoning in a
    comment while applying it only to itself; #544 applied it everywhere.
    """
    untimed = [f"{wf}:{job} ({runs_on})" for wf, job, runs_on, t in _workflow_jobs() if t is None]
    assert not untimed, "these jobs can run for GitHub's default 360 minutes:\n" + "\n".join(
        untimed
    )


def test_box_jobs_are_bounded_well_below_the_harvest_window() -> None:
    """The box's jobs must not be able to run into each other.

    The harvest owns 12:30–03:00 ET and stops itself clear of the 03:45 `eod_backfill`. A
    non-harvest job on the box that could run for hours would defeat that scheduling, so they are
    capped at 30 minutes — comfortably above their observed p95 (deploy 0.9 min, publish 0.5).
    """
    over = [
        f"{wf}:{job} = {t}m"
        for wf, job, runs_on, t in _workflow_jobs()
        if "self-hosted" in runs_on and wf != "harvest.yml" and (t is None or t > 30)
    ]
    assert not over, "box jobs (other than the harvest) must be capped at <= 30 min:\n" + "\n".join(
        over
    )
