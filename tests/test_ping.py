import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models import Base, Folder, PingJob, PingSchedule, Project
from app.services.ping import (
    PING_JOB_QUEUED,
    PING_JOB_RUNNING,
    PING_SCHEDULE_PROJECT,
    _claim_next_ping_job,
    _chunk_rows,
    _supports_postgres_advisory_locks,
    enqueue_due_ping_schedules,
    enqueue_project_ping,
    ensure_project_ping_schedule,
    requeue_stale_ping_jobs,
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

    def test_claim_next_ping_job_marks_job_running_on_sqlite(self) -> None:
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
            job = enqueue_project_ping(db, project.id, reason="test")
            job_id = job.id
            project_id = project.id

            claimed_job = _claim_next_ping_job(db)
            saved_job = db.get(PingJob, job_id)
            second_claim = _claim_next_ping_job(db)

        self.assertEqual(claimed_job, (job_id, project_id))
        self.assertEqual(saved_job.status, PING_JOB_RUNNING)
        self.assertIsNone(second_claim)

    def test_sqlite_does_not_use_postgres_advisory_locks(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            self.assertFalse(_supports_postgres_advisory_locks(db))

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

    def test_due_schedule_skips_when_scheduler_lock_is_busy(self) -> None:
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

            with patch("app.services.ping._try_ping_advisory_lock", return_value=False):
                enqueued = enqueue_due_ping_schedules(db, Settings(initial_admin_password="x"))
            job = db.scalar(select(PingJob).where(PingJob.project_id == project.id))

        self.assertEqual(enqueued, 0)
        self.assertIsNone(job)

    def test_requeue_stale_ping_jobs_moves_timed_out_running_jobs_to_queue(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            folder = Folder(name="Office")
            db.add(folder)
            db.flush()
            project = Project(folder_id=folder.id, name="LAN", cidr="10.0.0.0/24")
            db.add(project)
            db.flush()
            job = PingJob(
                project_id=project.id,
                status=PING_JOB_RUNNING,
                reason="test",
                run_after=datetime.utcnow() - timedelta(hours=3),
                started_at=datetime.utcnow() - timedelta(hours=3),
            )
            db.add(job)
            db.commit()

            requeued = requeue_stale_ping_jobs(
                db,
                Settings(initial_admin_password="x", ping_running_job_timeout_seconds=3600),
            )
            saved_job = db.get(PingJob, job.id)

        self.assertEqual(requeued, 1)
        self.assertEqual(saved_job.status, PING_JOB_QUEUED)
        self.assertIsNone(saved_job.started_at)
        self.assertIsNone(saved_job.finished_at)
        self.assertEqual(saved_job.error, "Requeued after worker timeout")

    def test_requeue_stale_ping_jobs_keeps_recent_running_jobs(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as db:
            folder = Folder(name="Office")
            db.add(folder)
            db.flush()
            project = Project(folder_id=folder.id, name="LAN", cidr="10.0.0.0/24")
            db.add(project)
            db.flush()
            job = PingJob(
                project_id=project.id,
                status=PING_JOB_RUNNING,
                reason="test",
                run_after=datetime.utcnow(),
                started_at=datetime.utcnow() - timedelta(minutes=10),
            )
            db.add(job)
            db.commit()

            requeued = requeue_stale_ping_jobs(
                db,
                Settings(initial_admin_password="x", ping_running_job_timeout_seconds=3600),
            )
            saved_job = db.get(PingJob, job.id)

        self.assertEqual(requeued, 0)
        self.assertEqual(saved_job.status, PING_JOB_RUNNING)


if __name__ == "__main__":
    unittest.main()
