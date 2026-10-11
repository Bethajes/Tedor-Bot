"""Entry point that runs the Telegram bot and the FastAPI REST server together.

Both processes are independently runnable:

* ``python run.py``            -> bot polling + REST API in one process
* ``python -m app.main``       -> bot polling only
* ``uvicorn app.api.server:app`` -> REST API only
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.api.server import create_app
from app.bot import run_bot
from app.config import settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


async def _serve_api() -> None:
    import uvicorn

    config = uvicorn.Config(
        create_app(),
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=True,
    )
    server = uvicorn.Server(config)
    logger.info("Starting REST API on %s:%s", settings.api_host, settings.api_port)
    await server.serve()


async def _run_bot() -> None:
    await run_bot()


async def amain() -> None:
    configure_logging()
    logger.info("Booting Tedor Tutors system")
    bot_task = asyncio.create_task(_run_bot(), name="bot")
    api_task = asyncio.create_task(_serve_api(), name="api")
    try:
        await asyncio.gather(bot_task, api_task)
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        raise
    finally:
        for task in (bot_task, api_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(bot_task, api_task, return_exceptions=True)


def main() -> int:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        logger.info("Shutting down")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())