import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import create_app
from app.models import Base, CustomField, Folder, IPAddress, Project, User
from app.services.custom_fields import (
    CustomFieldSpec,
    IncompatibleCustomFieldValueError,
    make_custom_field_key,
    next_available_key,
    replace_custom_fields,
)
from app.web import routes as web_routes


class CustomFieldServiceTest(unittest.TestCase):
    def test_make_custom_field_key(self) -> None:
        self.assertEqual(make_custom_field_key(" Rack Unit "), "rack_unit")

    def test_make_custom_field_key_fallback(self) -> None:
        self.assertEqual(make_custom_field_key("!!!"), "field")

    def test_next_available_key(self) -> None:
        self.assertEqual(next_available_key("rack", {"rack", "rack_2"}), "rack_3")

    def test_replace_custom_fields_creates_updates_reorders_and_deletes(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            folder = Folder(name="Office")
            db.add(folder)
            db.flush()
            project = Project(folder_id=folder.id, name="LAN", cidr="10.0.0.0/24")
            db.add(project)
            db.flush()
            rack = CustomField(project_id=project.id, name="Rack", key="rack", field_type="text", position=1)
            installed = CustomField(
                project_id=project.id,
                name="Installed",
                key="installed",
                field_type="text",
                position=2,
            )
            db.add_all([rack, installed])
            db.flush()
            db.add(
                IPAddress(
                    project_id=project.id,
                    ordinal=1,
                    address="10.0.0.1",
                    custom_values={"rack": "A1", "installed": "2026-09-17"},
                )
            )
            db.commit()

            saved = replace_custom_fields(
                db,
                project_id=project.id,
                specs=[
                    CustomFieldSpec(field_id=installed.id, name="Commissioned", field_type="date"),
                    CustomFieldSpec(field_id=None, name="Cost", field_type="number"),
                ],
            )
            ip_record = db.scalar(select(IPAddress).where(IPAddress.project_id == project.id))
            fields = db.scalars(
                select(CustomField).where(CustomField.project_id == project.id).order_by(CustomField.position)
            ).all()

        self.assertEqual([field.id for field in saved], [field.id for field in fields])
        self.assertEqual([(field.name, field.field_type, field.position) for field in fields], [
            ("Commissioned", "date", 1),
            ("Cost", "number", 2),
        ])
        self.assertEqual(ip_record.custom_values, {"installed": "2026-09-17"})

    def test_replace_custom_fields_rejects_incompatible_type_without_changes(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            folder = Folder(name="Office")
            db.add(folder)
            db.flush()
            project = Project(folder_id=folder.id, name="LAN", cidr="10.0.0.0/24")
            db.add(project)
            db.flush()
            field = CustomField(project_id=project.id, name="Asset code", key="asset_code", field_type="text", position=1)
            db.add(field)
            db.flush()
            db.add(
                IPAddress(
                    project_id=project.id,
                    ordinal=1,
                    address="10.0.0.1",
                    custom_values={"asset_code": "not-a-number"},
                )
            )
            db.commit()

            with self.assertRaises(IncompatibleCustomFieldValueError):
                replace_custom_fields(
                    db,
                    project_id=project.id,
                    specs=[CustomFieldSpec(field_id=field.id, name="Asset code", field_type="number")],
                )
            db.rollback()
            saved_field = db.get(CustomField, field.id)

        self.assertEqual(saved_field.field_type, "text")
        self.assertEqual(saved_field.position, 1)

    def test_project_page_renders_columns_manager_modal(self) -> None:
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
            db.add(CustomField(project_id=project.id, name="Rack", key="rack", field_type="text", position=1))
            db.commit()
            project_id = project.id

        def override_db():
            with session_factory() as db:
                yield db

        app = create_app()
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[web_routes.require_user] = lambda: User(
            id=1,
            username="admin",
            is_admin=True,
            is_active=True,
        )
        response = TestClient(app).get(f"/projects/{project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'id="project-columns-modal-{project_id}"', response.text)
        self.assertIn(f'action="/projects/{project_id}/fields/configure"', response.text)
        self.assertIn("data-columns-save disabled", response.text)
        self.assertNotIn(f'action="/projects/{project_id}/fields"', response.text)


if __name__ == "__main__":
    unittest.main()
