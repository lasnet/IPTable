import unittest

from app.services.auth import hash_password, verify_password


class AuthServiceTest(unittest.TestCase):
    def test_password_hash_verifies_original_password(self) -> None:
        password_hash = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", password_hash))

    def test_password_hash_rejects_wrong_password(self) -> None:
        password_hash = hash_password("correct horse battery staple")
        self.assertFalse(verify_password("wrong password", password_hash))


if __name__ == "__main__":
    unittest.main()
