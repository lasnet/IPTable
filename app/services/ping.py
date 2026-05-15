import asyncio
import logging
import platform
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import SessionLocal
from app.models import Folder, IPAddress, PingJob, PingSchedule, Project

logger = logging.getLogger(__name__)
PING_JOB_QUEUED = "queued"
PING_JOB_RUNNING = "running"
PING_JOB_DONE = "done"
PING_JOB_FAILED = "failed"
PING_SCHEDULE_PROJECT = "project"
PING_SCHEDULE_FOLDER = "folder"


@dataclass(slots=True)
class PingResult:
    ip_id: int
    project_id: int
    reachable: bool | None
    latency_ms: float | None
    checked_at: datetime
    probe_ok: bool
    error: str = ""


_PING_TIME_RE = re.compile(r"time[=<]([0-9.]+)\s*ms")


def build_ping_command(address: str, timeout_seconds: int) -> list[str]:
    system = platform.system().lower()
    if system == "windows":
        return ["ping", "-n", "1", "-w", str(timeout_seconds * 1000), address]
    if system == "darwin":
        return ["ping", "-c", "1", "-W", str(timeout_seconds * 1000), address]
    return ["ping", "-c", "1", "-W", str(timeout_seconds), address]


async def ping_address(ip_id: int, project_id: int, address: str, timeout_seconds: int) -> PingResult:
    checked_at = datetime.utcnow()
    try:
        process = await asyncio.create_subprocess_exec(
            *build_ping_command(address, timeout_seconds),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds + 2)
    except FileNotFoundError as exc:
        return PingResult(ip_id, project_id, None, None, checked_at, False, str(exc))
    except (TimeoutError, asyncio.TimeoutError) as exc:
        return PingResult(ip_id, project_id, False, None, checked_at, True, str(exc))

    output = stdout.decode("utf-8", errors="ignore")
    error_output = stderr.decode("utf-8", errors="ignore").strip()
    latency = None
    match = _PING_TIME_RE.search(output)
    if match:
        latency = float(match.group(1))

    if process.returncode not in {0, 1}:
        return PingResult(ip_id, project_id, None, None, checked_at, False, error_output)

    return PingResult(ip_id, project_id, process.returncode == 0, latency, checked_at, True, error_output)


def _chunk_rows(rows: list[tuple[int, int, str]], batch_size: int) -> list[list[tuple[int, int, str]]]:
    return [rows[index:index + batch_size] for index in range(0, len(rows), batch_size)]


async def _probe_rows(rows: list[tuple[int, int, str]], settings: Settings) -> list[PingResult]:
    semaphore = asyncio.Semaphore(settings.ping_concurrency)

    async def guarded_ping(ip_id: int, project_id: int, address: str) -> PingResult:
        async with semaphore:
            return await ping_address(ip_id, project_id, address, settings.ping_timeout_seconds)

    results: list[PingResult] = []
    batches = _chunk_rows(rows, settings.ping_batch_size)
    for batch_index, batch in enumerate(batches):
        results.extend(
            await asyncio.gather(*(guarded_ping(ip_id, project_id, address) for ip_id, project_id, address in batch))
        )
        if settings.ping_batch_pause_seconds and batch_index < len(batches) - 1:
            await asyncio.sleep(settings.ping_batch_pause_seconds)

    return results


def _filter_safe_ping_updates(results: list[PingResult]) -> list[PingResult]:
    invalid_results = [result for result in results if not result.probe_ok]
    for result in invalid_results[:5]:
        logger.warning("Ping probe failed for ip_id=%s: %s", result.ip_id, result.error or "unknown error")

    return [result for result in results if result.probe_ok and result.reachable is not None]


def _apply_ping_results(results_to_update: list[PingResult], *, project_id: int | None = None) -> None:
    with SessionLocal() as db:
        for result in results_to_update:
            values = {
                IPAddress.is_reachable: result.reachable,
                IPAddress.last_checked_at: result.checked_at,
                IPAddress.ping_latency_ms: result.latency_ms,
            }
            if result.reachable:
                values[IPAddress.last_seen_at] = result.checked_at
            db.query(IPAddress).filter(IPAddress.id == result.ip_id).update(values)

        project_query = db.query(Project)
        if project_id is not None:
            project_query = project_query.filter(Project.id == project_id)
        project_query.update({Project.last_ping_finished_at: datetime.utcnow()})
        db.commit()


async def run_ping_project(project_id: int, settings: Settings) -> int:
    started_at = datetime.utcnow()
    with SessionLocal() as db:
        db.query(Project).filter(Project.id == project_id).update({Project.last_ping_started_at: started_at})
        rows = db.execute(
            select(IPAddress.id, IPAddress.project_id, IPAddress.address).where(IPAddress.project_id == project_id)
        ).all()
        db.commit()

    results = await _probe_rows(list(rows), settings)
    results_to_update = _filter_safe_ping_updates(results)
    _apply_ping_results(results_to_update, project_id=project_id)
    return len(results_to_update)


def _next_run(interval_seconds: int) -> datetime:
    return datetime.utcnow() + timedelta(seconds=interval_seconds)


def _has_active_ping_job(db, project_id: int) -> bool:
    existing = db.scalar(
        select(PingJob.id)
        .where(
            PingJob.project_id == project_id,
            PingJob.status.in_([PING_JOB_QUEUED, PING_JOB_RUNNING]),
        )
        .limit(1)
    )
    return existing is not None


def enqueue_project_ping(
    db,
    project_id: int,
    *,
    reason: str = "manual",
    run_after: datetime | None = None,
    commit: bool = True,
) -> PingJob | None:
    if _has_active_ping_job(db, project_id):
        return None

    job = PingJob(
        project_id=project_id,
        status=PING_JOB_QUEUED,
        reason=reason[:40],
        run_after=run_after or datetime.utcnow(),
    )
    db.add(job)
    if commit:
        db.commit()
        db.refresh(job)
    return job


def ensure_project_ping_schedule(db, project_id: int, settings: Settings) -> PingSchedule:
    schedule = db.scalar(
        select(PingSchedule).where(
            PingSchedule.scope == PING_SCHEDULE_PROJECT,
            PingSchedule.project_id == project_id,
        )
    )
    if schedule is not None:
        return schedule

    schedule = PingSchedule(
        scope=PING_SCHEDULE_PROJECT,
        project_id=project_id,
        enabled=True,
        interval_seconds=settings.ping_interval_seconds,
        next_run_at=_next_run(settings.ping_interval_seconds),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


def ensure_default_project_schedules(db, settings: Settings) -> int:
    project_ids = db.scalars(select(Project.id).order_by(Project.id.asc())).all()
    created_count = 0
    for project_id in project_ids:
        schedule = db.scalar(
            select(PingSchedule.id).where(
                PingSchedule.scope == PING_SCHEDULE_PROJECT,
                PingSchedule.project_id == project_id,
            )
        )
        if schedule is None:
            db.add(
                PingSchedule(
                    scope=PING_SCHEDULE_PROJECT,
                    project_id=project_id,
                    enabled=True,
                    interval_seconds=settings.ping_interval_seconds,
                    next_run_at=_next_run(settings.ping_interval_seconds),
                )
            )
            created_count += 1

    if created_count:
        db.commit()
    return created_count


def set_project_ping_schedule(
    db,
    project_id: int,
    *,
    enabled: bool,
    interval_seconds: int,
) -> PingSchedule:
    schedule = db.scalar(
        select(PingSchedule).where(
            PingSchedule.scope == PING_SCHEDULE_PROJECT,
            PingSchedule.project_id == project_id,
        )
    )
    if schedule is None:
        schedule = PingSchedule(scope=PING_SCHEDULE_PROJECT, project_id=project_id)
        db.add(schedule)

    schedule.enabled = enabled
    schedule.interval_seconds = interval_seconds
    schedule.next_run_at = _next_run(interval_seconds) if enabled else None
    db.commit()
    db.refresh(schedule)
    return schedule


def set_folder_ping_schedule(
    db,
    folder_id: int,
    *,
    enabled: bool,
    interval_seconds: int,
) -> PingSchedule:
    schedule = db.scalar(
        select(PingSchedule).where(
            PingSchedule.scope == PING_SCHEDULE_FOLDER,
            PingSchedule.folder_id == folder_id,
        )
    )
    if schedule is None:
        schedule = PingSchedule(scope=PING_SCHEDULE_FOLDER, folder_id=folder_id)
        db.add(schedule)

    schedule.enabled = enabled
    schedule.interval_seconds = interval_seconds
    schedule.next_run_at = _next_run(interval_seconds) if enabled else None
    db.commit()
    db.refresh(schedule)
    return schedule


def enqueue_due_ping_schedules(db, settings: Settings) -> int:
    now = datetime.utcnow()
    schedules = db.scalars(
        select(PingSchedule)
        .where(PingSchedule.enabled.is_(True), PingSchedule.next_run_at.is_not(None), PingSchedule.next_run_at <= now)
        .order_by(PingSchedule.next_run_at.asc(), PingSchedule.id.asc())
    ).all()
    enqueued_count = 0

    for schedule in schedules:
        project_ids: list[int] = []
        if schedule.scope == PING_SCHEDULE_PROJECT and schedule.project_id is not None:
            project_ids = [schedule.project_id]
        elif schedule.scope == PING_SCHEDULE_FOLDER and schedule.folder_id is not None:
            if db.get(Folder, schedule.folder_id) is not None:
                project_ids = db.scalars(
                    select(Project.id).where(Project.folder_id == schedule.folder_id).order_by(Project.id.asc())
                ).all()

        for project_id in project_ids:
            if enqueue_project_ping(db, project_id, reason=f"{schedule.scope}-schedule", commit=False) is not None:
                enqueued_count += 1

        schedule.last_run_at = now
        schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)

    if schedules or enqueued_count:
        db.commit()
    return enqueued_count


async def run_next_ping_job(settings: Settings) -> bool:
    now = datetime.utcnow()
    with SessionLocal() as db:
        job = db.scalar(
            select(PingJob)
            .where(PingJob.status == PING_JOB_QUEUED, PingJob.run_after <= now)
            .order_by(PingJob.run_after.asc(), PingJob.id.asc())
            .limit(1)
        )
        if job is None:
            return False

        job.status = PING_JOB_RUNNING
        job.started_at = now
        job.error = ""
        job_id = job.id
        project_id = job.project_id
        db.commit()

    try:
        await run_ping_project(project_id, settings)
    except Exception as exc:
        logger.exception("Ping job failed for project_id=%s", project_id)
        with SessionLocal() as db:
            failed_job = db.get(PingJob, job_id)
            if failed_job is not None:
                failed_job.status = PING_JOB_FAILED
                failed_job.finished_at = datetime.utcnow()
                failed_job.error = str(exc)[:4000]
                db.commit()
        return True

    with SessionLocal() as db:
        finished_job = db.get(PingJob, job_id)
        if finished_job is not None:
            finished_job.status = PING_JOB_DONE
            finished_job.finished_at = datetime.utcnow()
            finished_job.error = ""
            db.commit()
    return True


async def run_ping_pass(settings: Settings) -> int:
    with SessionLocal() as db:
        project_ids = db.scalars(select(Project.id).order_by(Project.id.asc())).all()

    updated_count = 0
    for project_index, project_id in enumerate(project_ids):
        updated_count += await run_ping_project(project_id, settings)
        if settings.ping_project_pause_seconds and project_index < len(project_ids) - 1:
            await asyncio.sleep(settings.ping_project_pause_seconds)

    return updated_count


async def run_ping_scheduler(settings: Settings) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            with SessionLocal() as db:
                ensure_default_project_schedules(db, settings)
                enqueue_due_ping_schedules(db, settings)

            processed = await run_next_ping_job(settings)
            if processed and settings.ping_project_pause_seconds:
                await asyncio.sleep(settings.ping_project_pause_seconds)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ping worker iteration failed")

        await asyncio.sleep(settings.ping_queue_poll_seconds)
