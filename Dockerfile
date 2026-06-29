FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    DUCKDB_PATH=/data/small_cap_stack.duckdb \
    JSON_LOGS=true \
    METRICS_PORT=9090

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 9090

CMD ["python", "-m", "small_cap_stack"]
