import unittest
import re

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import create_app
from app.models import Base, Folder, HistoryEntry, IPAddress, Project, User
from app.services.inventory import is_ip_record_empty
from app.web import routes as web_routes


class InventoryServiceTest(unittest.TestCase):
    def test_ip_record_empty_when_visible_fields_are_blank(self) -> None:
        ip_record = IPAddress(project_id=1, ordinal=1, address="10.0.0.1")

        self.assertTrue(is_ip_record_empty(ip_record))

    def test_ip_record_empty_counts_visible_fields_and_custom_values(self) -> None:
        self.assertFalse(
            is_ip_record_empty(IPAddress(project_id=1, ordinal=1, address="10.0.0.1", hostname="gw"))
        )
        self.assertFalse(
            is_ip_record_empty(
                IPAddress(project_id=1, ordinal=2, address="10.0.0.2", custom_values={"rack": "A1"})
            )
        )

    def test_ip_mutation_routes_require_edit_permission(self) -> None:
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as db:
            folder = Folder(name="Office")
            db.add(folder)
            db.flush()
            project = Project(folder_id=folder.id, name="LAN", cidr="10.0.0.0/24")
            db.add(project)
            db.flush()
            ip_record = IPAddress(project_id=project.id, ordinal=1, address="10.0.0.1", hostname="original")
            db.add(ip_record)
            db.commit()
            project_id, ip_id = project.id, ip_record.id

        def override_db():
            with session_factory() as db:
                yield db

        app = create_app()
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[web_routes.require_user] = lambda: User(
            id=1, username="reader", is_active=True, can_edit=False,
        )
        client = TestClient(app)
        login_page = client.get("/login")
        csrf_token = re.search(r'name="_csrf_token" value="([^"]+)"', login_page.text).group(1)
        address_path = f"/projects/{project_id}/addresses/{ip_id}"

        for path in (address_path, f"{address_path}/clear"):
            with self.subTest(path=path):
                response = client.post(
                    path,
                    data={"_csrf_token": csrf_token, "hostname": "changed"},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 403)
                with session_factory() as db:
                    self.assertEqual(db.get(IPAddress, ip_id).hostname, "original")
                    self.assertEqual(db.scalars(select(HistoryEntry)).all(), [])

        app.dependency_overrides[web_routes.require_user] = lambda: User(
            id=2, username="editor", is_active=True, can_edit=True,
        )
        response = client.post(
            address_path,
            data={"_csrf_token": csrf_token, "hostname": "changed"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        with session_factory() as db:
            self.assertEqual(db.get(IPAddress, ip_id).hostname, "changed")

        response = client.post(
            f"{address_path}/clear",
            data={"_csrf_token": csrf_token},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        with session_factory() as db:
            self.assertEqual(db.get(IPAddress, ip_id).hostname, "")


if __name__ == "__main__":
    unittest.main()
