# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    DUCKDB_PATH=/data/small_cap_stack.duckdb \
    JSON_LOGS=true \
    METRICS_PORT=9090

WORKDIR /app

# 1. Dependencies in their OWN layer — only re-runs when the lock changes, so a source-only deploy
#    skips the (slow) dependency reinstall (#72). The BuildKit cache also avoids re-downloads.
#
#    Installs from `requirements.lock`, NOT from pyproject's ranges (#546). Every runtime dep was
#    declared `>=` with no upper bound, and this layer resolved independently of CI on whatever day
#    it was built — so two builds of the SAME commit could bake different polars/duckdb, and "the
#    box disagrees with my Mac" was not a diagnosable statement. `--require-hashes` makes the
#    install refuse anything the lock didn't record, which also closes the mid-build swap.
#
#    `requirements.lock` is generated from `uv.lock` — see the Makefile's `lock` target. Editing it
#    by hand is pointless: `tests/test_deployment.py` fails when it drifts from `uv.lock`.
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && pip install --require-hashes -r requirements.lock

# 2. The package itself installs with --no-deps (fast) and re-runs only when the source changes.
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 9090

# The Gateway is health-gated and the app that depends on it was not (#549): compose could report
# `Up` for a tracker wedged mid-tick, and the deploy had to hand-roll a metrics probe to tell.
# `/metrics` is served by the app's own metrics server, so a response means the process is
# answering rather than merely running.
#
# Uses python rather than curl/wget: python:3.11-slim ships neither, and adding one to the image
# for a healthcheck would be a package installed on every deploy to ask one question.
#
# NOTE this reports health; it does not restore it. `restart: unless-stopped` acts on exit, not on
# unhealthy — plain compose has no restart-on-unhealthy. The dead-man's switch (Healthchecks.io,
# monitoring.py) remains the thing that notices a tracker that has stopped ticking.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/metrics', timeout=4)" || exit 1

# Deployed commit — baked by the deploy via --build-arg GIT_SHA (compose reads $GIT_SHA).
# After the install layers so a commit-only change doesn't invalidate them.
ARG GIT_SHA=""
ENV DEPLOYED_COMMIT=$GIT_SHA

CMD ["python", "-m", "small_cap_stack"]
