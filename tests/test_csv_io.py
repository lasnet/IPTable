import unittest
import io

from openpyxl import Workbook
import pyzipper

from app.services.csv_io import CSVImportError, build_zip_archive, parse_assets_csv, parse_assets_xlsx, render_project_xlsx
from app.models import IPAddress, Project


class CSVImportTest(unittest.TestCase):
    def test_parse_assets_csv_detects_network(self) -> None:
        content = (
            "ip;hostname;os;type;comment;tags\n"
            "192.168.10.10;gw;RouterOS;Gateway;main;network,core\n"
            "192.168.10.20;server;Linux;Server;app;linux\n"
        ).encode("utf-8")

        result = parse_assets_csv(content, max_addresses=256)

        self.assertEqual(result.cidr, "192.168.10.0/27")
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.rows[0].hostname, "gw")
        self.assertEqual(result.rows[0].tags, ["network", "core"])

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

    def test_parse_assets_xlsx_detects_network(self) -> None:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["ip", "hostname", "os", "type", "comment", "tags"])
        worksheet.append(["10.10.10.10", "cam-01", "", "Camera", "warehouse", "video,edge"])
        worksheet.append(["10.10.10.11", "cam-02", "", "Camera", "office", "video"])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = parse_assets_xlsx(buffer.getvalue(), max_addresses=256)

        self.assertEqual(result.cidr, "10.10.10.10/31")
        self.assertEqual(result.rows[0].asset_type, "Camera")
        self.assertEqual(result.rows[0].tags, ["video", "edge"])

    def test_render_project_xlsx_contains_tags(self) -> None:
        content = render_project_xlsx(
            Project(id=1, folder_id=1, name="LAN", cidr="192.168.1.0/24"),
            [
                IPAddress(
                    id=1,
                    project_id=1,
                    ordinal=1,
                    address="192.168.1.10",
                    hostname="gw",
                    tags=["network", "core"],
                )
            ],
        )

        result = parse_assets_xlsx(content, max_addresses=256)

        self.assertEqual(result.rows[0].address, "192.168.1.10")
        self.assertEqual(result.rows[0].tags, ["network", "core"])

    def test_build_password_zip_can_be_read_by_stdlib(self) -> None:
        archive = build_zip_archive({"asset.csv": b"ip;hostname\n192.168.1.10;gw\n"}, password="secret")

        with pyzipper.AESZipFile(io.BytesIO(archive)) as zip_file:
            content = zip_file.read("asset.csv", pwd=b"secret")

        self.assertIn(b"192.168.1.10", content)


if __name__ == "__main__":
    unittest.main()
