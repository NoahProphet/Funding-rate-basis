"""Process entry point.

Deliberately import-light: stop signals are trapped before the heavy
imports in `cli`, which take long enough that a quick `docker compose
down` would otherwise kill the process uncleanly with the default handler.
"""

import signal
import sys
from types import FrameType


def main() -> int:
    early: list[int] = []

    def remember(signum: int, frame: FrameType | None) -> None:
        early.append(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, remember)

    from basis_bot.core import cli

    return cli.main(stop_requested=lambda: bool(early))


if __name__ == "__main__":
    sys.exit(main())
