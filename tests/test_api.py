import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import api_router
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import Base, Folder, Project


class IntegrationApiTest(unittest.TestCase):
    def test_projects_endpoint_requires_token_and_returns_projects(self) -> None:
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

        app = FastAPI()
        app.include_router(api_router)
        app.dependency_overrides[get_settings] = lambda: Settings(
            initial_admin_password="admin-password",
            integration_api_token="secret-token",
        )
        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        self.assertEqual(client.get("/api/v1/projects").status_code, 401)
        response = client.get("/api/v1/projects", headers={"X-API-Key": "secret-token"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["cidr"], "192.168.1.0/24")


if __name__ == "__main__":
    unittest.main()
