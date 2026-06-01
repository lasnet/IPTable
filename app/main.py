import asyncio
from contextlib import asynccontextmanager
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.services.auth import bootstrap_initial_admin
from app.services.i18n import html_language, make_translator, normalize_language
from app.services.inventory import normalize_project_address_rows
from app.services.ping import ensure_default_project_schedules, run_ping_scheduler
from app.services.security import csrf_input
from app.web.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    with SessionLocal() as db:
        bootstrap_initial_admin(db, settings)
        normalize_project_address_rows(db)
        ensure_default_project_schedules(db, settings)

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
    is_production = settings.app_env.lower() == "production"
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )
    session_secret = settings.secret_key or secrets.token_urlsafe(32)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        https_only=is_production,
        max_age=settings.session_idle_timeout_seconds,
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if is_production:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
                "img-src 'self' data:; script-src 'self'; style-src 'self'",
            )
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["_"] = make_translator(settings.interface_language)
    templates.env.globals["html_language"] = html_language(settings.interface_language)
    templates.env.globals["interface_language"] = normalize_language(settings.interface_language)
    templates.env.globals["csrf_input"] = csrf_input
    app.state.templates = templates
    app.include_router(router)
    app.include_router(api_router)
    return app


app = create_app()
