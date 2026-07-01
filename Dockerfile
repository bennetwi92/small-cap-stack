# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    DUCKDB_PATH=/data/small_cap_stack.duckdb \
    JSON_LOGS=true \
    METRICS_PORT=9090

WORKDIR /app

# 1. Dependencies in their OWN layer — only re-runs when pyproject.toml changes, so a source-only
#    deploy skips the (slow) dependency reinstall (#72). Extract [project].dependencies to a
#    requirements file (tomllib is stdlib on 3.11); the BuildKit cache also avoids re-downloads.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && python -c "import tomllib, pathlib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; pathlib.Path('/tmp/requirements.txt').write_text(chr(10).join(deps))" \
 && pip install -r /tmp/requirements.txt

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
