import asyncio
import logging

from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.services.auth import bootstrap_initial_admin
from app.services.inventory import normalize_project_address_rows
from app.services.ping import ensure_default_project_schedules, run_ping_scheduler


logger = logging.getLogger(__name__)


async def worker_main() -> None:
    settings = get_settings()
    init_db()
    with SessionLocal() as db:
        bootstrap_initial_admin(db, settings)
        normalize_project_address_rows(db)
        ensure_default_project_schedules(db, settings)

    logger.info("Ping worker started")
    await run_ping_scheduler(settings)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(worker_main())


if __name__ == "__main__":
    main()
