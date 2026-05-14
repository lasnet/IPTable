import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.database import init_db
from app.services.ping import run_ping_scheduler
from app.web.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()

    ping_task: asyncio.Task | None = None
    if settings.enable_ping_worker:
        ping_task = asyncio.create_task(run_ping_scheduler(settings))

    yield

    if ping_task:
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.state.templates = Jinja2Templates(directory="app/templates")
    app.include_router(router)
    return app


app = create_app()
