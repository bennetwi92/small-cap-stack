"""What `scripts/harvest.sh` actually hands the container (#431).

This file exists because the alternative was a note in the RUNBOOK saying "check this by hand on
the box before the first night" — and the box needs SSH, which needs a laptop. Everything here runs
the real script with ``HARVEST_DRY_RUN=1`` against a temp ``.env``, so it is verified in CI on any
machine, with no Docker daemon and no box.

Two failures are worth this much apparatus, because both are silent:

- **`DATA_DIR`.** The box's ``.env`` is a copy of ``.env.example``, which carries the LOCAL-dev
  ``DATA_DIR=./data``. Inherited by the container it overrides the image's ``/data``, so a night's
  harvest lands inside the container's working directory and is deleted with it on ``--rm``.
  Exit code 0, empty store, ~218 API calls gone.
- **Comment and quote parsing.** ``docker run --env-file`` is NOT compose's parser: no inline
  comments, no quote stripping. The box's ``.env`` is written for compose and is full of both. That
  is why the script reads the file itself rather than delegating.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "harvest.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")

# A realistic box .env: compose-style inline comments, a quoted value, the local-dev DATA_DIR, and
# broker credentials that have no business inside the harvest container.
BOX_ENV = """\
# small-cap-stack runtime config
IMAGE_TAG=sha-abc1234
IBKR_TRADING_MODE=paper # paper | live
DATA_DIR=./data
DUCKDB_PATH=./data/small_cap_stack.duckdb
TWS_USERID=someuser
TWS_PASSWORD=hunter2
FMP_API_KEY=fmpsecret
HEALTHCHECKS_PING_URL=https://hc-ping.com/abc-123
MASSIVE_API_KEY=vendorkey
HARVEST_MIN_DAY_VOLUME=500000   # tightened after the sweep
RECON_CHARTS_MAX_DATES=12
SCAN_MAX_PRICE=50.0
TZ="America/New_York"
JSON_LOGS=true
"""


def _dry_run(
    tmp_path: Path, env_text: str = BOX_ENV, command: str = "run", **env: str
) -> list[str]:
    """Run the script in dry-run mode and return the docker argv it would have executed.

    Defaults to `run` — the vendor-spending path the timer actually takes nightly, and the one
    every limit asserted below exists for. Read-only commands deliberately take a different shape
    (#455: no name lock, no cgroup slice, a smaller cap), so a helper that defaulted to `status`
    would have quietly stopped testing the container the box really launches."""
    env_file = tmp_path / ".env"
    env_file.write_text(env_text)
    proc = subprocess.run(
        ["bash", str(SCRIPT), command],
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HARVEST_DRY_RUN": "1",
            "ENV_FILE": str(env_file),
            **env,
        },
    )
    return proc.stdout.splitlines()


def _env_flags(argv: list[str]) -> dict[str, str]:
    """The `-e KEY=VALUE` pairs, in order — later wins, exactly as docker resolves them."""
    out: dict[str, str] = {}
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "-e":
            key, _, val = value.partition("=")
            out[key] = val
    return out


def test_container_data_paths_are_never_inherited_from_the_host(tmp_path: Path) -> None:
    """The one that would have cost a night: `.env`'s local-dev DATA_DIR must not reach the
    container, or the harvest writes into a filesystem that dies with `--rm`."""
    env = _env_flags(_dry_run(tmp_path))
    assert env["DATA_DIR"] == "/data"
    assert env["DUCKDB_PATH"] == "/data/small_cap_stack.duckdb"


def test_broker_credentials_stay_out_of_the_harvest_container(tmp_path: Path) -> None:
    """The harvest talks to one HTTP vendor. It has no use for the IBKR login, and a background job
    that can't leak what it never held is strictly better than one trusted not to."""
    argv = _dry_run(tmp_path)
    env = _env_flags(argv)
    for secret in ("TWS_USERID", "TWS_PASSWORD", "FMP_API_KEY"):
        assert secret not in env
    assert "hunter2" not in " ".join(argv)


def test_the_tracker_dead_mans_switch_is_blanked(tmp_path: Path) -> None:
    """A stalled harvest must never page as a tracker outage (#431)."""
    env = _env_flags(_dry_run(tmp_path))
    assert env["HEALTHCHECKS_PING_URL"] == ""


def test_inline_comments_and_quotes_are_stripped_the_way_compose_does(tmp_path: Path) -> None:
    """`docker run --env-file` would pass `500000   # tightened after the sweep` through verbatim,
    and pydantic would reject it — after the run had already started."""
    env = _env_flags(_dry_run(tmp_path))
    assert env["HARVEST_MIN_DAY_VOLUME"] == "500000"
    assert env["TZ"] == "America/New_York"  # quotes stripped, not passed as part of the value
    assert env["SCAN_MAX_PRICE"] == "50.0"


def test_harvest_knobs_and_scan_gates_reach_the_container(tmp_path: Path) -> None:
    """SCAN_* is passed on purpose: the reconstruction applies the live scanner's gates, so a
    retuned tracker universe must retune the harvest's too rather than silently diverging."""
    env = _env_flags(_dry_run(tmp_path))
    assert env["HARVEST_MIN_DAY_VOLUME"] == "500000"
    assert env["SCAN_MAX_PRICE"] == "50.0"
    assert env["JSON_LOGS"] == "true"
    # RECON_* as a family, not RECON_SUBDIR alone (#488): `charts` reads RECON_CHARTS_MAX_DATES, and
    # a box that tuned the publish budget in .env must not have the container silently fall back to
    # the default and publish a different window than the operator asked for.
    assert env["RECON_CHARTS_MAX_DATES"] == "12"


def test_the_deployed_image_tag_is_pinned_not_latest(tmp_path: Path) -> None:
    """The harvest must run the code that is deployed, not drift to :latest behind the tracker."""
    argv = _dry_run(tmp_path)
    assert "ghcr.io/bennetwi92/small-cap-stack:sha-abc1234" in argv
    # ...and with no IMAGE_TAG stored, :latest is the documented fallback rather than an error.
    bare = _dry_run(tmp_path, env_text="MASSIVE_API_KEY=k\n")
    assert "ghcr.io/bennetwi92/small-cap-stack:latest" in bare


def test_an_ambient_key_wins_over_the_stored_one(tmp_path: Path) -> None:
    """This is what lets the workflow inject `secrets.MASSIVE_API_KEY` into a box that has never
    had the key written to disk — no SSH, no laptop. Exactly one -e must survive, or whichever
    docker saw last would silently decide which key is live."""
    argv = _dry_run(tmp_path, MASSIVE_API_KEY="from-actions")
    assert _env_flags(argv)["MASSIVE_API_KEY"] == "from-actions"
    assert argv.count("MASSIVE_API_KEY=from-actions") == 1
    assert "MASSIVE_API_KEY=vendorkey" not in argv
    # ...and with nothing ambient, the stored one is used.
    assert _env_flags(_dry_run(tmp_path))["MASSIVE_API_KEY"] == "vendorkey"


def test_a_missing_env_file_still_produces_a_runnable_command(tmp_path: Path) -> None:
    """A fresh box, or a restored one, must not have the harvest fail on a parse step."""
    proc = subprocess.run(
        ["bash", str(SCRIPT), "status"],
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HARVEST_DRY_RUN": "1",
            "ENV_FILE": str(tmp_path / "nope.env"),
            "MASSIVE_API_KEY": "k",
        },
    )
    argv = proc.stdout.splitlines()
    assert argv[:3] == ["docker", "run", "--rm"]
    assert _env_flags(argv)["DATA_DIR"] == "/data"


def test_the_memory_cap_is_always_present_and_swap_is_pinned_to_it(tmp_path: Path) -> None:
    """memory-swap is the COMBINED limit, so equal values mean no swap at all — swapping a
    background job is how the CX23 thrashes past sshd (#264)."""
    argv = _dry_run(tmp_path)
    assert "--memory=1g" in argv and "--memory-swap=1g" in argv
    tuned = _dry_run(tmp_path, HARVEST_MEM_LIMIT="512m")
    assert "--memory=512m" in tuned and "--memory-swap=512m" in tuned


# ------------------------------------------------------------------------------------------------
# install-key: placing the vendor key without SSH
# ------------------------------------------------------------------------------------------------


def _install_key(tmp_path: Path, key: str, env_text: str | None = None) -> tuple[int, str, str]:
    env_file = tmp_path / ".env"
    if env_text is not None:
        env_file.write_text(env_text)
    proc = subprocess.run(
        ["bash", str(SCRIPT), "install-key"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "ENV_FILE": str(env_file), "MASSIVE_API_KEY": key},
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_install_key_writes_the_key_without_ever_printing_it(tmp_path: Path) -> None:
    code, out, _ = _install_key(tmp_path, "supersecretvalue", env_text=BOX_ENV)
    assert code == 0
    assert "supersecretvalue" not in out  # a workflow log is not a place for the key
    assert "16 chars" in out and "alue" in out  # enough to tell "landed" from "landed truncated"
    lines = (tmp_path / ".env").read_text().splitlines()
    assert "MASSIVE_API_KEY=supersecretvalue" in lines
    assert "TWS_PASSWORD=hunter2" in lines  # the rest of the file is untouched


def test_install_key_replaces_rather_than_appends_so_a_rotation_takes(tmp_path: Path) -> None:
    """Appending would leave the old key in the file, and whichever `-e` docker saw last would
    quietly decide which one is live."""
    _install_key(tmp_path, "oldkey", env_text=BOX_ENV)
    _install_key(tmp_path, "newkey")
    body = (tmp_path / ".env").read_text()
    assert body.count("MASSIVE_API_KEY=") == 1
    assert "MASSIVE_API_KEY=newkey" in body
    assert "oldkey" not in body


def test_install_key_refuses_an_empty_secret_with_a_useful_message(tmp_path: Path) -> None:
    """The likeliest failure by far: the workflow ran before the repo secret was added. It must say
    so, not write an empty line and let the harvest fail 13 seconds into a night."""
    code, _, err = _install_key(tmp_path, "", env_text=BOX_ENV)
    assert code == 2
    assert "Actions secret" in err
    assert "MASSIVE_API_KEY=" not in (tmp_path / ".env").read_text().replace(
        "MASSIVE_API_KEY=vendorkey", ""
    )


def test_install_key_locks_down_the_env_file(tmp_path: Path) -> None:
    _install_key(tmp_path, "k", env_text=BOX_ENV)
    assert oct((tmp_path / ".env").stat().st_mode)[-3:] == "600"


def test_install_units_installs_from_this_checkout(tmp_path: Path) -> None:
    """The workflow runs the CHECKED-OUT script, so dispatching a ref installs that ref's units —
    and both bootstrap commands work before the change is merged and deployed, which is the whole
    reason they exist (the alternative is SSH)."""
    systemd = tmp_path / "systemd"
    systemd.mkdir()
    proc = subprocess.run(
        ["bash", str(SCRIPT), "install-units"],
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "HARVEST_DRY_RUN": "1",  # stop before systemctl; the copy is what's under test
            "SYSTEMD_DIR": str(systemd),
        },
    )
    installed = sorted(p.name for p in systemd.iterdir())
    # The slices carry the limits that actually reach a container (#452), so a bootstrap that
    # installed the service and timer without them would enable a harvest with no cap at all.
    # `scs-jobs.slice` rides along (#545) so the on-demand backfill/export envelope can be
    # installed from the phone path too, rather than needing SSH.
    assert installed == [
        "scs-harvest.service",
        "scs-harvest.slice",
        "scs-harvest.timer",
        "scs-jobs.slice",
    ]
    assert "OnCalendar" in (systemd / "scs-harvest.timer").read_text()
    assert "not touching systemd" in proc.stdout
    # Idempotent: re-running is how a changed unit is rolled out, so it must not fail on existing.
    subprocess.run(
        ["bash", str(SCRIPT), "install-units"],
        check=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "HARVEST_DRY_RUN": "1", "SYSTEMD_DIR": str(systemd)},
    )


# ================================================================================================
# The concurrency lock, exercised rather than grepped (#455)
# ================================================================================================
#
# The first version of these assertions grepped the shell source for three substrings. That cannot
# catch the bug it was written for: the lock was keyed on `$1`, so `harvest.sh --limit 1 run` — a
# perfectly valid invocation, since argparse takes options before the positional — spent vendor
# budget with no lock, no name and no stale sweep, racing the timer's own harvest. A PATH stub for
# `docker` lets the real code path run with no daemon.


def _docker_stub(tmp_path: Path, *, state: str) -> dict[str, str]:
    """A fake `docker` on PATH. ``state`` is what `docker inspect` reports for scs-harvest:
    "true" (running), "false" (a leaked corpse), or "" (absent — inspect fails, as the real one
    does). Every invocation is appended to a log so the test can assert what was called."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    inspect = f'printf "%s\\n" "{state}"; exit 0' if state else "exit 1"
    (bin_dir / "docker").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "inspect" ]; then\n'
        f"  {inspect}\n"
        "fi\n"
        'if [ "$1" = "run" ]; then echo "RAN"; fi\n'
        "exit 0\n"
    )
    (bin_dir / "docker").chmod(0o755)
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "ENV_FILE": str(tmp_path / "no-such-env"),
        "MASSIVE_API_KEY": "k",
        "DOCKER_LOG": str(log),
    }


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )


def test_a_second_spending_run_refuses_while_one_is_live() -> None:
    """Exit NON-ZERO. The timer never reaches this branch — systemd merges a duplicate start job
    into the running one — so the only callers are a human and the `harvest` workflow, and for them
    "a harvest was already running so I did nothing" must render as a red run, not a green tick."""
    with tempfile.TemporaryDirectory() as td:
        env = _docker_stub(Path(td), state="true")
        proc = _run(env, "run", "--limit", "1")
    assert proc.returncode != 0
    assert "already running" in proc.stderr
    assert "RAN" not in proc.stdout, "it started a second harvest anyway"


def test_the_lock_is_taken_however_the_arguments_are_ordered() -> None:
    """argparse accepts `--limit 1 run`, so a lock keyed on $1 alone let a full phase-2 run spend
    budget unlocked, beside the timer's own harvest."""
    for args in (("run", "--limit", "1"), ("--limit", "1", "run"), ("auto",), ("daily",)):
        with tempfile.TemporaryDirectory() as td:
            env = _docker_stub(Path(td), state="true")
            proc = _run(env, *args)
        assert proc.returncode != 0, f"{args} bypassed the lock"
        assert "already running" in proc.stderr, f"{args} bypassed the lock"


def test_read_only_commands_run_beside_a_live_harvest_and_stay_out_of_its_slice() -> None:
    """They spend nothing and race on nothing, so being unable to check `status` during a 14-hour
    run is pure cost. But they must NOT join the harvest's cgroup slice: its MemoryMax is only
    200 MB above the harvest's own limit and it has MemorySwapMax=0, so a `sweep` beside a 900 MB
    harvest could push the slice over and have the kernel kill the night it came to look at."""
    for cmd in ("status", "sweep", "prefilter"):
        with tempfile.TemporaryDirectory() as td:
            env = _docker_stub(Path(td), state="true")
            proc = _run(env, cmd)
            called = Path(env["DOCKER_LOG"]).read_text()
        assert proc.returncode == 0, f"{cmd} was blocked by a lock it should not take"
        assert "RAN" in proc.stdout
        assert "--name scs-harvest" not in called, f"{cmd} took the lock"
        assert "--cgroup-parent" not in called, f"{cmd} joined the harvest's slice"
        assert "--memory=512m" in called, f"{cmd} took the harvest's full 1g cap"


def test_charts_takes_the_lock_and_the_harvest_slice(tmp_path: Path) -> None:
    """`charts` (#488) spends no vendor budget, but it read-modify-writes recon_index.json and so
    does the per-session publish hook inside a running `run`/`auto`. Interleaved, one of them writes
    an index built from a stale snapshot — a dropped row and an orphaned payload. The container name
    is the only cross-process mutex on the box, so this one is NOT a read-only command.

    It also does real work (DuckDB + polars + the detector, per date), which is why it belongs in
    the memory slice rather than in the 512 MB no-slice envelope `status`/`sweep`/`prefilter` use.
    """
    with tempfile.TemporaryDirectory() as td:
        env = _docker_stub(Path(td), state="true")
        proc = _run(env, "charts")
    assert proc.returncode != 0, "charts ran beside a live harvest and could corrupt the index"
    assert "already running" in proc.stderr

    argv = _dry_run(tmp_path, command="charts")
    assert "--cgroup-parent=scs-harvest.slice" in argv
    assert "--memory=1g" in argv
    assert "--name" in argv and "scs-harvest" in argv


def test_a_leaked_container_is_cleared_rather_than_blocking_every_future_night() -> None:
    """`--rm` is server-side, so a daemon restart mid-harvest can leave an Exited container holding
    the name — after which every night exits 125 in under a second, with no Restart= and no
    Healthchecks ping to notice. Only a NON-running container is removed, so the lock survives."""
    with tempfile.TemporaryDirectory() as td:
        env = _docker_stub(Path(td), state="false")
        proc = _run(env, "run")
        called = Path(env["DOCKER_LOG"]).read_text()
    assert proc.returncode == 0
    assert "rm scs-harvest" in called
    assert "RAN" in proc.stdout, "it cleared the corpse but never started the harvest"
