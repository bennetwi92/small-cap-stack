"""Guards for deployment artifacts (#6): presence, key wiring, and no committed secrets."""

from __future__ import annotations

import re
import tomllib
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

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


# --------------------------------------------- on-demand jobs get their own cgroup (#545)

#: Workflows that run work against the box's store on demand, and the label each passes.
BOX_JOB_WORKFLOWS = ("backfill-dashboard.yml", "data-export.yml", "deploy-backfill-publish.yml")


def test_no_workflow_execs_into_the_app_container() -> None:
    """`docker exec` puts the work inside the TRACKER's cgroup.

    compose caps the app at `mem_limit: 2g` with `oom_score_adj: 500`, and
    `build_portfolio_payload` holds every collected day's bars in memory regardless of which date
    was asked for (#273). So a growing backfill pushed that cgroup over and the kernel reaped the
    live tracker rather than the job — the shape of #264. `scripts/harvest.sh` was rewritten around
    this exact lesson, and `deploy/actions-runner-restart.conf` says "constraining backfill memory
    has to happen at the container level"; nothing had done it.

    It also matters for #544's timeouts: `docker exec` does not forward a cancellation inward, so
    killing the client left the work running. An attached `docker run` gets the signal.
    """
    offenders = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # the prose explaining all this necessarily names it
            if "docker exec" in stripped:
                offenders.append(f"{path.name}:{i}: {stripped}")
    assert not offenders, (
        "run box work via scripts/box-job.sh (its own container + cgroup), not docker exec:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("wf", BOX_JOB_WORKFLOWS)
def test_box_job_workflows_use_the_shared_runner(wf: str) -> None:
    """One runner, so the memory envelope and the session guard are single-sourced."""
    text = (ROOT / ".github" / "workflows" / wf).read_text()
    assert "scripts/box-job.sh" in text, f"{wf} must run its work through scripts/box-job.sh"
    assert "ignore_window" in text, f"{wf} must expose the session-window override"


def test_box_job_runner_bounds_memory_and_swap() -> None:
    """The container limit is the primary protection; the slice is only a backstop.

    A slice that isn't installed on the box is created unbounded, so `--memory` here is what
    actually holds on a fresh host. `--memory-swap` must equal it: that value is the COMBINED
    limit, so anything larger re-admits the swapping that thrashes the CX23 past sshd (#264/#320).
    """
    sh = (ROOT / "scripts" / "box-job.sh").read_text()
    assert "--memory=" in sh and "--memory-swap=" in sh
    assert '--memory="$MEM_LIMIT"' in sh and '--memory-swap="$MEM_LIMIT"' in sh, (
        "memory and memory-swap must be the same value, or the job can swap"
    )
    assert "--cgroup-parent=scs-jobs.slice" in sh, "without this the limits bind the docker client"
    assert "--oom-score-adj=800" in sh, (
        "under host pressure the kernel must take the job, not the app"
    )
    # Its own slice, not the harvest's: sharing lets a dispatched job push a running harvest over.
    assert "scs-harvest.slice" not in sh


def test_the_jobs_slice_is_stricter_than_the_container_limit() -> None:
    """So a job normally dies inside its own cgroup with an attributable OOM."""
    slice_unit = (ROOT / "deploy" / "scs-jobs.slice").read_text()
    assert "MemoryMax=1200M" in slice_unit
    assert "MemorySwapMax=0" in slice_unit, "the swapfile is the tracker's cushion, not a job's"


def test_box_jobs_refuse_the_live_session_window() -> None:
    """The harvest refuses outside 12:30-03:00 ET; these had no guard at all, so a phone dispatch
    at 09:45 competed with the live scan on a 2-vCPU box."""
    sh = (ROOT / "scripts" / "box-job.sh").read_text()
    assert "BOX_JOB_IGNORE_WINDOW" in sh, "there must be a documented override"
    assert "1610" in sh, "the window must end at the harvest's own 16:10 EOD recess (#455)"
    assert "exit 1" in sh, "it must refuse rather than wait — waiting holds the single runner"


def test_every_box_job_step_has_a_checkout_in_its_job() -> None:
    """`scripts/box-job.sh` is a WORKSPACE path, so the job must check the repo out (#545).

    This is the mistake #545's first draft shipped. `docker exec` needed nothing from the
    workspace, so neither backfill job had a checkout — and the self-hosted runner shares one
    workspace across workflows. Worse, `deploy-backfill-publish`'s deploy job sparse-checks-out
    only `.github/actions` and runs `git clean -ffdx` in that same directory immediately before
    the backfill job, so `scripts/` was deterministically absent: broken 100% of the time.

    The non-deterministic case is nastier than the deterministic one — if a previous run happened
    to leave a full checkout, the job silently executes a STALE box-job.sh from whatever ref that
    run used.
    """
    for wf in BOX_JOB_WORKFLOWS:
        text = (ROOT / ".github" / "workflows" / wf).read_text()
        # Split into jobs on the two-space job keys, then require any job that calls the runner to
        # also check out. Per job, not per file: the pipeline's deploy job has a checkout that
        # does NOT help its backfill job.
        blocks = re.split(r"^  (?=[\w-]+:\s*$)", text.split("\njobs:", 1)[1], flags=re.M)
        for block in blocks:
            if "box-job.sh" not in block:
                continue
            name = block.split(":", 1)[0].strip()
            assert "actions/checkout" in block, (
                f"{wf}:{name} runs scripts/box-job.sh but never checks the repo out — on the "
                f"shared self-hosted workspace that file is absent or stale."
            )
