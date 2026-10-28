import os
import re
import secrets
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app
from sqlalchemy.engine import make_url


class DeploymentTest(unittest.TestCase):
    def test_postgres_settings_encode_password_and_preserve_legacy_url(self) -> None:
        password = secrets.token_hex(12) + "@:/%$#"
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None, postgres_host="postgres", postgres_password=password)
            url = make_url(settings.database_url)
            self.assertEqual(url.password, password)
            self.assertEqual(url.host, "postgres")
            self.assertEqual(url.drivername, "postgresql+psycopg")
            legacy = Settings(_env_file=None, database_url="sqlite:///./custom.sqlite3", postgres_host="postgres")
            self.assertEqual(legacy.database_url, "sqlite:///./custom.sqlite3")

    def test_postgres_requires_password_and_standalone_sqlite_still_works(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, postgres_host="postgres", postgres_password="")
            self.assertEqual(Settings(_env_file=None).database_url, "sqlite:///./data/iptable.sqlite3")

    def test_production_http_session_persists_and_openapi_stays_disabled(self) -> None:
        with patch.dict(os.environ, {
            "APP_ENV": "production", "SECRET_KEY": secrets.token_hex(32),
            "INITIAL_ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "SESSION_COOKIE_SECURE": "false", "ENABLE_PING_WORKER": "false",
        }):
            get_settings.cache_clear()
            try:
                client = TestClient(create_app(), base_url="http://testserver")
                first = client.get("/login")
                second = client.get("/login")
                token_pattern = r'name="_csrf_token" value="([^"]+)"'
                self.assertEqual(re.search(token_pattern, first.text).group(1), re.search(token_pattern, second.text).group(1))
                self.assertNotIn("; secure", first.headers["set-cookie"].lower())
                self.assertIn("httponly", first.headers["set-cookie"].lower())
                self.assertNotIn("Strict-Transport-Security", first.headers)
                self.assertEqual(client.get("/docs").status_code, 404)
                self.assertEqual(client.post("/login", data={}).status_code, 403)
            finally:
                get_settings.cache_clear()

    def test_production_rejects_weak_configuration(self) -> None:
        valid = {"app_env": "production", "secret_key": secrets.token_hex(32),
                 "initial_admin_password": secrets.token_urlsafe(24)}
        cases = [
            {"app_env": "prodution"},
            {"secret_key": "short"},
            {"secret_key": "replace-with-random-32-plus-character-secret"},
            {"initial_admin_password": "short"},
            {"initial_admin_password": "replace-with-strong-admin-password"},
        ]
        for invalid in cases:
            with self.subTest(keys=list(invalid)), self.assertRaises(ValidationError):
                Settings(_env_file=None, **(valid | invalid))

    def test_invalid_configuration_does_not_print_secrets(self) -> None:
        password = secrets.token_urlsafe(24)
        try:
            Settings(_env_file=None, app_env="production", secret_key="short", initial_admin_password=password)
        except ValidationError as exc:
            self.assertNotIn(password, str(exc))
            self.assertNotIn("input_value", str(exc))
        else:
            self.fail("Invalid production configuration was accepted")

    def test_production_security_headers_and_secure_cookie(self) -> None:
        with patch.dict(os.environ, {
            "APP_ENV": "production", "SECRET_KEY": secrets.token_hex(32),
            "INITIAL_ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "ENABLE_PING_WORKER": "false",
        }):
            get_settings.cache_clear()
            try:
                # Without lifespan: no connection to a developer's local database.
                client = TestClient(create_app(), base_url="https://testserver")
                response = client.get("/login")
                self.assertEqual(response.status_code, 200)
                self.assertIn("secure", response.headers["set-cookie"].lower())
                self.assertIn("httponly", response.headers["set-cookie"].lower())
                self.assertIn("Strict-Transport-Security", response.headers)
                self.assertIn("Content-Security-Policy", response.headers)
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                for path in ("/docs", "/redoc", "/openapi.json"):
                    self.assertEqual(client.get(path).status_code, 404)
                self.assertEqual(client.post("/login", data={}).status_code, 403)
            finally:
                get_settings.cache_clear()


if __name__ == "__main__":
    unittest.main()
