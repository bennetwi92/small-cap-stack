"""Export a slice of the tracker's ``/data`` store for offline analysis from the phone.

Runs ON THE BOX inside the app container — the same ``docker exec -i ... python -`` recipe as
``probe_run.py``. The ``data-export.yml`` workflow pipes this in on the self-hosted ``vps`` runner,
then commits the produced file to the ``data-export`` branch. A Claude Code web/mobile session can
then pull it back over GitHub and analyze it *without* SSH or any secret in the cloud (direct SSH
into the box is impossible from a web session — HTTP-only proxy, no secret store; see the
``box-data`` skill).

    docker exec -i -e SCS_DATASET -e SCS_START -e SCS_END -e SCS_SYMBOLS \\
        -e SCS_FORMAT -e SCS_OUT small-cap-stack-app-1 python - \\
        < scripts/analysis/export_query.py

Env (all optional unless noted):
  SCS_DATASET   one of: bars, opportunities, scanner_hits, news, fundamentals, analysis.
                Mutually exclusive with SCS_QUERY; one of the two is required.
  SCS_QUERY     raw DuckDB SQL against the dataset views (see storage.Store.query). Overrides
                SCS_DATASET. Use for joins / aggregates the dataset+filter form can't express.
  SCS_START     inclusive dt lower bound, YYYY-MM-DD (dataset mode only).
  SCS_END       inclusive dt upper bound, YYYY-MM-DD (dataset mode only).
  SCS_SYMBOLS   comma-separated symbols to keep (dataset mode, symbol-keyed datasets only).
  SCS_FORMAT    parquet | csv | ndjson  (default parquet — compressed; prefer it for wide ranges).
  SCS_OUT       output file path (default /data/exports/export.<ext>).
  SCS_DATA_DIR  store root (default /data) — override to run against a fixture dir offline.
"""

import os
from datetime import date
from pathlib import Path

from small_cap_stack.storage import Store

DATASETS = ("bars", "opportunities", "scanner_hits", "news", "fundamentals", "analysis")
EXT = {"parquet": "parquet", "csv": "csv", "ndjson": "ndjson"}


def _sql_str(value: str) -> str:
    """Single-quote a literal for inlining into DuckDB SQL (doubling embedded quotes)."""
    return "'" + value.replace("'", "''") + "'"


def _valid_date(value: str, field: str) -> None:
    """Fail with a readable message (not a traceback) when a date bound doesn't parse."""
    try:
        date.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"{field}={value!r} is not an ISO date (YYYY-MM-DD)") from None


def build_sql(dataset: str, start: str, end: str, symbols: str) -> str:
    """Build ``SELECT * FROM <dataset>`` with optional dt-range and symbol filters.

    ``dt`` is the Hive partition column (ISO ``YYYY-MM-DD`` strings), so lexical comparison is
    also chronological — we compare as strings and validate the bounds parse as real dates.
    """
    if dataset not in DATASETS:
        raise SystemExit(f"unknown SCS_DATASET={dataset!r}; choose from {list(DATASETS)}")
    clauses = []
    if start:
        _valid_date(start, "SCS_START")
        clauses.append(f"dt >= {_sql_str(start)}")
    if end:
        _valid_date(end, "SCS_END")
        clauses.append(f"dt <= {_sql_str(end)}")
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if syms:
        clauses.append(f"symbol IN ({', '.join(_sql_str(s) for s in syms)})")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return f'SELECT * FROM "{dataset}"{where}'


def main() -> None:
    fmt = (os.environ.get("SCS_FORMAT") or "parquet").lower()
    if fmt not in EXT:
        raise SystemExit(f"unknown SCS_FORMAT={fmt!r}; choose from {list(EXT)}")

    query = (os.environ.get("SCS_QUERY") or "").strip()
    if query:
        sql = query
    else:
        dataset = (os.environ.get("SCS_DATASET") or "").strip()
        if not dataset:
            raise SystemExit("set SCS_DATASET or SCS_QUERY")
        sql = build_sql(
            dataset,
            (os.environ.get("SCS_START") or "").strip(),
            (os.environ.get("SCS_END") or "").strip(),
            (os.environ.get("SCS_SYMBOLS") or "").strip(),
        )

    out = Path(os.environ.get("SCS_OUT") or f"/data/exports/export.{EXT[fmt]}")
    out.parent.mkdir(parents=True, exist_ok=True)

    df = Store(Path(os.environ.get("SCS_DATA_DIR") or "/data")).query(sql)
    if fmt == "parquet":
        df.write_parquet(out)
    elif fmt == "csv":
        df.write_csv(out)
    else:
        df.write_ndjson(out)

    # stdout is captured into the workflow's job summary — keep it to metadata (no binary).
    print(f"sql={sql}")
    print(f"rows={df.height} cols={df.width}")
    print(f"schema={dict(df.schema)}")
    print(f"out={out}")


if __name__ == "__main__":
    main()
