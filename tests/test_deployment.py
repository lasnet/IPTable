import os
from pathlib import Path
import secrets
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app
from scripts.init_production import generate_environment


class DeploymentTest(unittest.TestCase):
    def test_generated_environment_is_private_and_independent(self) -> None:
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.env"
            second = Path(directory) / "second.env"
            for output in (first, second):
                generate_environment("ghcr.io/example/iptable:v0.1.0-rc.1", output)
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                with patch.dict(os.environ, {}, clear=True):
                    settings = Settings(_env_file=output)
                    self.assertEqual(settings.app_env, "production")
                    self.assertGreaterEqual(len(settings.secret_key), 32)
                    self.assertGreaterEqual(len(settings.initial_admin_password), 12)
            self.assertNotEqual(first.read_text(), second.read_text())

    def test_generator_never_overwrites_existing_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / ".env"
            output.write_text("existing configuration", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                generate_environment("ghcr.io/example/iptable:v0.1.0", output)
            self.assertEqual(output.read_text(), "existing configuration")

    def test_generator_rejects_unversioned_images_and_newlines(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / ".env"
            for image in ("iptable", "iptable:latest", "iptable:develop", "iptable:v1.0.0\nAPP_ENV=local"):
                with self.subTest(image=image), self.assertRaises(ValueError):
                    generate_environment(image, output)
            self.assertFalse(output.exists())

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
