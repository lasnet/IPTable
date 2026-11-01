import copy
from datetime import datetime, timedelta
import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import create_app
from app.models import Base, User, UserSession
from app.services.auth import hash_password


class BrowserSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.settings = Settings(_env_file=None, app_env="local", session_cookie_secure=False)
        with self.factory() as db:
            db.add_all([
                User(username="reader", password_hash=hash_password("reader-password")),
                User(username="admin", password_hash=hash_password("admin-password"), is_admin=True),
            ])
            db.commit()
            self.user_id = db.scalar(select(User.id).where(User.username == "reader"))

        def database():
            with self.factory() as db:
                yield db

        with patch("app.main.get_settings", return_value=self.settings):
            self.app = create_app()
        self.app.dependency_overrides[get_db] = database
        self.app.dependency_overrides[get_settings] = lambda: self.settings

    def client(self) -> TestClient:
        return TestClient(self.app, follow_redirects=False)

    def csrf(self, client: TestClient, path: str = "/") -> str:
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="_csrf_token" value="([^"]+)"', response.text).group(1)

    def login(self, client: TestClient, username: str = "reader", password: str = "reader-password") -> str:
        token = self.csrf(client, "/login")
        response = client.post("/login", data={"_csrf_token": token, "username": username, "password": password})
        self.assertEqual(response.status_code, 303)
        return self.csrf(client)

    def clone(self, client: TestClient) -> TestClient:
        other = self.client()
        for cookie in client.cookies.jar:
            other.cookies.jar.set_cookie(copy.copy(cookie))
        return other

    def test_logout_revokes_copied_cookie_but_preserves_independent_login(self) -> None:
        browser = self.client()
        csrf = self.login(browser)
        attacker = self.clone(browser)
        independent = self.client()
        self.login(independent)
        self.assertEqual(browser.post("/logout", data={"_csrf_token": csrf}).status_code, 303)
        self.assertEqual(browser.get("/").status_code, 303)
        self.assertEqual(attacker.get("/").status_code, 303)
        self.assertEqual(independent.get("/").status_code, 200)
        with self.factory() as db:
            self.assertEqual(len(db.scalars(select(UserSession)).all()), 1)

    def test_password_change_revokes_all_old_sessions_and_new_password_works(self) -> None:
        admin = self.client()
        csrf = self.login(admin, "admin", "admin-password")
        browser = self.client()
        self.login(browser)
        attacker = self.clone(browser)
        independent = self.client()
        self.login(independent)
        self.assertEqual(admin.post(f"/admin/users/{self.user_id}/update", data={
            "_csrf_token": csrf, "username": "reader", "password": "changed-password", "is_active": "on",
        }).status_code, 303)
        for client in (browser, attacker, independent):
            self.assertEqual(client.get("/").status_code, 303)
        self.login(self.client(), password="changed-password")

    def test_deactivation_then_reactivation_does_not_revive_cookie(self) -> None:
        admin = self.client()
        csrf = self.login(admin, "admin", "admin-password")
        browser = self.client()
        self.login(browser)
        for active in (False, True):
            form = {"_csrf_token": csrf, "username": "reader"}
            if active:
                form["is_active"] = "on"
            self.assertEqual(admin.post(f"/admin/users/{self.user_id}/update", data=form).status_code, 303)
        self.assertEqual(browser.get("/").status_code, 303)

    def test_server_expiry_is_enforced_even_with_valid_signed_cookie(self) -> None:
        browser = self.client()
        self.login(browser)
        with self.factory() as db:
            row = db.scalar(select(UserSession))
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(browser.get("/").status_code, 303)

    def test_password_hash_change_also_invalidates_a_session_without_explicit_delete(self) -> None:
        browser = self.client()
        self.login(browser)
        with self.factory() as db:
            db.get(User, self.user_id).password_hash = hash_password("out-of-band-password")
            db.commit()
        self.assertEqual(browser.get("/").status_code, 303)

    def test_private_pages_are_not_cached(self) -> None:
        browser = self.client()
        self.login(browser)
        self.assertEqual(browser.get("/").headers["Cache-Control"], "no-store")
        self.assertEqual(browser.get("/login").headers["Cache-Control"], "no-store")
