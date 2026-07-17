"""Guards for deployment artifacts (#6): presence, key wiring, and no committed secrets."""

from __future__ import annotations

from pathlib import Path

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


def test_deploy_workflows_pull_and_never_build_on_box() -> None:
    """The box must never build (#278). Guards both the standalone deploy and the pipeline."""
    for name in ("deploy.yml", "deploy-backfill-publish.yml"):
        w = (ROOT / ".github" / "workflows" / name).read_text()
        assert "docker compose pull app" in w, name
        assert "docker compose up -d --no-build app" in w, name
        assert "docker compose up -d --build" not in w, name
        # The image lands via a racing workflow — deploying without waiting 404s (#278).
        assert "docker manifest inspect" in w, name


def test_build_image_covers_every_main_commit() -> None:
    """deploy resolves the image by commit SHA, so a path-filtered main build would strand
    commits with no image to deploy (#265's SHA was exactly that case, #278)."""
    w = (ROOT / ".github" / "workflows" / "build-image.yml").read_text()
    push = w.split("pull_request:")[0]
    assert "paths:" not in push, "main/tags builds must not be path-filtered"


def test_runbook_present() -> None:
    assert "Hetzner" in (ROOT / "deploy" / "RUNBOOK.md").read_text()


def test_env_example_has_no_committed_secrets() -> None:
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith(("TWS_USERID=", "TWS_PASSWORD=", "HEALTHCHECKS_PING_URL=")):
            key, _, value = line.partition("=")
            assert value.strip() == "", f"{key} must be an empty placeholder in .env.example"
