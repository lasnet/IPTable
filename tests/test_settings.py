import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.models import AppConfiguration, AppConfigurationAudit, Base, IntegrationCredential
from app.services.settings import RevisionConflict, current, initialize, replace_token, save_group, token_hash


class RuntimeConfigurationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)

    def test_legacy_import_is_once_and_preserves_token(self) -> None:
        old = Settings(_env_file=None, ping_interval_seconds=61, project_table_default_page_size=17,
                       integration_api_token="old-token")
        with self.factory() as db:
            initialize(db, old)
            self.assertEqual(current(db, old).ping_interval_seconds, 61)
            self.assertEqual(db.get(AppConfiguration, 1).project_table_default_page_size, 25)
            self.assertEqual(db.get(IntegrationCredential, 1).token_hash, token_hash("old-token"))
            initialize(db, Settings(_env_file=None, ping_interval_seconds=3600, integration_api_token="changed"))
            self.assertEqual(db.get(AppConfiguration, 1).ping_interval_seconds, 61)
            self.assertEqual(db.get(IntegrationCredential, 1).token_hash, token_hash("old-token"))

    def test_atomic_revision_and_audit(self) -> None:
        with self.factory() as db:
            initialize(db, Settings(_env_file=None))
            values = {"app_name": "Network", "interface_language": "EN",
                      "interface_theme": "dark", "project_table_default_page_size": "50"}
            self.assertEqual(current(db).interface_theme, "blue")
            with self.assertRaisesRegex(ValueError, "Invalid setting value"):
                save_group(db, "interface", values | {"interface_theme": "unknown"}, 1,
                           actor_id=None, actor_name="test")
            self.assertEqual(save_group(db, "interface", values, 1, actor_id=None, actor_name="test"), 2)
            self.assertEqual(current(db).app_name, "Network")
            self.assertEqual(current(db).interface_language, "EN")
            self.assertEqual(current(db).interface_theme, "dark")
            with self.assertRaises(RevisionConflict):
                save_group(db, "interface", values, 1, actor_id=None, actor_name="stale")
            audit = db.scalars(select(AppConfigurationAudit).order_by(AppConfigurationAudit.id)).all()
            self.assertEqual(len(audit), 2)
            self.assertEqual(audit[-1].changes["interface_theme"], ["blue", "dark"])

    def test_token_replacement_invalidates_old_version(self) -> None:
        with self.factory() as db:
            initialize(db, Settings(_env_file=None))
            token = replace_token(db, 1, 1)
            self.assertEqual(db.get(IntegrationCredential, 1).token_hash, token_hash(token))
            with self.assertRaises(RevisionConflict):
                replace_token(db, 1, 1)
            replace_token(db, 2, 1, disable=True)
            self.assertIsNone(db.get(IntegrationCredential, 1).token_hash)

    def test_invalid_group_does_not_change_revision(self) -> None:
        with self.factory() as db:
            initialize(db, Settings(_env_file=None))
            values = {"max_project_addresses": "0", "csv_import_max_bytes": "2097152"}
            with self.assertRaises(ValueError):
                save_group(db, "limits", values, 1, actor_id=None, actor_name="test")
            self.assertEqual(db.get(AppConfiguration, 1).revision, 1)
