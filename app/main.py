import asyncio
from contextlib import asynccontextmanager
import secrets
import anyio

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse

from app.api.routes import api_router
from app.core.config import _request_settings, get_settings
from app.core.database import SessionLocal, init_db
from app.services.auth import bootstrap_initial_admin
from app.services.i18n import html_language, make_translator, normalize_language
from app.services.inventory import normalize_project_address_rows
from app.services.ping import ensure_default_project_schedules, run_ping_scheduler
from app.services.security import csrf_input
from app.services.settings import current
from app.web.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    with SessionLocal() as db:
        bootstrap_initial_admin(db, settings)
        normalize_project_address_rows(db)
        ensure_default_project_schedules(db, current(db, settings))
    app.state.config_ready = True

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


class RuntimeSessionMiddleware:
    def __init__(self, app, *, secret_key: str, session_cookie: str, https_only: bool,
                 default_max_age: int, same_site: str = "lax"):
        self.app = app
        self.secret_key = secret_key
        self.session_cookie = session_cookie
        self.https_only = https_only
        self.default_max_age = default_max_age
        self.same_site = same_site

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        base = get_settings()
        runtime_path = scope.get("path", "")
        if (runtime_path != "/health" and not runtime_path.startswith("/static/")
                and scope.get("app") and getattr(scope["app"].state, "config_ready", False)):
            try:
                def load_snapshot():
                    with SessionLocal() as db:
                        return current(db, base)
                snapshot = await anyio.to_thread.run_sync(load_snapshot)
            except Exception:
                await PlainTextResponse("Configuration unavailable", status_code=503)(scope, receive, send)
                return
        else:
            snapshot = base
        marker = _request_settings.set(snapshot)
        try:
            middleware = SessionMiddleware(self.app, secret_key=self.secret_key,
                session_cookie=self.session_cookie, same_site=self.same_site, https_only=self.https_only,
                max_age=snapshot.session_idle_timeout_seconds)
            await middleware(scope, receive, send)
        finally:
            _request_settings.reset(marker)


def create_app() -> FastAPI:
    settings = get_settings()
    is_production = settings.app_env.lower() == "production"
    secure_cookie = settings.session_cookie_secure if settings.session_cookie_secure is not None else is_production
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )
    app.state.bootstrap_settings = settings
    session_secret = settings.secret_key or secrets.token_urlsafe(32)
    app.add_middleware(
        RuntimeSessionMiddleware,
        secret_key=session_secret,
        # A browser must not overwrite an old Secure cookie from plain HTTP.
        # Separate names also prevent an HTTP session from becoming an HTTPS one.
        session_cookie="__Host-iptable_session" if secure_cookie else "iptable_http_session",
        same_site="lax",
        https_only=secure_cookie,
        default_max_age=settings.session_idle_timeout_seconds,
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path != "/health" and not request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if is_production:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
                "img-src 'self' data:; script-src 'self'; style-src 'self'",
            )
            if secure_cookie:
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["_"] = lambda key, **kwargs: make_translator(get_settings().interface_language)(key, **kwargs)
    templates.context_processors.append(lambda request: {
        "html_language": html_language(get_settings().interface_language),
        "interface_language": normalize_language(get_settings().interface_language),
        "app_name": get_settings().app_name,
    })
    templates.env.globals["csrf_input"] = csrf_input
    app.state.templates = templates
    app.include_router(router)
    app.include_router(api_router)
    return app


app = create_app()
