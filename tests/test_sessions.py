import copy
import csv
from datetime import UTC, datetime, timedelta
import io
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
from app.models import Base, CustomField, Folder, HistoryEntry, IPAddress, PingJob, Project, Role, User, UserSession
from app.services.auth import hash_password
from app.services.history import build_field_change, record_ip_address_history


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

    def test_import_opens_full_subnet_and_rejects_duplicate_cidr(self) -> None:
        with self.factory() as db:
            db.add(Folder(id=1, name="Office"))
            db.commit()

        client = self.client()
        csrf = self.login(client, "admin", "admin-password")
        form = {"_csrf_token": csrf, "name": "LAN", "cidr": "10.0.0.6/28"}
        files = {"csv_file": ("assets.csv", b"10.0.0.0;Network\n10.0.0.1;gw\n10.0.0.4;server\n")}
        headers = {"X-Requested-With": "XMLHttpRequest"}
        path = "/folders/1/projects/import"

        self.assertEqual(client.post(path, data={"name": "LAN"}, files=files, headers=headers).status_code, 403)
        response = client.post(path, data=form, files=files, headers=headers)
        self.assertEqual(response.status_code, 201)
        project_page = client.get(response.json()["location"])
        self.assertEqual(project_page.status_code, 200)
        self.assertIn("10.0.0.2", project_page.text)
        self.assertIn("10.0.0.14", project_page.text)
        self.assertIn("Пропущено служебных адресов (network/broadcast): 1", project_page.text)

        duplicate = client.post(path, data={**form, "name": "Other"}, files=files, headers=headers)
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("Подсеть 10.0.0.0/28 уже существует", duplicate.json()["error"])
        manual_duplicate = client.post("/projects", data={**form, "folder_id": 1, "name": "Manual"})
        page = client.get(manual_duplicate.headers["location"])
        self.assertRegex(page.text, r'data-project-error autofocus>Подсеть 10\.0\.0\.0/28 уже существует')

        with self.factory() as db:
            project = db.scalars(select(Project)).one()
            records = db.scalars(select(IPAddress).order_by(IPAddress.ordinal)).all()
            self.assertEqual(project.cidr, "10.0.0.0/28")
            self.assertEqual([row.address for row in records], [f"10.0.0.{host}" for host in range(1, 15)])
            self.assertEqual(records[0].hostname, "gw")
            self.assertEqual(records[1].hostname, "")
            self.assertEqual(records[3].hostname, "server")
            self.assertEqual(db.scalars(select(PingJob)).one().project_id, project.id)

    def test_history_records_inventory_mutations_and_survives_deletion(self) -> None:
        admin = self.client()
        token = self.login(admin, "admin", "admin-password")

        def post(path, **fields):
            return admin.post(path, data={"_csrf_token": token, **fields})

        self.assertEqual(post("/folders", name="Office").status_code, 303)
        self.assertIn("Создать подсеть", admin.get("/inventory").text)
        self.assertEqual(post("/projects", folder_id=1, name="LAN", cidr="192.0.2.0/30").status_code, 303)
        self.assertEqual(post("/projects/1/update", name="LAN renamed", description="Rack B").status_code, 303)
        self.assertEqual(post("/projects/1/update", name="LAN renamed", description="Rack B").status_code, 303)

        imported = admin.post("/folders/1/projects/import", data={"_csrf_token": token, "name": "Imported"},
                              files={"csv_file": ("Склад.csv", b"198.51.100.1;server\n")})
        self.assertEqual(imported.status_code, 303)
        self.assertIn("error=", post("/projects/1/update", name="Imported", description="Rejected").headers["location"])
        self.assertEqual(post("/folders/1/update", name="Warehouse").status_code, 303)

        with self.factory() as db:
            address = db.scalar(select(IPAddress).where(IPAddress.address == "192.0.2.1"))
            change = build_field_change("hostname", "Hostname", "", "retained-ip-edit")
            record_ip_address_history(db, ip_record=address, user=None, changes=[change])
            db.commit()
        subnet_history = admin.get("/projects/1/history").text
        self.assertIn("Создание подсети", subnet_history)
        self.assertIn("Rack B", subnet_history)
        self.assertNotIn("Склад.csv", subnet_history)

        self.assertEqual(post("/projects/1/delete").status_code, 303)
        folder_history = admin.get("/folders/1/history").text
        self.assertIn("Удаление подсети", folder_history)
        self.assertIn("Склад.csv", folder_history)
        self.assertIn("retained-ip-edit", folder_history)
        self.assertEqual(post("/folders/1/delete").status_code, 303)

        global_history = admin.get("/history").text
        self.assertIn("Удаление папки", global_history)
        self.assertIn("retained-ip-edit", global_history)
        self.assertNotIn("Rejected", global_history)
        with self.factory() as db:
            deleted_subnets = db.scalars(select(HistoryEntry).where(HistoryEntry.action == "subnet_deleted")).all()
            self.assertEqual({(row.cidr, row.username) for row in deleted_subnets}, {
                ("192.0.2.0/30", "admin"), ("198.51.100.1/32", "admin"),
            })
            updates = db.scalars(select(HistoryEntry).where(HistoryEntry.action == "subnet_updated")).all()
            self.assertEqual(len(updates), 2)

    def test_history_export_respects_period_scope_and_permissions_without_page_limit(self) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        with self.factory() as db:
            db.add(Folder(id=1, name="Office", created_at=now - timedelta(days=150)))
            db.add(Project(id=1, folder_id=1, name="LAN", cidr="192.0.2.0/24", created_at=now - timedelta(days=150)))
            for days, value in ((0.5, "=1+1"), (2, "2d"), (10, "10d"), (40, "40d"), (100, "100d")):
                db.add(HistoryEntry(
                    action="ip_updated", folder_id=1, project_id=1, folder_name="Office", subnet_name="LAN",
                    cidr="192.0.2.0/24", address="192.0.2.1", username="admin", field_label="Hostname",
                    field_name="hostname", new_value=value, created_at=now - timedelta(days=days),
                ))
            db.add_all(HistoryEntry(action="folder_created", folder_id=2, folder_name=f"Folder {i}") for i in range(205))
            db.commit()

        admin = self.client()
        token = self.login(admin, "admin", "admin-password")
        self.assertEqual(admin.get("/history").text.count('class="history-item"'), 200)
        self.assertEqual(admin.post("/history/export", data={"period": "all"}).status_code, 403)
        reader = self.client()
        reader_token = self.login(reader)
        self.assertNotIn('id="history-export-modal"', reader.get("/history").text)
        self.assertEqual(reader.post("/history/export", data={"period": "all", "_csrf_token": reader_token}).status_code, 403)

        def export(period, **filters):
            response = admin.post("/history/export", data={"period": period, "_csrf_token": token, **filters})
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
            return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))

        self.assertEqual(len(export("all")), 210)
        expected = ["'=1+1", "2d", "10d", "40d", "100d"]
        for count, period in enumerate(("1", "7", "30", "90", "all"), 1):
            with self.subTest(period=period):
                rows = export(period, project_id=1, address="192.0.2.1", q="192.0.2")
                self.assertEqual([row["Стало"] for row in rows], expected[:count])

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
            db.add_all(HistoryEntry(
                action="ip_updated", folder_id=1, folder_name="Office", subnet_name="Office LAN", cidr="192.0.2.0/24",
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

    def test_read_only_role_can_view_ip_but_cannot_change_or_clear_it(self) -> None:
        with self.factory() as db:
            db.add(Role(id=4, name="Inventory viewer", name_key="inventory viewer", can_delete=True))
            db.get(User, self.user_id).role_id = 4
            db.add(Folder(id=1, name="Office"))
            db.add(Project(id=1, folder_id=1, name="Office LAN", cidr="192.0.2.0/24"))
            db.add(IPAddress(id=1, project_id=1, ordinal=1, address="192.0.2.1", hostname="original"))
            db.commit()
        reader = self.client()
        token = self.login(reader)
        table = reader.get("/projects/1")
        self.assertIn("data-host-drawer-open", table.text)
        self.assertIn("data-host-drawer-history", table.text)
        for control in ("data-host-drawer-edit", "data-host-drawer-save", "data-host-drawer-clear", "data-update-url"):
            self.assertNotIn(control, table.text)
        for path in ("/projects/1/addresses/1", "/projects/1/addresses/1/clear"):
            response = reader.post(path, headers={"Accept": "text/html"}, data={
                "_csrf_token": token, "hostname": "blocked", "hide_empty": "true", "per_page": "50",
            })
            self.assertEqual(response.status_code, 303)
            self.assertTrue(response.headers["location"].startswith("/projects/1?hide_empty=true&page=1&per_page=50"))
            self.assertIn("У вас нет прав на редактирование IP-адресов.",
                          reader.get(response.headers["location"]).text)
        with self.factory() as db:
            self.assertEqual(db.get(IPAddress, 1).hostname, "original")
            self.assertIsNone(db.scalar(select(HistoryEntry.id)))

    def test_only_unassigned_custom_roles_can_be_deleted(self) -> None:
        with self.factory() as db:
            db.add(Role(id=4, name="Custom operator", name_key="custom operator"))
            db.get(User, self.user_id).role_id = 4
            db.commit()
        reader = self.client()
        reader_token = self.login(reader)
        admin = self.client()
        admin_token = self.login(admin, "admin", "admin-password")
        page = admin.get("/admin/roles")
        self.assertIn('action="/admin/roles/4/delete"', page.text)
        self.assertNotIn('action="/admin/roles/1/delete"', page.text)
        self.assertEqual(reader.post("/admin/roles/4/delete", data={"_csrf_token": reader_token}).status_code, 403)
        self.assertEqual(admin.post("/admin/roles/4/delete").status_code, 403)

        for role_id in (1, 2, 3, 4):
            response = admin.post(f"/admin/roles/{role_id}/delete", data={"_csrf_token": admin_token})
            self.assertIn("error=", response.headers["location"])
        with self.factory() as db:
            self.assertEqual(db.get(User, self.user_id).role_id, 4)
            self.assertIsNotNone(db.get(Role, 4))
        admin.post(f"/admin/users/{self.user_id}/update", data={
            "_csrf_token": admin_token, "username": "reader", "is_active": "on", "role_id": "2",
        })
        response = admin.post("/admin/roles/4/delete", data={"_csrf_token": admin_token})
        self.assertIn("message=", response.headers["location"])
        with self.factory() as db:
            self.assertEqual(db.get(User, self.user_id).role_id, 2)
            self.assertEqual(list(db.scalars(select(Role.id).order_by(Role.id))), [1, 2, 3])

    def test_address_edit_lock_coordinates_two_users_and_rejects_stale_saves(self) -> None:
        with self.factory() as db:
            db.get(User, self.user_id).role_id = 3
            db.add(Folder(id=1, name="Office"))
            db.add(Project(id=1, folder_id=1, name="Office LAN", cidr="192.0.2.0/24"))
            db.add(IPAddress(id=1, project_id=1, ordinal=1, address="192.0.2.1", hostname="original"))
            db.commit()
        first, second = self.client(), self.client()
        first_csrf = self.login(first, "admin", "admin-password")
        second_csrf = self.login(second)
        address = "/projects/1/addresses/1"
        lock = f"{address}/edit-lock"
        token = first.post(lock, data={"_csrf_token": first_csrf}).json()["token"]
        self.assertEqual(second.get(lock).json(), {"owner": "admin"})
        conflict = second.post(lock, data={"_csrf_token": second_csrf})
        self.assertEqual((conflict.status_code, conflict.json()), (409, {"owner": "admin"}))
        self.assertEqual(first.post(lock, data={"_csrf_token": first_csrf}).status_code, 409)
        self.assertEqual(second.post(f"{lock}/renew", data={"_csrf_token": second_csrf, "token": token}).status_code, 409)
        second.post(f"{lock}/release", data={"_csrf_token": second_csrf, "token": token})
        self.assertEqual(second.get(lock).json(), {"owner": "admin"})
        self.assertEqual(second.post(f"{address}/clear", data={"_csrf_token": second_csrf}).status_code, 409)
        self.assertEqual(first.post(f"{lock}/renew", data={"_csrf_token": first_csrf, "token": token}).status_code, 204)

        response = first.post(address, headers={"Accept": "text/html"}, data={
            "_csrf_token": first_csrf, "edit_lock_token": token, "hostname": "saved-by-admin",
        })
        self.assertEqual(response.status_code, 303)
        self.assertIsNone(second.get(lock).json()["owner"])
        next_lease = second.post(lock, data={"_csrf_token": second_csrf}).json()
        self.assertEqual(next_lease["fields"]["hostname"], "saved-by-admin")
        self.assertEqual(first.post(address, data={
            "_csrf_token": first_csrf, "edit_lock_token": token, "hostname": "stale",
        }).status_code, 409)

        with self.factory() as db:
            row = db.get(IPAddress, 1)
            self.assertEqual(row.hostname, "saved-by-admin")
            row.edit_lock_expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertIsNone(first.get(lock).json()["owner"])
        self.assertEqual(second.post(address, data={
            "_csrf_token": second_csrf, "edit_lock_token": next_lease["token"], "hostname": "expired",
        }).status_code, 409)
        self.assertEqual(first.post(lock, data={"_csrf_token": first_csrf}).status_code, 200)

    def test_edit_lock_polling_does_not_extend_idle_session(self) -> None:
        with self.factory() as db:
            db.add(Folder(id=1, name="Office"))
            db.add(Project(id=1, folder_id=1, name="LAN", cidr="192.0.2.0/24"))
            db.add(IPAddress(id=1, project_id=1, ordinal=1, address="192.0.2.1"))
            db.commit()

        self.settings.session_idle_timeout_seconds = 60
        client = self.client()
        lock = "/projects/1/addresses/1/edit-lock"
        with patch("app.web.routes.time") as clock:
            clock.time.return_value = 1000
            csrf = self.login(client, "admin", "admin-password")
            token = client.post(lock, data={"_csrf_token": csrf}).json()["token"]
            renewal = {"_csrf_token": csrf, "token": token}
            with self.factory() as db:
                expires_at = db.scalar(select(UserSession.expires_at))

            for elapsed in (20, 40, 59):
                clock.time.return_value = 1000 + elapsed
                status = client.get(lock)
                self.assertEqual(status.json(), {"owner": "admin"})
                self.assertNotIn("set-cookie", status.headers)
                renewed = client.post(f"{lock}/renew", data=renewal)
                self.assertEqual(renewed.status_code, 204)
                self.assertNotIn("set-cookie", renewed.headers)
            with self.factory() as db:
                self.assertEqual(db.scalar(select(UserSession.expires_at)), expires_at)

            clock.time.return_value = 1061
            self.assertEqual(client.get(lock).status_code, 401)
            self.assertEqual(client.post(f"{lock}/renew", data=renewal).status_code, 401)
            self.assertEqual(client.get("/").status_code, 303)

    def test_edit_lock_save_rejects_recreated_custom_field(self) -> None:
        with self.factory() as db:
            db.add(Folder(id=1, name="Office"))
            db.add(Project(id=1, folder_id=1, name="LAN", cidr="192.0.2.0/24"))
            db.add(IPAddress(id=1, project_id=1, ordinal=1, address="192.0.2.1", hostname="original"))
            field = CustomField(project_id=1, name="Owner", key="owner", created_at=datetime(2026, 1, 1))
            db.add(field)
            db.commit()
            stale_input = field.drawer_input_name

        editor, admin = self.client(), self.client()
        editor_csrf = self.login(editor, "admin", "admin-password")
        admin_csrf = self.login(admin, "admin", "admin-password")
        address = "/projects/1/addresses/1"
        lock = f"{address}/edit-lock"
        token = editor.post(lock, data={"_csrf_token": editor_csrf}).json()["token"]
        fields = "/projects/1/fields"
        self.assertEqual(admin.post(f"{fields}/configure", data={"_csrf_token": admin_csrf}).status_code, 303)
        self.assertEqual(admin.post(fields, data={"_csrf_token": admin_csrf, "name": "Owner"}).status_code, 303)
        with self.factory() as db:
            field = db.scalar(select(CustomField))
            self.assertEqual(field.key, "owner")
            fresh_input = field.drawer_input_name
            self.assertNotEqual(fresh_input, stale_input)

        response = editor.post(address, data={
            "_csrf_token": editor_csrf, "edit_lock_token": token, "hostname": "stale", stale_input: "stale",
        })
        self.assertEqual(response.status_code, 409)
        with self.factory() as db:
            row = db.get(IPAddress, 1)
            self.assertEqual(row.hostname, "original")
            self.assertEqual(row.custom_values, {})
            self.assertIsNone(db.scalar(select(HistoryEntry)))

        self.assertIn(f'data-custom-name="{fresh_input}"', editor.get("/projects/1").text)
        token = editor.post(lock, data={"_csrf_token": editor_csrf}).json()["token"]
        response = editor.post(address, data={
            "_csrf_token": editor_csrf, "edit_lock_token": token, "hostname": "original", fresh_input: "current",
        })
        self.assertEqual(response.status_code, 303)
        with self.factory() as db:
            self.assertEqual(db.get(IPAddress, 1).custom_values, {"owner": "current"})

    def test_column_visibility_is_personal_and_preserves_address_values(self) -> None:
        with self.factory() as db:
            db.add(Folder(id=1, name="Office"))
            db.add_all([
                Project(id=1, folder_id=1, name="LAN", cidr="192.0.2.0/24"),
                Project(id=2, folder_id=1, name="Lab", cidr="198.51.100.0/24"),
            ])
            field = CustomField(project_id=1, name="Rack", key="rack")
            db.add(field)
            db.add_all([
                IPAddress(project_id=1, ordinal=1, address="192.0.2.1", comment="Only value",
                          custom_values={"rack": "A1"}),
                IPAddress(project_id=2, ordinal=1, address="198.51.100.1", comment="Other project"),
            ])
            db.commit()
            custom_key = field.visibility_key

        reader, admin = self.client(), self.client()
        csrf_token = self.login(reader)
        self.login(admin, "admin", "admin-password")
        path = "/projects/1"
        visibility_path = f"{path}/fields/visibility"
        self.assertIn(f'action="{visibility_path}"', reader.get(path).text)
        self.assertNotIn(f'action="{path}/fields/configure"', reader.get(path).text)
        self.assertEqual(reader.post(visibility_path, data={"hidden_column": "comment"}).status_code, 403)
        response = reader.post(visibility_path, data={
            "_csrf_token": csrf_token, "hidden_column": ["comment", custom_key, "address", "status"],
            "hide_empty": "true",
        })
        self.assertEqual(response.status_code, 303)
        for headers in ({}, {"X-Requested-With": "XMLHttpRequest"}):
            body = reader.get(response.headers["location"], headers=headers).text
            self.assertNotIn("<th>Comment</th>", body)
            self.assertNotIn("<th>Rack</th>", body)
            self.assertIn('data-detail-value="Only value" hidden', body)
            self.assertIn('data-custom-value="A1" hidden', body)
            self.assertIn('data-row-address="192.0.2.1"', body)
            self.assertIn('class="col-ip"', body)
            self.assertIn('class="col-status"', body)
        self.assertIn("<th>Comment</th>", reader.get("/projects/2").text)
        self.assertIn("<th>Comment</th>", admin.get(path).text)
        self.assertIn("<th>Rack</th>", admin.get(path).text)
        editor_fragment = admin.get(path, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertIn("data-update-url=", editor_fragment.text)
        reader.post(visibility_path, data={"_csrf_token": csrf_token})
        self.assertIn("<th>Comment</th>", reader.get(path).text)
        self.assertIn("<th>Rack</th>", reader.get(path).text)

    def test_admin_hides_columns_for_the_subnet_without_changing_personal_preferences(self) -> None:
        with self.factory() as db:
            db.add(Folder(id=1, name="Office"))
            db.add_all([
                Project(id=1, folder_id=1, name="LAN", cidr="192.0.2.0/24"),
                Project(id=2, folder_id=1, name="Lab", cidr="198.51.100.0/24"),
            ])
            field = CustomField(project_id=1, name="Rack", key="rack")
            db.add(field)
            db.add_all([
                IPAddress(project_id=1, ordinal=1, address="192.0.2.1", comment="Keep this",
                          custom_values={"rack": "A1"}),
                IPAddress(project_id=2, ordinal=1, address="198.51.100.1", comment="Other project"),
            ])
            db.commit()
            custom_key = field.visibility_key
        reader, admin = self.client(), self.client()
        reader_csrf = self.login(reader)
        admin_csrf = self.login(admin, "admin", "admin-password")
        path = "/projects/1"
        personal_path = f"{path}/fields/visibility"
        global_path = f"{path}/fields/global-visibility"
        self.assertNotIn(f'action="{global_path}"', reader.get(path).text)
        self.assertIn(f'action="{global_path}"', admin.get(path).text)
        self.assertEqual(reader.post(global_path, data={"_csrf_token": reader_csrf}).status_code, 403)
        self.assertEqual(admin.post(global_path, data={"hidden_column": "comment"}).status_code, 403)
        self.assertEqual(reader.post(personal_path, data={
            "_csrf_token": reader_csrf, "hidden_column": "comment",
        }).status_code, 303)
        self.assertEqual(admin.post(global_path, data={
            "_csrf_token": admin_csrf, "hidden_column": ["comment", custom_key, "address", "status"],
        }).status_code, 303)
        for browser in (reader, admin):
            self.assertIn("Скрыт для всех", browser.get(path).text)
            table = browser.get(path, headers={"X-Requested-With": "XMLHttpRequest"}).text
            self.assertNotIn("<th>Comment</th>", table)
            self.assertNotIn("<th>Rack</th>", table)
            self.assertIn('data-detail-value="Keep this" hidden', table)
            self.assertIn('data-custom-value="A1" hidden', table)
            self.assertIn('class="col-ip"', table)
            self.assertIn('class="col-status"', table)
            self.assertIn("<th>Comment</th>", browser.get("/projects/2").text)
        self.assertEqual(admin.post(global_path, data={"_csrf_token": admin_csrf}).status_code, 303)
        self.assertIn("<th>Comment</th>", admin.get(path).text)
        self.assertNotIn("<th>Comment</th>", reader.get(path).text)
        self.assertIn("<th>Rack</th>", reader.get(path).text)
