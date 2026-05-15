import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models import Base, User
from app.services.auth import authenticate_user, bootstrap_initial_admin, hash_password, verify_password


class AuthServiceTest(unittest.TestCase):
    def test_password_hash_verifies_original_password(self) -> None:
        password_hash = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", password_hash))

    def test_password_hash_rejects_wrong_password(self) -> None:
        password_hash = hash_password("correct horse battery staple")
        self.assertFalse(verify_password("wrong password", password_hash))

    def test_admin_user_has_all_inventory_permissions(self) -> None:
        user = User(username="admin", password_hash="hash", is_admin=True)

        self.assertTrue(user.can_create_inventory)
        self.assertTrue(user.can_edit_inventory)
        self.assertTrue(user.can_delete_inventory)
        self.assertTrue(user.can_manage_project_columns)

    def test_regular_user_gets_only_explicit_permissions(self) -> None:
        user = User(username="engineer", password_hash="hash", can_create=True, can_edit=True)

        self.assertTrue(user.can_create_inventory)
        self.assertTrue(user.can_edit_inventory)
        self.assertFalse(user.can_delete_inventory)
        self.assertFalse(user.can_manage_project_columns)

    def test_bootstrap_initial_admin_demotes_previous_admin(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            db.add(
                User(
                    username="old-admin",
                    password_hash="hash",
                    is_admin=True,
                    can_create=True,
                    can_edit=True,
                    can_delete=True,
                    can_manage_columns=True,
                )
            )
            db.commit()

            bootstrap_initial_admin(
                db,
                Settings(initial_admin_username="env-admin", initial_admin_password="admin-password"),
            )

            old_admin = db.scalar(select(User).where(User.username == "old-admin"))
            env_admin = db.scalar(select(User).where(User.username == "env-admin"))

        self.assertIsNotNone(old_admin)
        self.assertIsNotNone(env_admin)
        self.assertFalse(old_admin.is_admin)
        self.assertFalse(old_admin.can_create)
        self.assertTrue(env_admin.is_admin)
        self.assertTrue(env_admin.can_create_inventory)

    def test_inactive_user_cannot_authenticate(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            db.add(
                User(
                    username="disabled",
                    password_hash=hash_password("valid-password"),
                    is_active=False,
                )
            )
            db.commit()

            self.assertIsNone(authenticate_user(db, "disabled", "valid-password"))


if __name__ == "__main__":
    unittest.main()
