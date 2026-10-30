import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import create_app
from app.models import Base, Folder, IPAddress, IPAddressHistory, Project, User
from app.web import routes as web_routes


class HistorySearchTest(unittest.TestCase):
    def test_search_filters_history_by_address_prefix_in_each_scope(self) -> None:
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
            for ordinal, address in enumerate(("10.0.0.1", "10.0.0.2"), start=1):
                ip_record = IPAddress(project_id=project.id, ordinal=ordinal, address=address)
                db.add(ip_record)
                db.flush()
                db.add(
                    IPAddressHistory(
                        project_id=project.id,
                        ip_address_id=ip_record.id,
                        address=address,
                        username="admin",
                        field_name="hostname",
                        field_label="Hostname",
                        old_value="",
                        new_value=f"host-{ordinal}",
                    )
                )
            db.commit()
            project_id, folder_id = project.id, folder.id

        def override_db():
            with session_factory() as db:
                yield db

        app = create_app()
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[web_routes.require_user] = lambda: User(
            id=1, username="admin", is_admin=True, is_active=True
        )
        client = TestClient(app)

        for path in ("/history", f"/projects/{project_id}/history", f"/folders/{folder_id}/history"):
            response = client.get(path, params={"q": "10.0.0.1"})
            self.assertEqual(response.status_code, 200)
            self.assertIn('class="history-identity"', response.text)
            self.assertNotIn('class="sidebar"', response.text)
            self.assertIn("host-1", response.text)
            self.assertNotIn("host-2", response.text)

        literal_wildcard = client.get("/history", params={"q": "%"})
        self.assertIn("Ничего не найдено", literal_wildcard.text)

        with_address = client.get(
            f"/projects/{project_id}/history", params={"address": "10.0.0.1", "q": "10.0"}
        )
        self.assertIn('name="address" value="10.0.0.1"', with_address.text)
        self.assertIn("host-1", with_address.text)
        self.assertNotIn("host-2", with_address.text)


if __name__ == "__main__":
    unittest.main()
