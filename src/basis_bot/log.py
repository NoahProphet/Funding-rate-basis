"""Structured JSON logging to stdout, with UTC timestamps and run_id."""

import json
import logging
import sys
from typing import Any, TextIO

import structlog


def _json_dumps(obj: Any, **kwargs: Any) -> str:
    # Decimal, datetime and Path render as str(), never as lossy floats or
    # structlog's repr() fallback.
    kwargs["default"] = str
    return json.dumps(obj, **kwargs)


def _shared_processors() -> list[structlog.typing.Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def configure_logging(level: str, stream: TextIO = sys.stdout) -> None:
    """Route structlog and stdlib logging (sqlalchemy, alembic) to JSON."""
    renderer = structlog.processors.JSONRenderer(serializer=_json_dumps)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_shared_processors(),
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def bind_run_id(run_id: str) -> None:
    structlog.contextvars.bind_contextvars(run_id=run_id)
