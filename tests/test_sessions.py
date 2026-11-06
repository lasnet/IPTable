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
from app.models import Base, Folder, IPAddress, IPAddressHistory, Project, Role, User, UserSession
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
                Role(id=1, code="admin", name="Administrator", name_key="administrator",
                     can_create=True, can_edit=True, can_delete=True, can_manage_columns=True),
                Role(id=2, code="user", name="User", name_key="user"),
                Role(id=3, code="editor", name="Editor", name_key="editor",
                     can_create=True, can_edit=True, can_delete=True, can_manage_columns=True),
            ])
            db.add_all([
                User(username="reader", password_hash=hash_password("reader-password"), role_id=2),
                User(username="admin", password_hash=hash_password("admin-password"), is_admin=True, role_id=1),
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

    def test_users_directory_filters_by_name_and_status_and_opens_profile(self) -> None:
        admin = self.client()
        token = self.login(admin, "admin", "admin-password")
        form = admin.get("/admin/users/new", follow_redirects=True)
        self.assertEqual(form.status_code, 200)
        self.assertIn('autocomplete="new-password" required', form.text)
        created = admin.post("/admin/users", data={
            "_csrf_token": token, "username": "operator", "password": "operator-password",
            "first_name": "Ирина", "last_name": "Волкова", "role_id": "3", "is_active": "off",
            "is_admin": "on",
        })
        self.assertEqual(created.status_code, 303)
        with self.factory() as db:
            account = db.scalar(select(User).where(User.username == "operator"))
            self.assertFalse(account.is_admin)
            self.assertFalse(account.is_active)
            self.assertTrue(account.can_edit_inventory)
            user_id = account.id
            db.add(Folder(id=1, name="Office"))
            db.add(Project(id=1, folder_id=1, name="Office LAN", cidr="192.0.2.0/24"))
            db.add(IPAddress(id=1, project_id=1, ordinal=1, address="192.0.2.1"))
            db.add_all(IPAddressHistory(
                project_id=1, ip_address_id=1, user_id=actor_id, username=actor_name,
                address="192.0.2.1", field_name="hostname", field_label="Hostname", new_value=value,
            ) for actor_id, actor_name, value in (
                (user_id, "old-operator-name", "profile-host"), (self.user_id, "operator", "other-host"),
            ))
            db.commit()

        profile_path = f"/admin/users/{user_id}"
        inactive = admin.get("/admin/users", params={"q": "вОЛКОВА иРИНА", "status": "inactive"})
        self.assertIn(f'href="{profile_path}"', inactive.text)
        self.assertIn(f'href="{profile_path}/edit"', inactive.text)
        self.assertNotIn(f'href="{profile_path}"', admin.get("/admin/users?status=active").text)
        self.assertIn(f'href="{profile_path}"', admin.get("/admin/users?q=OPER").text)

        profile = admin.get(profile_path)
        self.assertIn("Ирина Волкова", profile.text)
        self.assertIn("profile-host", profile.text)
        self.assertNotIn("other-host", profile.text)
        self.assertNotIn("pbkdf2", profile.text)
        edit = admin.get(f"{profile_path}/edit", follow_redirects=True)
        self.assertIn('name="username"', edit.text)
        self.assertIn('name="password" type="password"', edit.text)
        self.assertNotIn("pbkdf2", edit.text)
        self.assertIn(f'id="user-edit-modal-{user_id}"', edit.text)
        self.assertIn("data-open-on-load", edit.text)
        updated = admin.post(f"{profile_path}/update", data={
            "_csrf_token": token, "username": "operator", "first_name": "Ирина",
            "last_name": "Волкова", "is_active": "off", "role_id": "3", "from_profile": "true",
        })
        self.assertEqual(updated.status_code, 303)
        self.assertTrue(updated.headers["location"].startswith(f"{profile_path}?message="))
        self.assertEqual(admin.get("/admin/users/999").status_code, 404)

    def test_users_management_pages_require_admin_and_mutations_require_csrf(self) -> None:
        reader = self.client()
        token = self.login(reader)
        profile_path = f"/admin/users/{self.user_id}"
        for path in ("/admin/users", "/admin/users/new", profile_path, f"{profile_path}/edit", "/admin/roles"):
            self.assertEqual(reader.get(path).status_code, 403)
        self.assertEqual(reader.post(f"{profile_path}/update", data={
            "_csrf_token": token, "username": "reader", "is_active": "on",
        }).status_code, 403)
        admin = self.client()
        self.login(admin, "admin", "admin-password")
        self.assertEqual(admin.post(f"{profile_path}/update", data={"username": "reader"}).status_code, 403)

    def test_custom_roles_apply_shared_permissions_without_granting_admin_access(self) -> None:
        admin = self.client()
        admin_token = self.login(admin, "admin", "admin-password")
        reader = self.client()
        reader_token = self.login(reader)
        role_form = {"name": "Операторы", "can_create": "on", "can_edit": "on"}
        self.assertEqual(reader.post("/admin/roles", data={"_csrf_token": reader_token, **role_form}).status_code, 403)
        self.assertEqual(admin.post("/admin/roles", data=role_form).status_code, 403)
        self.assertEqual(admin.post("/admin/roles", data={
            "_csrf_token": admin_token, **role_form, "code": "admin", "is_admin": "on",
        }).status_code, 303)
        self.assertIn("Операторы", admin.get("/admin/roles").text)
        with self.factory() as db:
            role_id = db.scalar(select(Role.id).where(Role.name == "Операторы"))

        self.assertEqual(admin.post(f"/admin/users/{self.user_id}/update", data={
            "_csrf_token": admin_token, "username": "reader", "is_active": "on", "role_id": role_id,
        }).status_code, 303)
        self.assertEqual(reader.get("/settings").headers["location"], "/login")
        self.login(reader)
        self.assertNotIn('id="settings-security"', reader.get("/settings").text)
        self.assertEqual(reader.get("/admin/users").status_code, 403)
        self.assertIn('data-modal-target="folder-create-modal"', reader.get("/inventory").text)

        self.assertEqual(admin.post("/admin/roles", data={
            "_csrf_token": admin_token, "role_id": role_id, "name": "Операторы",
        }).status_code, 303)
        self.assertNotIn('data-modal-target="folder-create-modal"', reader.get("/inventory").text)
        self.assertEqual(admin.post("/admin/roles", data={
            "_csrf_token": admin_token, "role_id": 1, "name": "Renamed",
        }).status_code, 404)

    def test_delegated_admin_can_manage_global_settings_and_survives_bootstrap(self) -> None:
        from app.services.auth import bootstrap_initial_admin

        admin = self.client()
        token = self.login(admin, "admin", "admin-password")
        self.assertEqual(admin.post(f"/admin/users/{self.user_id}/update", data={
            "_csrf_token": token, "username": "reader", "is_active": "on", "role_id": 1,
        }).status_code, 303)
        with self.factory() as db:
            bootstrap_initial_admin(db, self.settings.model_copy(update={"initial_admin_password": "unused-password"}))
            delegate = db.get(User, self.user_id)
            self.assertFalse(delegate.is_admin)
            self.assertTrue(delegate.has_admin_access)
            self.assertTrue(delegate.can_delete_inventory)

        delegate_browser = self.client()
        delegate_token = self.login(delegate_browser)
        self.assertEqual(delegate_browser.get("/admin/users").status_code, 200)
        self.assertIn('id="settings-security"', delegate_browser.get("/settings").text)
        with self.factory() as db:
            admin_id = db.scalar(select(User.id).where(User.username == "admin"))
        response = delegate_browser.post(f"/admin/users/{admin_id}/update", data={
            "_csrf_token": delegate_token, "username": "admin", "is_active": "off", "role_id": 2,
        })
        self.assertIn("error=", response.headers["location"])
        response = delegate_browser.post(f"/admin/users/{admin_id}/update", data={
            "_csrf_token": delegate_token, "username": "admin", "password": "new-test-password",
        })
        self.assertIn("error=", response.headers["location"])
        with self.factory() as db:
            self.assertTrue(db.get(User, admin_id).is_active)
            self.assertTrue(db.get(User, admin_id).has_admin_access)
