import unittest

from app.services.csv_io import CSVImportError, parse_assets_csv


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


if __name__ == "__main__":
    unittest.main()
