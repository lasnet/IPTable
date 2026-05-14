import unittest
from unittest.mock import patch

from app.services.ping import build_ping_command


class PingServiceTest(unittest.TestCase):
    def test_build_ping_command_for_linux(self) -> None:
        with patch("platform.system", return_value="Linux"):
            self.assertEqual(build_ping_command("10.10.10.1", 2), ["ping", "-c", "1", "-W", "2", "10.10.10.1"])

    def test_build_ping_command_for_darwin_uses_milliseconds(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            self.assertEqual(build_ping_command("10.10.10.1", 2), ["ping", "-c", "1", "-W", "2000", "10.10.10.1"])


if __name__ == "__main__":
    unittest.main()
