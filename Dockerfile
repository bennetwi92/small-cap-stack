# syntax=docker/dockerfile:1
FROM python:3.11-slim

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
COPY pyproject.toml requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && pip install --require-hashes -r requirements.lock

# 2. The package itself installs with --no-deps (fast) and re-runs only when the source changes.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 9090

# Deployed commit — baked by the deploy via --build-arg GIT_SHA (compose reads $GIT_SHA).
# After the install layers so a commit-only change doesn't invalidate them.
ARG GIT_SHA=""
ENV DEPLOYED_COMMIT=$GIT_SHA

CMD ["python", "-m", "small_cap_stack"]
