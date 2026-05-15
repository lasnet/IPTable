import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models import Base, Folder, PingJob, PingSchedule, Project
from app.services.ping import (
    PING_JOB_QUEUED,
    PING_SCHEDULE_PROJECT,
    _chunk_rows,
    enqueue_due_ping_schedules,
    enqueue_project_ping,
    ensure_project_ping_schedule,
    build_ping_command,
)


class PingServiceTest(unittest.TestCase):
    def test_build_ping_command_for_linux(self) -> None:
        with patch("platform.system", return_value="Linux"):
            self.assertEqual(build_ping_command("10.10.10.1", 2), ["ping", "-c", "1", "-W", "2", "10.10.10.1"])

    def test_build_ping_command_for_darwin_uses_milliseconds(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            self.assertEqual(build_ping_command("10.10.10.1", 2), ["ping", "-c", "1", "-W", "2000", "10.10.10.1"])

    def test_chunk_rows_limits_ping_batches(self) -> None:
        rows = [(index, 1, f"10.0.0.{index}") for index in range(1, 8)]

        self.assertEqual([len(batch) for batch in _chunk_rows(rows, 3)], [3, 3, 1])

    def test_enqueue_project_ping_skips_duplicate_active_job(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            folder = Folder(name="Office")
            db.add(folder)
            db.flush()
            project = Project(folder_id=folder.id, name="LAN", cidr="10.0.0.0/24")
            db.add(project)
            db.commit()

            first_job = enqueue_project_ping(db, project.id, reason="test")
            duplicate_job = enqueue_project_ping(db, project.id, reason="test")
            queued_count = len(db.scalars(select(PingJob).where(PingJob.status == PING_JOB_QUEUED)).all())

        self.assertIsNotNone(first_job)
        self.assertIsNone(duplicate_job)
        self.assertEqual(queued_count, 1)

    def test_due_schedule_enqueues_ping_job(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            folder = Folder(name="Office")
            db.add(folder)
            db.flush()
            project = Project(folder_id=folder.id, name="LAN", cidr="10.0.0.0/24")
            db.add(project)
            db.commit()
            schedule = ensure_project_ping_schedule(db, project.id, Settings(initial_admin_password="x"))
            schedule.next_run_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()

            enqueued = enqueue_due_ping_schedules(db, Settings(initial_admin_password="x"))
            job = db.scalar(select(PingJob).where(PingJob.project_id == project.id))
            saved_schedule = db.scalar(
                select(PingSchedule).where(
                    PingSchedule.scope == PING_SCHEDULE_PROJECT,
                    PingSchedule.project_id == project.id,
                )
            )

        self.assertEqual(enqueued, 1)
        self.assertIsNotNone(job)
        self.assertEqual(job.status, PING_JOB_QUEUED)
        self.assertGreater(saved_schedule.next_run_at, datetime.utcnow())


if __name__ == "__main__":
    unittest.main()
