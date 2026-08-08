"""Guards for deployment artifacts (#6): presence, key wiring, and no committed secrets."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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


def test_gateway_healthcheck_follows_the_trading_mode() -> None:
    """The probed port must track TRADING_MODE, not be hardcoded (#663).

    The API binds 4002 in paper and 4001 in live. A hardcoded 4002 can never pass in live mode,
    and `depends_on: {ibgateway: {condition: service_healthy}}` would then stop the app starting
    at all — a live-mode blocker sitting in committed config, invisible until the day it matters.
    """
    # Parsed from the raw text rather than with PyYAML, which is not a dependency of this project
    # and is not worth becoming one to read four lines. There is exactly one healthcheck in the
    # compose file (the app's lives in the Dockerfile), so the split is unambiguous.
    c = (ROOT / "docker-compose.yml").read_text()
    block = c.split("healthcheck:")[1].split("interval:")[0]
    probe = " ".join(
        " ".join(ln for ln in block.splitlines() if not ln.strip().startswith("#")).split()
    )
    assert "TRADING_MODE" in probe, "the healthcheck must branch on the mode, not assume one"
    assert "4001" in probe and "4002" in probe, "both the live and paper API ports must appear"
    # /dev/tcp is a bashism; sh silently treats it as a normal path and the probe stops probing.
    assert "bash -c" in probe

    inner = re.search(r"CMD-SHELL\s*-\s*'(.*)'", probe)
    assert inner, f"could not read the healthcheck command out of: {probe}"

    def probed_port(mode: str) -> str:
        # `$$` is compose escaping its own interpolation; the shell sees a single `$`. Stop before
        # `bash -c` so this resolves the port without opening a socket.
        script = inner.group(1).replace("$$", "$").split("bash -c")[0] + 'printf "%s" "$p"'
        return subprocess.run(
            ["sh", "-c", script],
            env={**os.environ, "TRADING_MODE": mode},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    assert probed_port("live") == "4001"
    assert probed_port("paper") == "4002"
    assert probed_port("") == "4002", (
        "an unset mode must fall back to paper, like the compose default"
    )


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


def test_ci_installs_from_the_lock() -> None:
    """The Install step was ~23 s of a 75 s job under pip (#493), and it also **re-resolved** —
    so CI and the shipped image could bake different polars/duckdb from the same commit (#546).

    Two flags do the work, and my first draft got both wrong:

    - **`--locked`, not `--frozen`.** Measured: with a dep added to pyproject and no re-lock,
      `--frozen` exits 0 and installs *without* it, while `--locked` exits 1 saying the lockfile
      needs updating. Only `--locked` is a gate.
    - **`uv run --no-sync`.** A bare `uv run` re-resolves and **rewrites** `uv.lock` before running
      the command. So the tool steps would quietly repair a stale lock, CI would go green on the PR
      and on the push to main, and `build-image` — which reads `requirements.lock` and does not run
      on pull requests — would build an image without the dependency. That surfaces as the live
      tracker ImportError-ing on the box, which is a worse failure than the one #546 set out to fix.
    """
    w = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "uv sync --locked" in w, (
        "CI must use --locked: --frozen installs a stale lock without complaining"
    )
    assert "uv sync --frozen" not in w, "--frozen is not a gate; see the docstring"
    assert "pip install -e" not in w, "the pip install path is gone; uv replaced it"
    for tool in ("ruff check .", "ruff format --check .", "mypy", "pytest"):
        assert f"uv run --no-sync {tool}" in w, (
            f"`{tool}` must run through `uv run --no-sync` — a bare `uv run` re-resolves and "
            "rewrites uv.lock, hiding a stale lock from CI and breaking only the image"
        )


def test_every_install_path_reads_the_lock() -> None:
    """Three installers, one recorded set. Before #546 they were `pip install -e '.[dev]'`, a bare
    `pip install -e .`, and a Dockerfile that extracted pyproject's unpinned ranges — resolving
    independently, on different days, from `>=` constraints with no upper bound.

    For a system whose product is numbers, that made "the box disagrees with my Mac" an
    undiagnosable statement rather than a bug report.
    """
    paths = {
        "ci.yml": (ROOT / ".github" / "workflows" / "ci.yml").read_text(),
        "spike-massive.yml": (ROOT / ".github" / "workflows" / "spike-massive.yml").read_text(),
        "Dockerfile": (ROOT / "Dockerfile").read_text(),
    }
    assert "uv sync --locked" in paths["ci.yml"]
    for name in ("spike-massive.yml", "Dockerfile"):
        assert "--require-hashes -r requirements.lock" in paths[name], (
            f"{name} must install the locked set with hashes, not resolve its own"
        )
    # The Dockerfile's old path extracted pyproject's ranges with tomllib — it must not come back.
    assert "tomllib" not in paths["Dockerfile"], (
        "the Dockerfile is back to extracting unpinned ranges from pyproject"
    )


def test_the_lockfiles_exist_and_pin_every_runtime_dependency() -> None:
    """`requirements.lock` is generated from `uv.lock` (`make lock`) and is what the pip-based
    paths read. Every line must be an exact `==` pin carrying hashes — a single `>=` surviving in
    there would reopen the hole for that package alone, silently."""
    uv_lock = ROOT / "uv.lock"
    req_lock = ROOT / "requirements.lock"
    assert uv_lock.is_file(), "uv.lock is the source of truth for resolution"
    assert req_lock.is_file(), "requirements.lock is what the Dockerfile and the spike install"

    body = [
        ln
        for ln in req_lock.read_text().splitlines()
        if ln and not ln.startswith(("#", " ", "\t", "--hash"))
    ]
    assert len(body) >= 30, "the export looks truncated"
    unpinned = [ln for ln in body if "==" not in ln]
    assert not unpinned, f"these are not exact pins: {unpinned}"
    assert "--hash=sha256:" in req_lock.read_text(), "the export must carry hashes"


def test_requirements_lock_is_exactly_what_uv_would_export() -> None:
    """The two lockfiles are one artifact in two formats, and only `uv.lock` is authoritative. If
    they drift, the image installs a set CI never tested — the original defect wearing a lockfile.

    **`uv export` is the oracle**, rather than a comparison written by hand. My first version
    diffed `requirements.lock` against `uv.lock` and needed a hand-kept list of dev-only packages
    to subtract; that list was already wrong on its first run (it missed three transitives), and a
    guard whose correctness depends on an allowlist someone must remember to extend is a guard
    that quietly stops guarding. Regenerating and comparing has no such list, and it is the
    definition of "in step" — this file is *by construction* what `make lock` produces.

    It also catches the direction that actually breaks the image: deleting `polars` outright from
    `requirements.lock` passed the hand-written version, and the image would have built clean and
    died at import on the box.
    """
    uv = shutil.which("uv")
    assert uv, (
        "uv is not on PATH, so this guard cannot run. CI installs it via setup-uv before pytest; "
        "locally, `make lock` needs it too."
    )
    result = subprocess.run(  # noqa: S603
        [uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, f"uv export failed: {result.stderr[-400:]}"

    def pins(text: str) -> list[str]:
        # The header records the command that produced the file, which differs by `-o` path — so
        # compare the pins and their hashes, not the bytes.
        return [ln for ln in text.splitlines() if ln and not ln.lstrip().startswith("#")]

    committed = pins((ROOT / "requirements.lock").read_text())
    fresh = pins(result.stdout)
    assert committed, "requirements.lock parsed to nothing; has the format changed?"
    assert committed == fresh, (
        "requirements.lock is not what `uv export` produces from the current uv.lock — "
        "run `make lock` and commit the result."
    )


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


def test_pyarrow_stays_declared_despite_having_no_import() -> None:
    """The trap this test exists for: `pyarrow` looks unused and is load-bearing (#521).

    There is no `import pyarrow` anywhere in the repo, so every "find unused dependencies" pass
    flags it — this repo's own audit did. But `storage.py` bridges DuckDB to polars with `.pl()`,
    which goes through Arrow and hard-requires it, and **nothing pulls it in for us**: duckdb
    declares it only under `extra == "all"`, polars only under `extra == "pyarrow"`. Dropping it
    breaks every store read, at import time, on a fresh install only.
    """
    with (ROOT / "pyproject.toml").open("rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]
    assert any(d.startswith("pyarrow") for d in deps), (
        "pyarrow must stay declared: storage.py's DuckDB->polars `.pl()` needs it and no other "
        "dependency requires it outside an extra. It has no import; that is not evidence."
    )
    # The thing that makes it necessary, so deleting the bridge is what retires the dependency.
    assert ".pl()" in (ROOT / "src" / "small_cap_stack" / "storage.py").read_text()


def test_runtime_deps_are_imported_or_explained() -> None:
    """Every declared runtime dep is imported somewhere, or carries a comment saying why not.

    Two legitimately are not — `pyarrow` (above) and `python-dotenv` (pydantic-settings needs it
    for the `env_file=` this project uses). Both are commented in `pyproject.toml`. A new
    unexplained one is either dead weight or the next `pyarrow`, and both want a human to look.
    """
    text = (ROOT / "pyproject.toml").read_text()
    block = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    explained: set[str] = set()
    prev_comment = False
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            prev_comment = True
            continue
        if line.startswith('"') and prev_comment:
            explained.add(line.strip('", ').split(">")[0].split("=")[0].split("<")[0].lower())
        prev_comment = False

    sources = "\n".join(
        p.read_text() for p in [*(ROOT / "src").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
    )
    module_of = {
        "python-dotenv": "dotenv",
        "prometheus-client": "prometheus_client",
        "ib-async": "ib_async",
        "exchange-calendars": "exchange_calendars",
        "pydantic-settings": "pydantic_settings",
    }
    with (ROOT / "pyproject.toml").open("rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]

    unexplained = []
    for dep in deps:
        name = dep.split(">")[0].split("=")[0].split("<")[0].strip()
        mod = module_of.get(name.lower(), name.lower().replace("-", "_"))
        if re.search(rf"^\s*(?:import|from)\s+{re.escape(mod)}\b", sources, re.M | re.I):
            continue
        if name.lower() in explained:
            continue
        unexplained.append(name)
    assert not unexplained, (
        f"declared but never imported and unexplained: {unexplained}. Either drop it, or add a "
        f"comment above it saying what needs it — `pyarrow` looked droppable and was not."
    )


# ------------------------------------------------ every action is SHA-pinned (#547)

#: `uses: owner/repo@ref`, ignoring local composite refs (`./.github/actions/...`), which are this
#: repo's own files and move with the commit being run. Quotes are stripped: a correctly-pinned
#: `uses: "owner/repo@<sha>"` would otherwise keep its trailing quote and read as floating.
_USES = re.compile(r"""^\s*(?:-\s*)?uses:\s*["']?(?!\./)([^"'\s]+)["']?(.*)$""", re.M)
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _action_files() -> list[Path]:
    """Every file that can carry a `uses:` — workflows **and composite actions**.

    The composites matter more than the workflows, not less: `deploy-app` is invoked by `deploy.yml`
    and `deploy-backfill-publish.yml`, both `[self-hosted, vps]`. A first draft of this scanned only
    `.github/workflows/`, and adding a floating `uses:` to `deploy-app/action.yml` left all four
    guards green — a third-party action running unpinned on the trading box, which is the exact
    thing this section exists to stop.
    """
    gh = ROOT / ".github"
    return sorted([*(gh / "workflows").glob("*.y*ml"), *gh.glob("actions/*/action.y*ml")])


def _action_uses() -> list[tuple[str, str, str]]:
    """(file, ref, trailing) for every third-party action this repo runs."""
    out: list[tuple[str, str, str]] = []
    for path in _action_files():
        for ref, trailing in _USES.findall(path.read_text()):
            out.append((path.name, ref, trailing))
    return out


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    """A tag is mutable. `actions/checkout@v7` is whatever `v7` points at *today*, and six of these
    run on `[self-hosted, vps]` — the trading box, with the runner's ambient Docker socket. A
    retagged action there executes on the machine holding the live tracker and its data.

    Before #547 exactly one of 22 was pinned, and it was the one on a *hosted* runner — the inverse
    of the risk ordering. This makes the rule total rather than a posture applied by hand: there is
    no per-action judgement to get wrong, and the box jobs are covered by construction.
    """
    floating = [
        f"{wf}: {ref}" for wf, ref, _ in _action_uses() if not _SHA.match(ref.split("@")[-1])
    ]
    assert not floating, (
        "these actions float on a mutable tag:\n  "
        + "\n  ".join(floating)
        + "\nPin to the commit SHA and keep a trailing `# vN` comment so dependabot still bumps it:"
        "\n  gh api repos/<owner>/<repo>/git/ref/tags/<tag>"
    )


def test_every_pin_keeps_the_version_comment_dependabot_reads() -> None:
    """The pin is only maintainable because dependabot rewrites *both* the SHA and the `# vN`
    comment beside it. Drop the comment and the pin silently stops being updated — which is worse
    than floating: it looks deliberate while quietly ageing into an unpatched action."""
    missing = [
        f"{wf}: {ref}"
        for wf, ref, trailing in _action_uses()
        # `v` followed by a digit — so a stray `# verified` can't stand in for a version.
        if not re.search(r"#\s*v[\d.]", trailing)
    ]
    assert not missing, (
        "these pins have no `# vN` comment, so dependabot will not bump them:\n  "
        + "\n  ".join(missing)
    )


def test_the_uses_scanner_sees_every_file_that_can_carry_one() -> None:
    """A regex that matched nothing would make every check above pass on an empty list.

    Compared as a **set against the files themselves**, not as a `len() >= N` floor. The floor was
    the first draft, and deleting two whole `uses:` lines from `pages.yml` left it green — the same
    objection `test_every_workflow_contributes_to_the_job_inventory` raises about that form.
    """
    uses = _action_uses()
    scanned = {wf for wf, _, _ in uses}
    expected = {
        p.name
        for p in _action_files()
        if re.search(r"""^\s*(?:-\s*)?uses:\s*["']?(?!\./)""", p.read_text(), re.M)
    }
    assert scanned == expected, "the scanner missed a file that carries a third-party `uses:`"
    assert scanned >= {"ci.yml", "deploy.yml", "harvest.yml", "build-image.yml"}
    # Composite action files are in scope (that is the gap #547's review found) even though none
    # carries a third-party `uses:` today — so assert the file is really being read, not just
    # globbed past.
    assert (ROOT / ".github" / "actions" / "deploy-app" / "action.yml").is_file()
    assert any(p.name == "action.yml" for p in _action_files())
    # Local `./` composite REFERENCES stay excluded — they move with the commit being run.
    assert "./.github/actions/" in (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert not any(ref.startswith("./") for _, ref, _ in uses)


def test_no_action_runs_on_the_box_unpinned() -> None:
    """States the risk ordering the original posture got backwards, so it is checked rather than
    remembered: whatever else is true, nothing floating may execute on the trading box.

    Strictly redundant while the check above is total — it cannot fail on its own — and kept
    anyway. The pre-#547 state was exactly one pin, on the *hosted* runner and none on the box, so
    the rule that was missing was never "pin things"; it was "the box is where it matters".
    """
    box_workflows = {wf for wf, _, runs_on, _ in _workflow_jobs() if "self-hosted" in runs_on}
    assert box_workflows, "no self-hosted jobs found — has the runner label changed?"
    unpinned_on_box = [
        f"{wf}: {ref}"
        for wf, ref, _ in _action_uses()
        if wf in box_workflows and not _SHA.match(ref.split("@")[-1])
    ]
    assert not unpinned_on_box, (
        f"floating actions in workflows that run on the box: {unpinned_on_box}"
    )


# ------------------------------------------------ the deploy path is linted too (#548)


def _all_workflow_text() -> str:
    wf = ROOT / ".github" / "workflows"
    return "\n".join(p.read_text() for p in sorted(wf.glob("*.y*ml")))


def test_make_check_still_mirrors_ci_after_adding_the_shell_lint() -> None:
    """CLAUDE.md's parity property — `make check` runs exactly what CI runs — is worth preserving,
    and it only holds if a new CI step gets a make target and vice versa."""
    mk = (ROOT / "Makefile").read_text()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    step = re.search(r"^\s*run:\s*make lint-sh\s*$", ci, re.M)
    assert step, (
        "CI must run `make lint-sh`, and run it *bare* — `make lint-sh || true` is the canonical "
        "way to neuter a gate while leaving it looking present, and a substring check accepts it"
    )
    assert re.search(r"^lint-sh:", mk, re.M), "`make lint-sh` is what CI calls"
    check = re.search(r"^check:([^\n]*)", mk, re.M)
    assert check and "lint-sh" in check.group(1), (
        "`make check` must include lint-sh, or the local gate stops predicting CI"
    )


# rglob over the whole tree, not three hardcoded directories: the first version of the guard below
# globbed `scripts/`, `deploy/` and `.claude/hooks/`, which just moved the staleness from the
# Makefile into the test. A script added under `deploy/scripts/` passed the guard AND
# `make lint-sh`.
_SKIP_DIRS = frozenset(
    {".venv", ".git", "node_modules", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
)
# `.claude/worktrees/` holds whole CHECKOUTS of this repo, so every script below reappears once per
# worktree and the guard fails for a reason that has nothing to do with the Makefile (#680). CI
# checks out clean and never has one, so this broke ONLY `make check` — the gate whose entire job is
# to predict CI. A local red that a push turns green teaches you to read past it, which is the same
# failure #530 fixed for coverage flags in `addopts`.
_WORKTREE_ROOT = (".claude", "worktrees")


def shell_scripts_on_disk(root: Path) -> set[str]:
    """Every `*.sh` under `root` that `make lint-sh` is expected to cover, as relative paths.

    The worktree exclusion is a path PREFIX rather than a bare directory name on purpose:
    `.claude/hooks/session-setup.sh` is a real `SHELL_FILES` entry and has to stay covered, and a
    directory merely *named* `worktrees` elsewhere in the tree must not be silently skipped.
    """
    found: set[str] = set()
    for path in root.rglob("*.sh"):
        rel = path.relative_to(root)
        if _SKIP_DIRS & set(rel.parts) or rel.parts[: len(_WORKTREE_ROOT)] == _WORKTREE_ROOT:
            continue
        found.add(str(rel))
    return found


def test_shell_script_discovery_ignores_nested_worktrees(tmp_path: Path) -> None:
    """A git worktree under `.claude/worktrees/` is a second checkout, not tree content (#680)."""

    def touch(*parts: str) -> None:
        p = tmp_path.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/usr/bin/env bash\n")

    touch("scripts", "real.sh")
    touch(".claude", "hooks", "session-setup.sh")  # a real SHELL_FILES entry — must stay covered
    touch("worktrees", "odd.sh")  # merely NAMED worktrees — must stay covered
    touch(".claude", "worktrees", "wt", "scripts", "real.sh")  # a checkout — must be ignored
    touch(".venv", "bin", "activate.sh")  # cache/tooling — must be ignored

    assert shell_scripts_on_disk(tmp_path) == {
        "scripts/real.sh",
        ".claude/hooks/session-setup.sh",
        "worktrees/odd.sh",
    }


def test_every_shell_script_in_the_tree_is_linted() -> None:
    """The Makefile lists the scripts rather than globbing them, so this is what stops the list
    going stale. A glob would have been worse, not better: `shellcheck $(wildcard scripts/*.sh)`
    with a typo'd path expands to nothing and exits 0 — a lint step that lints nothing, reported
    as green. Listing them makes the omission possible; this makes it fail.
    """
    mk = (ROOT / "Makefile").read_text()
    block = mk.split("SHELL_FILES :=")[1].split("\n\n")[0]
    listed = set(re.findall(r"[\w./-]+\.sh", block))
    on_disk = shell_scripts_on_disk(ROOT)
    assert on_disk, "no shell scripts found — has the layout changed?"
    assert listed == on_disk, (
        f"SHELL_FILES and the tree disagree. Only in the tree: {sorted(on_disk - listed)}; "
        f"only in the Makefile: {sorted(listed - on_disk)}"
    )


def test_the_self_hosted_label_is_declared_for_actionlint() -> None:
    """`vps` is a custom label actionlint cannot know, so without this every box job reports as a
    typo — eight identical non-findings, which is how a reader learns to skim past the real ones.
    It doubles as the only machine-readable statement of the runner topology."""
    cfg = (ROOT / ".github" / "actionlint.yaml").read_text()
    lines = [ln.strip() for ln in cfg.splitlines() if ln.strip().startswith("- ")]
    labels = {ln.lstrip("- ").strip() for ln in lines}
    assert "vps" in labels
    used = set(re.findall(r"runs-on:\s*\[self-hosted,\s*([\w-]+)\]", _all_workflow_text()))
    assert used, "no self-hosted jobs found — has the runner label changed?"
    assert used <= labels, (
        f"workflows use self-hosted labels actionlint isn't told about: {sorted(used - labels)}"
    )


def test_the_backfill_dispatch_builds_its_arguments_as_an_array() -> None:
    """SC2086 here was a real argument-injection, not lint noise (#548). `args="--date $IN_DATE"`
    expanded unquoted, and `IN_DATE` is a `workflow_dispatch` input — so `2026-01-01 --all` would
    have smuggled `--all` past the refusal three lines above it, dispatching the very rebuild that
    OOM-killed the box for 5h37m (#264). Quoting the string would break the two-word
    `--all --force` case; an array keeps each argument whole."""
    for name in ("backfill-dashboard.yml", "deploy-backfill-publish.yml"):
        w = (ROOT / ".github" / "workflows" / name).read_text()
        assert 'args="--date $IN_DATE"' not in w, f"{name}: the injectable string form is back"
        assert 'args=(--date "$IN_DATE")' in w, f"{name}: the date arg must be an array element"
        assert '${args[@]+"${args[@]}"}' in w, (
            f"{name}: expand the array with the empty-safe idiom box-job.sh uses"
        )


def test_no_free_text_input_is_interpolated_into_a_shell_script() -> None:
    """`${{ }}` is substituted into a `run:` block's script TEXT before bash parses it, so a `"`
    in the value closes the string and the rest executes — on the box, as the runner user, with
    the Docker socket (#548). Reaching the value through `env:` makes it a real environment
    variable that bash never re-parses.

    ⚠️ **This is the check actionlint cannot do for us.** actionlint 1.7 refuses to lint composite
    actions at all — `.github/actions/deploy-app/action.yml` is *the* deploy path, 121 lines, and
    it is invisible to `make lint-sh`. It was also the file that had the problem: `inputs.ref` and
    `inputs.image_tag` are free-text dispatch inputs and both went straight into `git checkout`
    and a tag assignment. The workflows already followed the env: rule; the composite action, the
    one file no linter can see, did not.

    `github.*` context values are excluded — `github.repository` is not attacker-controlled and is
    the conventional way to build an image reference.
    """
    offenders: list[str] = []
    for path in _lintable_yaml():
        text = path.read_text()
        for block in re.finditer(r"^(\s+)run:\s*\|?\s*\n((?:\1  .*\n|\s*\n)+)", text, re.M):
            for ref in re.findall(r"\$\{\{\s*([\w.-]+)", block.group(2)):
                if ref.startswith(("github.", "runner.", "job.", "strategy.", "matrix.")):
                    continue
                line = text.count("\n", 0, block.start(2)) + 1
                offenders.append(f"{path.name}:~{line}: ${{{{ {ref} }}}} inside a run: block")
    assert not offenders, (
        'pass these through `env:` and reference them as "$VAR" instead:\n  '
        + "\n  ".join(offenders)
    )


def _lintable_yaml() -> list[Path]:
    gh = ROOT / ".github"
    return sorted([*(gh / "workflows").glob("*.y*ml"), *gh.glob("actions/*/action.y*ml")])


def test_actionlint_really_runs_shellcheck_over_run_blocks() -> None:
    """The `PATH=` on the Makefile's actionlint line is load-bearing and fails *silently*.

    Without shellcheck on PATH, actionlint exits **0** on a workflow containing SC2086 and only
    whispers `Rule "shellcheck" was disabled` under `-verbose`. So a `VENV` rename, or a CI image
    that puts the binary elsewhere, would quietly downgrade this to workflow-syntax-only while
    every other guard here stayed green — and the `run:` blocks are where the box commands live.

    Two halves, because either alone is insufficient: the recipe must *set* the PATH, and
    actionlint must actually *use* it. Removing the PATH from the Makefile leaves `make lint-sh`
    exiting 0 on a workflow with SC2086 — verified by mutation — and a test that only invokes
    actionlint itself would stay green through exactly that.
    """
    recipe = (ROOT / "Makefile").read_text()
    lint_sh = recipe.split("lint-sh:")[1].split("\n\n")[0]
    assert "$(VENV)/bin" in lint_sh and "PATH=" in lint_sh, (
        "the actionlint line must put the venv on PATH, or actionlint finds no shellcheck and "
        "silently skips every `run:` block while still exiting 0"
    )
    actionlint = ROOT / ".venv" / "bin" / "actionlint"
    if not actionlint.exists():  # pragma: no cover - only when the venv isn't synced
        pytest.skip("actionlint not installed; `uv sync --locked --all-extras`")
    bad = (
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: |\n"
        "          x=$1\n"
        "          echo $x\n"
    )
    env = {**os.environ, "PATH": f"{ROOT / '.venv' / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(  # noqa: S603
        [str(actionlint), "-stdin-filename", "probe.yml", "-oneline", "-"],
        input=bad,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert result.returncode != 0, "actionlint accepted a workflow with an unquoted expansion"
    assert "shellcheck" in result.stdout.lower(), (
        "actionlint ran but did NOT invoke shellcheck on the run: block — the PATH the Makefile "
        f"sets is not reaching it. Output: {result.stdout[:300]}"
    )


def test_the_sc1090_suppression_is_load_bearing() -> None:
    """#548 asked for this to be fixed *or justified*. Justified: `scripts/backup.sh` sources a
    runtime-resolved path (`/etc/scs-backup.env`, root-only, absent on any dev machine), which
    shellcheck cannot follow and shouldn't guess at. It is also no longer a directive for a linter
    that never runs — deleting it now makes `make lint-sh` fail, which is how it should be.
    """
    src = (ROOT / "scripts" / "backup.sh").read_text()
    assert "# shellcheck disable=SC1090" in src
    assert 'ENV_FILE="${SCS_BACKUP_ENV:-/etc/scs-backup.env}"' in src, (
        "the suppression is justified by the path being runtime-resolved; if that changed, "
        "re-examine whether the disable is still needed"
    )


# ------------------------------------------------ concurrency comments stay true (#550)


def _concurrency() -> dict[str, tuple[str, bool]]:
    """`{workflow: (group, cancel_in_progress)}` for every workflow that declares one."""
    out: dict[str, tuple[str, bool]] = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text()
        block = re.search(r"^concurrency:\s*\n((?:[ \t#].*\n|\s*\n)+)", text, re.M)
        if not block:
            continue
        group = re.search(r"^\s+group:\s*(\S.*?)\s*$", block.group(1), re.M)
        cancel = re.search(r"^\s+cancel-in-progress:\s*(true|false)", block.group(1), re.M)
        if group:
            out[path.name] = (group.group(1), bool(cancel and cancel.group(1) == "true"))
    return out


def test_the_pipeline_shares_a_group_with_the_standalone_deploy_and_nothing_else() -> None:
    """`deploy-backfill-publish.yml`'s comment used to claim it serialised against the standalone
    deploy, backfill AND publish. It shares a group with the deploy only (#550) — what actually
    stops the other two overlapping is that there is exactly ONE self-hosted runner, executing one
    job at a time, so a `*/15` cron publish queues rather than races.

    The corrected comment now says that, and this keeps it true: if the groups are ever unified or
    split differently, the prose describing them has to move with them.
    """
    groups = _concurrency()
    assert groups["deploy.yml"][0] == "deploy-vps"
    assert groups["deploy-backfill-publish.yml"][0] == "deploy-vps", (
        "the pipeline must share the standalone deploy's group, or two deploys can interleave"
    )
    assert groups["backfill-dashboard.yml"][0] != "deploy-vps"
    assert groups["publish-dashboard.yml"][0] != "deploy-vps"


def test_no_workflow_sharing_a_group_cancels_in_progress_runs() -> None:
    """The trap behind "just give them all one group", written down so it can't be walked into.

    `publish-dashboard.yml` sets `cancel-in-progress: true` and runs on a `*/15` cron. Put it in a
    shared `box-vps` group with the deploy and a scheduled publish would **cancel an in-flight
    deploy** — mid-`docker compose up`, on the box. Cancelling is safe only while its group is its
    own, so the two settings have to be checked together rather than one at a time.
    """
    groups = _concurrency()
    by_group: dict[str, list[str]] = {}
    for name, (group, _) in groups.items():
        by_group.setdefault(group, []).append(name)
    offenders = [
        f"{name} (group {group!r}, shared with {sorted(set(by_group[group]) - {name})})"
        for name, (group, cancel) in groups.items()
        if cancel and len(by_group[group]) > 1
    ]
    assert not offenders, (
        "a workflow that cancels in-progress runs must not share its concurrency group — it would "
        "cancel the OTHER workflow's run, not just its own:\n  " + "\n  ".join(offenders)
    )


def test_the_concurrency_parser_reads_the_real_workflows() -> None:
    """A regex that matched nothing would make both checks above pass on an empty dict."""
    groups = _concurrency()
    assert len(groups) >= 8, f"only found {len(groups)} concurrency blocks"
    assert groups["publish-dashboard.yml"] == ("publish-dashboard", True)
    assert groups["deploy.yml"] == ("deploy-vps", False)


def test_no_workflow_still_claims_to_be_blocked_on_the_runner_issue() -> None:
    """#6 closed on 2026-06-29 and the runner has been live since. Two workflow headers went on
    saying "blocked on #6 … this workflow is inert" for five weeks while deploying routinely — the
    kind of stale prose that teaches a reader to distrust the comments that ARE load-bearing.

    Scoped to the *claim*, not the issue number: the corrections quote the old wording, and a
    check that banned the string would ban its own record.
    """
    for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text()
        assert "PREREQUISITE (blocked on #6" not in text, f"{path.name} still claims to be blocked"
        assert "this workflow is inert" not in text.replace(
            'header used to say "blocked on #6 … this workflow is inert"', ""
        ), f"{path.name} still claims to be inert"


# ------------------------------------------------ container hygiene (#549)


def _compose_services() -> dict[str, str]:
    """`{service: its yaml block}` from docker-compose.yml, hand-parsed.

    Per this module's standing convention: PyYAML is not a dependency of this project, and adding
    one solely for a test is what `_workflow_jobs` already declines to do. My first version
    imported it regardless — it happened to be a leftover in the local venv, and #546's lockfile is
    precisely what caught that CI installs only the recorded set. A good demonstration of the
    lockfile earning its keep on the PR after it landed.

    Stops at the top-level `volumes:` key: `scs-data` is indented exactly like a service, so a
    naive two-space split reports it as a third one.
    """
    text = (ROOT / "docker-compose.yml").read_text().split("\nvolumes:")[0]
    parts = re.split(r"^  ([\w-]+):$", text, flags=re.M)[1:]
    services = dict(zip(parts[::2], parts[1::2], strict=True))
    assert set(services) == {"ibgateway", "app"}, f"unexpected services: {sorted(services)}"
    return services


def test_both_services_cap_their_logs() -> None:
    """Unbounded `json-file` logs are not a generic worry here. The nightly harvest **aborts** when
    free disk drops below `harvest_min_disk_free_mb` (2 GB), so logs quietly eating the disk
    disables the harvest rather than filling it — and the app writes a JSON line per tick."""
    for name, block in _compose_services().items():
        assert "driver: json-file" in block, f"{name} has no logging driver pinned"
        assert re.search(r"max-size:\s*\d+m", block), f"{name} has no max-size"
        assert re.search(r'max-file:\s*"?\d+', block), f"{name} has no max-file"


def test_the_gateway_is_pinned_by_digest() -> None:
    """`:stable` is mutable and **nothing re-pulls it**: the deploy runs `docker compose pull app`
    — app only — and the systemd unit uses `--pull missing`, which fetches only when the image is
    absent. So the Gateway froze at its first pull and aged silently past every IBC fix, on a live
    trading box (#549).

    The pin was set to the digest the box was ALREADY running, so adding it changed nothing at
    runtime — the value is that dependabot's `docker` ecosystem now makes each Gateway update a
    reviewable commit, landing when someone chooses rather than on whatever restart pulls first.
    """
    compose = (ROOT / "docker-compose.yml").read_text()
    assert re.search(r"image:\s*ghcr\.io/gnzsnz/ib-gateway@sha256:[0-9a-f]{64}", compose), (
        "the Gateway must be digest-pinned; a mutable tag here is never re-pulled and so never "
        "updated, which is worse than drifting"
    )
    assert "ib-gateway:stable" not in compose.replace("# :stable", ""), "the mutable tag is back"


def test_dependabot_watches_every_ecosystem_the_repo_actually_uses() -> None:
    """Each of these was added because the previous set silently froze something: `uv` because the
    lock nothing bumps freezes by hash (#546), `docker` because nothing re-pulls the Gateway
    (#549). A pinned dependency with no bumper is worse than an unpinned one — it looks
    deliberate."""
    cfg = (ROOT / ".github" / "dependabot.yml").read_text()
    declared = set(re.findall(r"package-ecosystem:\s*(\S+)", cfg))
    assert {"pip", "uv", "github-actions", "docker"} <= declared, (
        f"dependabot is missing an ecosystem this repo depends on: {declared}"
    )


def test_the_app_image_reports_its_own_health() -> None:
    """The Gateway was health-gated and the app depending on it was not (#549) — compose could
    report `Up` for a tracker wedged mid-tick, and the deploy hand-rolled a metrics probe to tell.

    The probe must use python: `python:3.11-slim` ships neither curl nor wget, and adding one to
    the image for a healthcheck would be a package installed on every deploy to ask one question.
    The command itself is exercised against the real metrics server in
    `test_the_healthcheck_command_actually_probes_the_metrics_server`.
    """
    df = (ROOT / "Dockerfile").read_text()
    assert "HEALTHCHECK" in df, "the app image must report its own health"
    assert "9090/metrics" in df, "probe the metrics endpoint the app actually serves"
    assert not re.search(r"HEALTHCHECK[^\n]*\n?[^\n]*\b(curl|wget)\b", df), (
        "python:3.11-slim has neither curl nor wget; use the interpreter that is already there"
    )


def test_the_healthcheck_command_actually_probes_the_metrics_server() -> None:
    """Lifted verbatim from the Dockerfile and run for real, because a healthcheck that always
    passes is worse than none: it turns `docker compose ps` into a green light that means nothing.
    Both directions — no server must fail, a live server must pass."""
    import sys
    import time as clock

    from small_cap_stack.monitoring import start_metrics_server

    df = (ROOT / "Dockerfile").read_text()
    match = re.search(r'CMD python -c "([^"]+)"', df)
    assert match, "the HEALTHCHECK CMD is not in the shape this test reads"
    port = 9393
    cmd = match.group(1).replace("9090", str(port))

    dead = subprocess.run([sys.executable, "-c", cmd], capture_output=True)  # noqa: S603
    assert dead.returncode != 0, "the probe passed with nothing listening — it checks nothing"

    start_metrics_server(port)
    clock.sleep(0.5)
    live = subprocess.run([sys.executable, "-c", cmd], capture_output=True)  # noqa: S603
    assert live.returncode == 0, (
        f"the probe failed against a live metrics server: {live.stderr.decode()[-300:]}"
    )


def test_both_publishers_use_the_shared_action() -> None:
    """The publish block was BYTE-IDENTICAL in `publish-dashboard.yml` and
    `deploy-backfill-publish.yml` — `diff` produced no output at all — so a fix to one silently
    missed the other. That is not hypothetical: #545's memory guard had to be applied to the
    sibling backfill block twice for exactly this reason (#526).

    Both callers also need the sparse checkout, because a composite action resolves from the
    workspace. `publish-dashboard.yml` didn't check out at all before this; it costs ~1s on the
    box against a 36s job, measured, which is the only reason it is acceptable in a `*/15` cron.
    """
    for name in ("publish-dashboard.yml", "deploy-backfill-publish.yml"):
        w = (ROOT / ".github" / "workflows" / name).read_text()
        assert "uses: ./.github/actions/publish-dashboard" in w, name
        assert "sparse-checkout: .github/actions" in w, (
            f"{name} must check the action directory out, or the composite ref won't resolve"
        )
        # Comments excluded: both files describe the mechanism in their headers, and a check
        # that banned the words would ban the explanation of what the action does.
        code = "\n".join(ln for ln in w.splitlines() if not ln.lstrip().startswith("#"))
        assert "git push -f" not in code, f"{name} still inlines the publish; delegate it"
        assert "docker cp" not in code, f"{name} still inlines the extraction; delegate it"


def test_the_container_lookup_has_one_form() -> None:
    """Five variants of "find the app container" once existed, including a hardcoded
    `small-cap-stack-app-1` that would miss a renamed container (#526). #545 and #580 removed
    most; extracting the publish action removed the last two. Keep it that way — a job that picks
    the wrong container copies the wrong data onto a public branch.
    """
    hits = [
        f"{p.name}:{i}"
        for p in _action_files()
        for i, ln in enumerate(p.read_text().splitlines(), 1)
        if "small-cap-stack-app" in ln and not ln.lstrip().startswith("#")
    ]
    assert hits == ["action.yml:26"] or len(hits) == 1, (
        f"the app-container lookup should exist once, in the publish action; found: {hits}"
    )


def test_every_composite_action_step_declares_its_shell() -> None:
    """A composite action's `run:` step MUST carry `shell:` — GitHub fails the step at runtime
    without it, with "Required property is missing: shell".

    Nothing else catches this. actionlint 1.7 refuses to lint composite actions at all (#548), so
    `make lint-sh` passes a broken one — verified by mutation. And these are the deploy and publish
    paths, which only run on the box: the failure surfaces as a deploy dying mid-flight, not as a
    red PR.
    """
    for path in sorted((ROOT / ".github").glob("actions/*/action.y*ml")):
        lines = path.read_text().splitlines()
        for i, ln in enumerate(lines):
            if not re.match(r"^\s*run:\s*\|?\s*$", ln) and "run:" not in ln:
                continue
            if not re.match(r"^\s+run:", ln):
                continue
            # Walk the step this `run:` belongs to — back to its `- ` bullet, forward to the next.
            start = next(j for j in range(i, -1, -1) if lines[j].lstrip().startswith("- "))
            end = next(
                (j for j in range(i + 1, len(lines)) if lines[j].lstrip().startswith("- ")),
                len(lines),
            )
            step = "\n".join(lines[start:end])
            assert re.search(r"^\s+shell:\s*\S", step, re.M), (
                f"{path.parent.name}/action.yml step at line {start + 1} has `run:` without "
                "`shell:` — it will fail at runtime, on the box, and no linter here will say so"
            )


# --- test-file naming (#529) ---------------------------------------------------------------------

TESTS = ROOT / "tests"
BULLFLAG = ROOT / "src" / "small_cap_stack" / "bullflag"


def test_every_bullflag_module_has_a_matching_test_file() -> None:
    """`tests/test_bullflag_<name>.py` for `bullflag/<name>.py`, one to one, no exceptions.

    #529's complaint was that the prefix convention existed for exactly *one* module: `gates.py`
    and `score.py` got `test_bullflag_*`, while `day`, `features`, `cycles`, `segment` and `tokens`
    got unprefixed names — so `tests/test_gates.py` (the root `gates.py`) and
    `tests/test_bullflag_gates.py` sat next to each other and the reader had to know which was
    which. A half-applied convention is worse than none: it reads as meaning something.

    Measured before writing this: after the renames it holds with **zero** exceptions, which is why
    it is a flat assertion rather than a guard carrying a list of excuses. Keep it that way — a new
    `bullflag/` module gets its test file named to match, and a test file that outlives its module
    gets deleted (`test_setup.py` and `test_segment.py` went with #518).
    """
    modules = {p.stem for p in BULLFLAG.glob("*.py") if not p.stem.startswith("__")}
    tested = {p.stem[len("test_bullflag_") :] for p in TESTS.glob("test_bullflag_*.py")}
    assert modules, "no bullflag modules found — has the package moved?"
    assert not modules - tested, (
        f"{sorted(modules - tested)} are bullflag modules with no tests/test_bullflag_<name>.py"
    )
    assert not tested - modules, (
        f"{sorted(tested - modules)} are test_bullflag_* files naming no bullflag module — "
        "rename them, or delete them if the module is gone"
    )


def test_no_test_file_is_a_one_character_typo_of_another() -> None:
    """`test_report.py` (533-line `report.py`) and `test_reports.py` (278-line `reports.py`) sat one
    character apart, and #529 found them by reading. The first is now `test_eod_report.py` — that
    is the side with no inbound references, so nothing published had to be rewritten to fix it.

    Plural-vs-singular is the specific collision worth pinning; a general edit-distance check would
    flag legitimate neighbours like `test_bullflag_score` / `test_bullflag_gates`.
    """
    names = {p.stem for p in TESTS.glob("test_*.py")}
    collisions = sorted(n for n in names if n.endswith("s") and n[:-1] in names)
    assert not collisions, (
        f"{collisions} differ from another test module by a trailing 's', which is how "
        "test_report.py and test_reports.py were confused for each other"
    )
