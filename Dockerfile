# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    DUCKDB_PATH=/data/small_cap_stack.duckdb \
    JSON_LOGS=true \
    METRICS_PORT=9090

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
# BuildKit pip cache persists downloaded wheels across builds, so a source-only change
# doesn't re-download every dependency — the slow part of an on-box rebuild (#72).
RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip && pip install .

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 9090

# Deployed commit — baked by the deploy via --build-arg GIT_SHA (compose reads $GIT_SHA).
# Placed after the pip layer so a commit-only change doesn't invalidate the dependency install.
# The app reads DEPLOYED_COMMIT (config.deployed_commit) and surfaces it on the dashboard (#68).
ARG GIT_SHA=""
ENV DEPLOYED_COMMIT=$GIT_SHA

CMD ["python", "-m", "small_cap_stack"]
