import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models import Base, CustomField, Folder, IPAddress, Project
from app.web.routes import (
    _ip_record_filled_condition,
    _normalize_project_page_size,
    _pagination_context,
    _project_table_filter_conditions,
)


class ProjectPaginationTest(unittest.TestCase):
    def test_normalize_project_page_size_uses_allowed_values(self) -> None:
        settings = Settings(initial_admin_password="x", project_table_default_page_size=50)

        self.assertEqual(_normalize_project_page_size(100, settings), 100)
        self.assertEqual(_normalize_project_page_size(17, settings), 50)

    def test_pagination_context_clamps_page_and_calculates_window(self) -> None:
        pagination = _pagination_context(42, hide_empty=True, page=99, per_page=25, total_items=61)

        self.assertEqual(pagination["current_page"], 3)
        self.assertEqual(pagination["total_pages"], 3)
        self.assertEqual(pagination["start_item"], 51)
        self.assertEqual(pagination["end_item"], 61)
        self.assertEqual(pagination["offset"], 50)
        self.assertFalse(pagination["has_next"])
        self.assertTrue(pagination["has_prev"])

    def test_pagination_context_preserves_table_filters(self) -> None:
        pagination = _pagination_context(
            42,
            hide_empty=True,
            page=2,
            per_page=25,
            total_items=61,
            ping_status="ok",
            type_filter="VM",
            os_filter="Linux",
        )

        self.assertIn("ping_status=ok", pagination["prev_url"])
        self.assertIn("type_filter=VM", pagination["prev_url"])
        self.assertIn("os_filter=Linux", pagination["next_url"])

    def test_ip_record_filled_condition_counts_custom_values_without_rendering_all_rows(self) -> None:
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
            custom_field = CustomField(project_id=project.id, name="Rack", key="rack", position=1)
            db.add(custom_field)
            db.add_all(
                [
                    IPAddress(project_id=project.id, ordinal=1, address="10.0.0.1"),
                    IPAddress(project_id=project.id, ordinal=2, address="10.0.0.2", hostname="gw"),
                    IPAddress(project_id=project.id, ordinal=3, address="10.0.0.3", custom_values={"rack": ""}),
                    IPAddress(project_id=project.id, ordinal=4, address="10.0.0.4", custom_values={"rack": "A1"}),
                    IPAddress(project_id=project.id, ordinal=5, address="10.0.0.5", tags=["edge"]),
                ]
            )
            db.commit()

            filled_count = db.scalar(
                select(func.count(IPAddress.id)).where(
                    IPAddress.project_id == project.id,
                    _ip_record_filled_condition([custom_field]),
                )
            )

        self.assertEqual(filled_count, 3)

    def test_project_table_filter_conditions_limit_by_ping_type_and_os(self) -> None:
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
            db.add_all(
                [
                    IPAddress(
                        project_id=project.id,
                        ordinal=1,
                        address="10.0.0.1",
                        os="Linux",
                        asset_type="VM",
                        is_reachable=True,
                    ),
                    IPAddress(
                        project_id=project.id,
                        ordinal=2,
                        address="10.0.0.2",
                        os="Windows",
                        asset_type="VM",
                        is_reachable=True,
                    ),
                    IPAddress(
                        project_id=project.id,
                        ordinal=3,
                        address="10.0.0.3",
                        os="Linux",
                        asset_type="Printer",
                        is_reachable=False,
                    ),
                ]
            )
            db.commit()

            matched = db.scalars(
                select(IPAddress.address).where(
                    IPAddress.project_id == project.id,
                    *_project_table_filter_conditions(
                        ping_status="ok",
                        type_filter="VM",
                        os_filter="Linux",
                    ),
                )
            ).all()

        self.assertEqual(matched, ["10.0.0.1"])


if __name__ == "__main__":
    unittest.main()
