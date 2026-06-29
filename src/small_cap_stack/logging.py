"""Structured logging via structlog, bridged to stdlib (JSON in prod, console in dev)."""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.stdlib import BoundLogger
from structlog.typing import Processor


def configure_logging(*, level: str = "INFO", json_logs: bool = False) -> None:
    """Configure structlog + stdlib once at startup."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    # APScheduler is chatty at INFO; keep our structured logs clean.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> BoundLogger:
    """Return a bound structlog logger."""
    logger: BoundLogger = structlog.get_logger(name)
    return logger
