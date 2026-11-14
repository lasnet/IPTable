import asyncio
import logging

from app.core.config import get_settings
from app.core.database import engine, init_db
from app.services.database_roles import validate_runtime_role
from app.services.ping import run_ping_scheduler


logger = logging.getLogger(__name__)


async def worker_main() -> None:
    settings = get_settings()
    init_db()
    if settings.app_env == "production":
        validate_runtime_role(engine)
    logger.info("Ping worker started")
    await run_ping_scheduler(settings)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(worker_main())


if __name__ == "__main__":
    main()
