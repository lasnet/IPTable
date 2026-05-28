import os
import re
import unittest
from datetime import datetime
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
    ensure_csrf_token,
    login_rate_limit_retry_after,
    record_login_failure,
    require_csrf_token,
    reset_login_failures,
)


class SecurityTest(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_production_requires_secret_key(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(app_env="production", secret_key="")

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


if __name__ == "__main__":
    unittest.main()
