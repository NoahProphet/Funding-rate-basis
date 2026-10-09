"""Command line entry point: `basis-bot run` and `basis-bot check-config`."""

import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

import structlog

from basis_bot.clock import utc_now
from basis_bot.config import Config, ConfigError, load_config
from basis_bot.core import app
from basis_bot.core.run_id import new_run_id
from basis_bot.log import bind_run_id, configure_logging
from basis_bot.storage import db

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BAD_CONFIG = 2

log = structlog.get_logger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="basis-bot")
    parser.add_argument(
        "--config",
        type=Path,
        default=os.environ.get("BASIS_BOT_CONFIG"),
        help="path to config YAML (default: $BASIS_BOT_CONFIG)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the core process")
    sub.add_parser("check-config", help="validate config and print its hash")
    return parser


def check_config(path: Path) -> int:
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(f"config INVALID: {exc.path}", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        return EXIT_BAD_CONFIG
    print(f"config OK: {path}\nhash: {config.config_hash()}")
    return EXIT_OK


def run_core(path: Path, stop_requested: Callable[[], bool]) -> int:
    run_id = new_run_id(utc_now())
    configure_logging("INFO")
    bind_run_id(run_id)
    try:
        config = load_config(path)
    except ConfigError as exc:
        log.error("config_invalid", path=str(exc.path), errors=exc.errors)
        return EXIT_BAD_CONFIG
    configure_logging(config.logging.level)
    log.info("core_starting", config_path=str(path))
    return asyncio.run(_run_async(config, run_id, stop_requested))


async def _run_async(
    config: Config, run_id: str, stop_requested: Callable[[], bool]
) -> int:
    stop = asyncio.Event()
    app.install_stop_handlers(stop)
    # Checked after installing handlers so no signal falls in between.
    if stop_requested():
        stop.set()
    try:
        engine = db.create_engine(config.database)
    except OSError as exc:
        log.error("db_password_unreadable", error=str(exc))
        return EXIT_ERROR
    try:
        await app.run(config, engine, run_id, utc_now, stop)
    except db.SchemaMismatchError as exc:
        log.error("schema_mismatch", error=str(exc))
        return EXIT_ERROR
    except Exception:
        log.exception("core_crashed")
        return EXIT_ERROR
    finally:
        await engine.dispose()
    return EXIT_OK


def main(
    argv: list[str] | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
) -> int:
    args = _parser().parse_args(argv)
    if args.config is None:
        print(
            "no config: pass --config or set BASIS_BOT_CONFIG", file=sys.stderr
        )
        return EXIT_BAD_CONFIG
    if args.command == "check-config":
        return check_config(args.config)
    return run_core(args.config, stop_requested)
