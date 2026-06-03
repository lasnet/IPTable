import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import create_app
from app.models import Base, Folder, Project
from app.models import User
from app.web import routes as web_routes
from app.web.routes import _load_sidebar_folders


class SidebarTest(unittest.TestCase):
    def test_sidebar_projects_are_sorted_by_network_address(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            folder = Folder(name="Office")
            db.add(folder)
            db.flush()
            db.add_all(
                [
                    Project(folder_id=folder.id, name="third", cidr="10.10.10.0/24"),
                    Project(folder_id=folder.id, name="first", cidr="10.10.2.0/24"),
                    Project(folder_id=folder.id, name="second", cidr="10.10.3.0/24"),
                ]
            )
            db.commit()

            folders = _load_sidebar_folders(db)

        self.assertEqual(
            [project.cidr for project in folders[0].projects],
            ["10.10.2.0/24", "10.10.3.0/24", "10.10.10.0/24"],
        )

    def test_index_does_not_redirect_to_first_project(self) -> None:
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
            db.add(Project(folder_id=folder.id, name="LAN", cidr="192.168.1.0/24"))
            db.commit()

        def override_db():
            db = session_factory()
            try:
                yield db
            finally:
                db.close()

        app = create_app()
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[web_routes.require_user] = lambda: User(
            id=1,
            username="admin",
            is_admin=True,
            is_active=True,
        )
        client = TestClient(app, follow_redirects=False)

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("location"))
        self.assertIn("Выберите проект", response.text)


if __name__ == "__main__":
    unittest.main()
