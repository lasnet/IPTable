import os
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import Settings, get_settings
from app.main import create_app
from app.models import Base, LoginRateLimitEvent
from app.services.security import (
    LoginRateLimiter,
    csrf_input,
    ensure_csrf_token,
    login_rate_limit_retry_after,
    record_login_failure,
    require_csrf_token,
    reset_login_failures,
)
from app.services.request_limits import RequestBodyLimitMiddleware
from app.web.routes import SESSION_LAST_ACTIVITY_KEY, _session_idle_expired


class SecurityTest(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_shared_dotenv_accepts_compose_variables(self) -> None:
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "APP_PORT=8000\nPOSTGRES_USER=iptable\nPOSTGRES_DB=iptable\n"
                "POSTGRES_PASSWORD=\nSESSION_IDLE_TIMEOUT_SECONDS=8640\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = Settings(_env_file=env_file)
                self.assertEqual(settings.session_idle_timeout_seconds, 8640)

            env_file.write_text("PING_CONCURRENCY=0\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValidationError):
                Settings(_env_file=env_file)

    def test_production_requires_secret_key(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(app_env="production", secret_key="")

    def test_production_rejects_insecure_cookie_override(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, app_env="production", secret_key="x" * 32,
                     initial_admin_password="test-password", session_cookie_secure=False)

    def test_production_cookie_and_headers_are_secure(self) -> None:
        settings = Settings(_env_file=None, app_env="production", secret_key="x" * 32,
                            initial_admin_password="test-password")
        with patch("app.main.get_settings", return_value=settings):
            app = create_app()
        response = TestClient(app, base_url="https://testserver").get("/login")
        self.assertIn("secure", response.headers["set-cookie"].lower())
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertIn("Strict-Transport-Security", response.headers)
        self.assertIn("Content-Security-Policy", response.headers)

    def test_csrf_input_escapes_session_token(self) -> None:
        request = SimpleNamespace(session={"csrf_token": '\"><script>alert(1)</script>'})
        markup = str(csrf_input(request))
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)

    def test_production_disables_openapi_routes(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "x" * 32,
                "INITIAL_ADMIN_PASSWORD": "admin-password",
            },
        ):
            get_settings.cache_clear()
            app = create_app()

        self.assertIsNone(app.openapi_url)
        self.assertIsNone(app.docs_url)
        self.assertIsNone(app.redoc_url)

    def test_csrf_dependency_rejects_missing_token(self) -> None:
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")

        @app.get("/form", response_class=HTMLResponse)
        def form(request: Request) -> str:
            token = ensure_csrf_token(request)
            return f'<input type="hidden" name="_csrf_token" value="{token}">'

        @app.post("/submit", dependencies=[Depends(require_csrf_token)])
        def submit() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        form_response = client.get("/form")
        token = re.search('value="([^"]+)"', form_response.text).group(1)

        self.assertEqual(client.post("/submit", data={}).status_code, 403)
        self.assertEqual(client.post("/submit", data={"_csrf_token": token}).status_code, 200)

    def test_login_rate_limiter_locks_after_configured_failures(self) -> None:
        limiter = LoginRateLimiter()

        self.assertIsNone(
            limiter.record_failure(
                "127.0.0.1",
                "admin",
                attempts=2,
                window_seconds=60,
                lockout_seconds=300,
                now=1000,
            )
        )
        retry_after = limiter.record_failure(
            "127.0.0.1",
            "admin",
            attempts=2,
            window_seconds=60,
            lockout_seconds=300,
            now=1001,
        )

        self.assertEqual(retry_after, 300)
        self.assertEqual(
            limiter.retry_after("127.0.0.1", "admin", window_seconds=60, now=1002),
            299,
        )

    def test_database_login_rate_limiter_is_shared_via_db(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            self.assertIsNone(
                record_login_failure(
                    db,
                    "127.0.0.1",
                    "admin",
                    attempts=2,
                    window_seconds=60,
                    lockout_seconds=300,
                    now=datetime.fromtimestamp(1000),
                )
            )
            retry_after = record_login_failure(
                db,
                "127.0.0.1",
                "admin",
                attempts=2,
                window_seconds=60,
                lockout_seconds=300,
                now=datetime.fromtimestamp(1001),
            )

            self.assertEqual(retry_after, 300)
            self.assertEqual(
                login_rate_limit_retry_after(
                    db,
                    "127.0.0.1",
                    "admin",
                    attempts=2,
                    window_seconds=60,
                    lockout_seconds=300,
                    now=datetime.fromtimestamp(1002),
                ),
                299,
            )
            reset_login_failures(db, "127.0.0.1", "admin")
            remaining_events = db.scalars(select(LoginRateLimitEvent)).all()

        self.assertEqual(remaining_events, [])

    def test_login_events_expire_across_unrelated_identities(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        now = datetime(2026, 11, 13)
        with sessionmaker(bind=engine)() as db:
            record_login_failure(db, "127.0.0.1", "old-user", attempts=5,
                                 window_seconds=300, lockout_seconds=900, now=now - timedelta(days=1))
            login_rate_limit_retry_after(db, "127.0.0.1", "other-user", attempts=5,
                                         window_seconds=300, lockout_seconds=900, now=now)
            self.assertEqual(db.scalars(select(LoginRateLimitEvent)).all(), [])

    def test_login_events_capacity_fails_closed_and_recovers_after_expiry(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        now = datetime(2026, 11, 13)
        with sessionmaker(bind=engine)() as db, patch("app.services.security.MAX_LOGIN_RATE_LIMIT_EVENTS", 3):
            for index in range(10):
                record_login_failure(db, "127.0.0.1", f"user-{index}", attempts=5,
                                     window_seconds=300, lockout_seconds=900, now=now)
            self.assertEqual(len(db.scalars(select(LoginRateLimitEvent)).all()), 3)
            self.assertEqual(login_rate_limit_retry_after(db, "127.0.0.1", "new-user", attempts=5,
                             window_seconds=300, lockout_seconds=900, now=now), 60)
            self.assertIsNone(login_rate_limit_retry_after(db, "127.0.0.1", "new-user", attempts=5,
                              window_seconds=300, lockout_seconds=900, now=now + timedelta(hours=1)))

    def test_session_idle_timeout_expires_old_activity(self) -> None:
        request = SimpleNamespace(session={"user_id": 1, SESSION_LAST_ACTIVITY_KEY: 100})
        settings = Settings(initial_admin_password="x", session_idle_timeout_seconds=60)

        self.assertTrue(_session_idle_expired(request, settings, now=161))

    def test_session_idle_timeout_rejects_missing_activity(self) -> None:
        request = SimpleNamespace(session={"user_id": 1})
        settings = Settings(initial_admin_password="x", session_idle_timeout_seconds=60)

        self.assertTrue(_session_idle_expired(request, settings, now=100))

    def test_session_idle_timeout_keeps_recent_activity(self) -> None:
        request = SimpleNamespace(session={"user_id": 1, SESSION_LAST_ACTIVITY_KEY: 100})
        settings = Settings(initial_admin_password="x", session_idle_timeout_seconds=60)

        self.assertFalse(_session_idle_expired(request, settings, now=160))


class RequestBodyLimitTest(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, chunks, headers=()):
        responses = []
        bodies = []
        messages = iter({"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
                        for i, chunk in enumerate(chunks))

        async def receive():
            return next(messages)

        async def send(message):
            responses.append(message)

        async def endpoint(scope, receive, send):
            data = bytearray()
            while True:
                message = await receive()
                data.extend(message["body"])
                if not message.get("more_body"):
                    break
            bodies.append(bytes(data))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        app = RequestBodyLimitMiddleware(endpoint, max_bytes=10)
        await app({"type": "http", "headers": list(headers)}, receive, send)
        return responses[0]["status"], bodies

    async def test_declared_oversize_rejected_before_endpoint(self):
        self.assertEqual(await self.run_request([], [(b"content-length", b"11")]), (413, []))

    async def test_streamed_body_limit_with_absent_or_understated_length(self):
        for headers in [[], [(b"content-length", b"1")]]:
            self.assertEqual(await self.run_request([b"123456", b"78901"], headers), (413, []))

    async def test_accepted_body_is_replayed_exactly(self):
        self.assertEqual(await self.run_request([b"12345", b"67890"]), (200, [b"1234567890"]))


if __name__ == "__main__":
    unittest.main()
