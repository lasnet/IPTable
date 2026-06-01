import unittest

from app.core.config import Settings
from app.services.i18n import html_language, translate, translate_error_message


class InterfaceLanguageTest(unittest.TestCase):
    def test_settings_normalizes_supported_interface_language(self) -> None:
        settings = Settings(initial_admin_password="x", interface_language="en")

        self.assertEqual(settings.interface_language, "EN")
        self.assertEqual(html_language(settings.interface_language), "en")

    def test_settings_falls_back_to_russian_for_unknown_language(self) -> None:
        settings = Settings(initial_admin_password="x", interface_language="de")

        self.assertEqual(settings.interface_language, "RU")
        self.assertEqual(html_language(settings.interface_language), "ru")

    def test_translate_uses_english_dictionary(self) -> None:
        self.assertEqual(translate("EN", "nav.logout"), "Logout")
        self.assertEqual(
            translate("EN", "login.rate_limited", minutes=3),
            "Too many login attempts. Try again in 3 min.",
        )

    def test_translate_error_message_handles_service_errors(self) -> None:
        self.assertEqual(
            translate_error_message("EN", "Строка 7: поле ip обязательно"),
            "Row 7: ip field is required",
        )
        self.assertEqual(
            translate_error_message("EN", "Подсеть содержит 8192 адресов, лимит: 4096"),
            "Subnet contains 8192 addresses, limit: 4096",
        )


if __name__ == "__main__":
    unittest.main()
