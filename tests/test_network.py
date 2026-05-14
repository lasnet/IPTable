import unittest

from app.services.network import NetworkValidationError, iter_project_addresses, normalize_cidr


class NetworkServiceTest(unittest.TestCase):
    def test_normalize_cidr_accepts_host_bits(self) -> None:
        self.assertEqual(normalize_cidr("172.16.16.42/24", max_addresses=256), "172.16.16.0/24")

    def test_iter_project_addresses_includes_network_and_broadcast(self) -> None:
        addresses = list(iter_project_addresses("192.168.1.0/30"))
        self.assertEqual(
            addresses,
            [
                (1, "192.168.1.0"),
                (2, "192.168.1.1"),
                (3, "192.168.1.2"),
                (4, "192.168.1.3"),
            ],
        )

    def test_rejects_large_networks(self) -> None:
        with self.assertRaises(NetworkValidationError):
            normalize_cidr("10.0.0.0/8", max_addresses=4096)

    def test_rejects_ipv6(self) -> None:
        with self.assertRaises(NetworkValidationError):
            normalize_cidr("2001:db8::/64", max_addresses=4096)


if __name__ == "__main__":
    unittest.main()
