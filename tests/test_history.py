import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, Folder, IPAddress, IPAddressHistory, Project, User
from app.services.history import build_field_change, record_ip_address_history


class HistoryServiceTest(unittest.TestCase):
    def test_build_field_change_ignores_equal_values(self) -> None:
        self.assertIsNone(build_field_change("hostname", "Hostname", "dc01", "dc01"))

    def test_record_ip_address_history_persists_changes(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            folder = Folder(name="Office")
            user = User(username="admin", password_hash="hash", is_admin=True)
            db.add_all([folder, user])
            db.flush()
            project = Project(folder_id=folder.id, name="LAN", cidr="10.0.0.0/24")
            db.add(project)
            db.flush()
            ip_record = IPAddress(project_id=project.id, ordinal=1, address="10.0.0.1")
            db.add(ip_record)
            db.flush()

            change = build_field_change("hostname", "Hostname", "", "gw01")
            count = record_ip_address_history(db, ip_record=ip_record, user=user, changes=[change])
            db.commit()

            history_item = db.scalar(select(IPAddressHistory).where(IPAddressHistory.ip_address_id == ip_record.id))

        self.assertEqual(count, 1)
        self.assertIsNotNone(history_item)
        self.assertEqual(history_item.address, "10.0.0.1")
        self.assertEqual(history_item.field_label, "Hostname")
        self.assertEqual(history_item.new_value, "gw01")


if __name__ == "__main__":
    unittest.main()
