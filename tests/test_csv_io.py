import unittest
import io

import pyzipper

from app.services.csv_io import CSVImportError, build_zip_archive, parse_assets_csv


class CSVImportTest(unittest.TestCase):
    def test_parse_assets_csv_detects_network(self) -> None:
        content = (
            "ip;hostname;os;type;comment\n"
            "192.168.10.10;gw;RouterOS;Gateway;main\n"
            "192.168.10.20;server;Linux;Server;app\n"
        ).encode("utf-8")

        result = parse_assets_csv(content, max_addresses=256)

        self.assertEqual(result.cidr, "192.168.10.0/27")
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0].hostname, "gw")

    def test_parse_assets_csv_rejects_wrong_header(self) -> None:
        content = "address;hostname;os;type;comment\n192.168.1.10;gw;;;;\n".encode("utf-8")

        with self.assertRaises(CSVImportError):
            parse_assets_csv(content, max_addresses=256)

    def test_parse_assets_csv_rejects_duplicate_ip(self) -> None:
        content = (
            "ip;hostname;os;type;comment\n"
            "192.168.1.10;one;;;;\n"
            "192.168.1.10;two;;;;\n"
        ).encode("utf-8")

        with self.assertRaises(CSVImportError):
            parse_assets_csv(content, max_addresses=256)

    def test_build_password_zip_can_be_read_by_stdlib(self) -> None:
        archive = build_zip_archive({"asset.csv": b"ip;hostname\n192.168.1.10;gw\n"}, password="secret")

        with pyzipper.AESZipFile(io.BytesIO(archive)) as zip_file:
            content = zip_file.read("asset.csv", pwd=b"secret")

        self.assertIn(b"192.168.1.10", content)


if __name__ == "__main__":
    unittest.main()
