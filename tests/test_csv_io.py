import unittest
import io
import csv
import zipfile

from openpyxl import Workbook, load_workbook
import pyzipper

from app.services.csv_io import (
    CSVImportError, build_zip_archive, parse_assets_csv, parse_assets_xlsx, render_project_xlsx, render_project_csv,
)
from app.models import IPAddress, Project


class CSVImportTest(unittest.TestCase):
    def test_shifted_header_is_rejected_in_both_formats(self) -> None:
        header = ["ip", "", "hostname", "os", "type", "comment"]
        row = ["10.0.0.1", "server", "Linux", "VM", "comment"]
        workbook = Workbook()
        workbook.active.append(header)
        workbook.active.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        csv_content = (";".join(header) + "\n" + ";".join(row)).encode()
        for parser, content in ((parse_assets_csv, csv_content), (parse_assets_xlsx, buffer.getvalue())):
            with self.subTest(parser=parser.__name__), self.assertRaises(CSVImportError):
                parser(content, max_addresses=256)

    def test_leading_blank_rows_preserve_source_row_numbers(self) -> None:
        content = b"\n;;;;\n IP ;hostname;os;type;comment\n10.0.0.1;server;Linux;VM;test\n"
        result = parse_assets_csv(content, max_addresses=256)
        self.assertEqual(result.rows[0].row_number, 4)
        self.assertEqual(result.rows[0].hostname, "server")

    def test_malformed_csv_is_a_user_error(self) -> None:
        with self.assertRaises(CSVImportError):
            parse_assets_csv(b'10.0.0.1;"unfinished', max_addresses=256)

    def test_zip_without_workbook_is_a_user_error(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("other.txt", "not a workbook")
        with self.assertRaises(CSVImportError):
            parse_assets_xlsx(buffer.getvalue(), max_addresses=256)

    def test_export_preserves_xlsx_text_and_neutralizes_csv_formulas(self) -> None:
        project = Project(name="LAN", cidr="10.0.0.0/24")
        for text in ("=1+1", "+1+1", "-1+1", "@SUM(1)", "  =1+1", "\t=1+1"):
            with self.subTest(text=text):
                record = IPAddress(address="10.0.0.1", hostname="server", os="Linux", asset_type="VM", comment=text)
                workbook = load_workbook(io.BytesIO(render_project_xlsx(project, [record])), data_only=False)
                self.assertEqual(workbook.active["E2"].data_type, "s")
                self.assertEqual(workbook.active["E2"].value, text)
                workbook.close()
                rows = list(csv.reader(io.StringIO(render_project_csv(project, [record])), delimiter=";"))
                self.assertEqual(rows[1][4], "'" + text)
                self.assertEqual(record.comment, text)

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

    def test_parse_assets_csv_accepts_missing_header(self) -> None:
        content = (
            "192.168.10.10;gw;RouterOS;Gateway;main\n"
            "192.168.10.20;server;Linux;Server;app\n"
        ).encode("utf-8")

        result = parse_assets_csv(content, max_addresses=256)

        self.assertEqual(result.cidr, "192.168.10.0/27")
        self.assertEqual(result.rows[0].row_number, 1)
        self.assertEqual(result.rows[0].hostname, "gw")

    def test_parse_assets_csv_rejects_wrong_header(self) -> None:
        content = "address;hostname;os;type;comment\n192.168.1.10;gw;;;;\n".encode("utf-8")

        with self.assertRaises(CSVImportError):
            parse_assets_csv(content, max_addresses=256)

    def test_parse_assets_csv_error_mentions_row_and_column(self) -> None:
        content = "ip;hostname;os;type;comment\n;gw;RouterOS;Gateway;main\n".encode("utf-8")

        with self.assertRaises(CSVImportError) as context:
            parse_assets_csv(content, max_addresses=256)

        self.assertIn("Строка 2, колонка ip", str(context.exception))

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
        worksheet.append(["ip", "hostname", "os", "type", "comment"])
        worksheet.append(["10.10.10.10", "cam-01", "", "Camera", "warehouse"])
        worksheet.append(["10.10.10.11", "cam-02", "", "Camera", "office"])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = parse_assets_xlsx(buffer.getvalue(), max_addresses=256)

        self.assertEqual(result.cidr, "10.10.10.10/31")
        self.assertEqual(result.rows[0].asset_type, "Camera")

    def test_parse_assets_xlsx_accepts_missing_header(self) -> None:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["10.10.10.10", "cam-01", "", "Camera", "warehouse"])
        worksheet.append(["10.10.10.11", "cam-02", "", "Camera", "office"])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = parse_assets_xlsx(buffer.getvalue(), max_addresses=256)

        self.assertEqual(result.cidr, "10.10.10.10/31")
        self.assertEqual(result.rows[0].row_number, 1)
        self.assertEqual(result.rows[0].asset_type, "Camera")

    def test_render_project_xlsx_contains_asset_fields(self) -> None:
        content = render_project_xlsx(
            Project(id=1, folder_id=1, name="LAN", cidr="192.168.1.0/24"),
            [
                IPAddress(
                    id=1,
                    project_id=1,
                    ordinal=1,
                    address="192.168.1.10",
                    hostname="gw",
                    asset_type="Gateway",
                )
            ],
        )

        result = parse_assets_xlsx(content, max_addresses=256)

        self.assertEqual(result.rows[0].address, "192.168.1.10")
        self.assertEqual(result.rows[0].asset_type, "Gateway")

    def test_build_password_zip_can_be_read_by_stdlib(self) -> None:
        archive = build_zip_archive({"asset.csv": b"ip;hostname\n192.168.1.10;gw\n"}, password="secret")

        with pyzipper.AESZipFile(io.BytesIO(archive)) as zip_file:
            content = zip_file.read("asset.csv", pwd=b"secret")

        self.assertIn(b"192.168.1.10", content)


if __name__ == "__main__":
    unittest.main()
