"""Bot process entry point: ``python -m app.main``."""

from __future__ import annotations

import asyncio
import logging

from app.bot import run_bot
from app.database import init_db
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    init_db()
    await run_bot()


def cli() -> int:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        logger.info("Bot stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())