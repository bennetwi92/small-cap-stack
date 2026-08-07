"""Guards for deployment artifacts (#6): presence, key wiring, and no committed secrets."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from small_cap_stack.clock import ET
from small_cap_stack.config import Settings
from small_cap_stack.harvest.guard import RunWindow

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
    """PRs run the suite bare and main carries the 80% gate (#494/#495) — the two halves only add
    up together, so dropping either the main-side trigger or the addopts threshold would leave
    coverage ungated everywhere."""
    w = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "branches: [main]" in w, "the main-side run is where the coverage gate lives"
    assert "github.event_name == 'pull_request' && '--no-cov'" in w, "PR runs must skip coverage"
    assert "--cov-fail-under=80" in (ROOT / "pyproject.toml").read_text()


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
    s = Settings(_env_file=None)  # type: ignore[call-arg]
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

    s = Settings(_env_file=None)  # type: ignore[call-arg]
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
    s = Settings(_env_file=None)  # type: ignore[call-arg]
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
