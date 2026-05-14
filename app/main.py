import asyncio
from contextlib import asynccontextmanager
import secrets

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.services.auth import bootstrap_initial_admin
from app.services.inventory import normalize_project_address_rows
from app.services.ping import run_ping_scheduler
from app.web.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    with SessionLocal() as db:
        bootstrap_initial_admin(db, settings)
        normalize_project_address_rows(db)

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
    session_secret = settings.secret_key or secrets.token_urlsafe(32)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        https_only=settings.app_env == "production",
    )
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.state.templates = Jinja2Templates(directory="app/templates")
    app.include_router(router)
    return app


app = create_app()
