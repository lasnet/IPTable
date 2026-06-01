import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Folder, Project
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


if __name__ == "__main__":
    unittest.main()
