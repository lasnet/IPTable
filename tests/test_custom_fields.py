import unittest

from app.services.custom_fields import make_custom_field_key, next_available_key


class CustomFieldServiceTest(unittest.TestCase):
    def test_make_custom_field_key(self) -> None:
        self.assertEqual(make_custom_field_key(" Rack Unit "), "rack_unit")

    def test_make_custom_field_key_fallback(self) -> None:
        self.assertEqual(make_custom_field_key("!!!"), "field")

    def test_next_available_key(self) -> None:
        self.assertEqual(next_available_key("rack", {"rack", "rack_2"}), "rack_3")


if __name__ == "__main__":
    unittest.main()
