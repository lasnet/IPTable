import unittest

from app.models import IPAddress
from app.services.inventory import is_ip_record_empty


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


if __name__ == "__main__":
    unittest.main()
