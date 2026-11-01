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
    def test_public_url_controls_transport_without_disabling_production_guards(self) -> None:
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                settings = Settings(_env_file=None, app_env="production", secret_key=secrets.token_hex(32),
                                    initial_admin_password=secrets.token_urlsafe(24),
                                    app_public_url=f"{scheme}://inventory.example:8000")
                with patch("app.main.get_settings", return_value=settings):
                    client = TestClient(create_app(), base_url=f"{scheme}://inventory.example:8000")
                response = client.get("/login")
                self.assertEqual(response.status_code, 200)
                self.assertEqual("; secure" in response.headers["set-cookie"].lower(), scheme == "https")
                self.assertEqual("strict-transport-security" in response.headers, scheme == "https")
                self.assertEqual(client.get("/docs").status_code, 404)
                self.assertEqual(client.post("/login", data={}).status_code, 403)

    def test_transport_configuration_rejects_invalid_origins_and_cookie_mismatch(self) -> None:
        cases = [
            {"app_public_url": "ftp://inventory.example"},
            {"app_public_url": "https://user:password@inventory.example"},
            {"app_public_url": "https://inventory.example/path"},
            {"app_public_url": "https://inventory.example?x=1"},
            {"app_public_url": "https://inventory.example:0"},
            {"app_public_url": "https://inventory.example:99999"},
            {"app_public_url": "https://inventory.example", "session_cookie_secure": False},
            {"app_public_url": "http://inventory.example", "https_enabled": True},
        ]
        for invalid in cases:
            with self.subTest(config=invalid), self.assertRaises(ValidationError):
                Settings(_env_file=None, **invalid)

    def test_assets_use_browser_origin_behind_https_gateway(self) -> None:
        # The backend sees HTTP when the gateway is outside Uvicorn's trusted
        # proxy range. Asset links must still inherit the browser's HTTPS origin.
        client = TestClient(create_app(), base_url="http://backend:8000")
        response = client.get("/login", headers={"Host": "inventory.example:8443"})
        assets = re.findall(r'<(?:link|script)\b[^>]*(?:href|src)="([^"]+)"', response.text)
        self.assertEqual(len(assets), 2)
        for asset in assets:
            with self.subTest(asset=asset):
                self.assertTrue(asset.startswith("/static/"))
                resource = client.get(asset)
                self.assertEqual(resource.status_code, 200)
                expected = "text/css" if ".css?" in asset else "javascript"
                self.assertIn(expected, resource.headers["content-type"])

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

    def test_production_https_session_persists_and_openapi_stays_disabled(self) -> None:
        with patch.dict(os.environ, {
            "APP_ENV": "production", "SECRET_KEY": secrets.token_hex(32),
            "INITIAL_ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "SESSION_COOKIE_SECURE": "true", "ENABLE_PING_WORKER": "false",
        }):
            get_settings.cache_clear()
            try:
                client = TestClient(create_app(), base_url="https://testserver")
                first = client.get("/login")
                second = client.get("/login")
                token_pattern = r'name="_csrf_token" value="([^"]+)"'
                self.assertEqual(re.search(token_pattern, first.text).group(1), re.search(token_pattern, second.text).group(1))
                self.assertIn("; secure", first.headers["set-cookie"].lower())
                self.assertIn("httponly", first.headers["set-cookie"].lower())
                self.assertIn("Strict-Transport-Security", first.headers)
                self.assertEqual(client.get("/docs").status_code, 404)
                self.assertEqual(client.post("/login", data={}).status_code, 403)
                self.assertEqual(client.post("http://testserver/login", data={
                    "_csrf_token": re.search(token_pattern, first.text).group(1),
                    "username": "admin", "password": "unused-test-password",
                }).status_code, 403)
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
