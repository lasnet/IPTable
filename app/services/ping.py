import asyncio
import logging
import platform
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import bindparam, case, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import Settings
from app.core.database import SessionLocal
from app.models import ACTIVE_PING_JOB, IPAddress, PingJob, PingSchedule, Project

logger = logging.getLogger(__name__)
PING_JOB_QUEUED = "queued"
PING_JOB_RUNNING = "running"
PING_JOB_DONE = "done"
PING_JOB_FAILED = "failed"
PING_SCHEDULE_PROJECT = "project"
PING_SCHEDULE_FOLDER = "folder"
PING_ADVISORY_LOCK_NAMESPACE = 0x495054
PING_SCHEDULER_LOCK_ID = 0
MAX_SCHEDULER_BACKOFF_SECONDS = 30


@dataclass(slots=True)
class PingResult:
    ip_id: int
    project_id: int
    reachable: bool | None
    latency_ms: float | None
    checked_at: datetime
    probe_ok: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class PingClaim:
    job_id: int
    project_id: int
    token: str
    run_after: datetime
    started_at: datetime


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
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *build_ping_command(address, timeout_seconds),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds + 2)
    except OSError as exc:
        return PingResult(ip_id, project_id, None, None, checked_at, False, str(exc))
    except (TimeoutError, asyncio.TimeoutError) as exc:
        return PingResult(ip_id, project_id, False, None, checked_at, True, str(exc))
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()

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
        tasks = [asyncio.create_task(guarded_ping(*row)) for row in batch]
        try:
            results.extend(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if settings.ping_batch_pause_seconds and batch_index < len(batches) - 1:
            await asyncio.sleep(settings.ping_batch_pause_seconds)

    return results


def _filter_safe_ping_updates(results: list[PingResult]) -> list[PingResult]:
    invalid_results = [result for result in results if not result.probe_ok]
    for result in invalid_results[:5]:
        logger.warning("Ping probe failed for ip_id=%s: %s", result.ip_id, result.error or "unknown error")

    return [result for result in results if result.probe_ok and result.reachable is not None]


def _apply_ping_results(db, project_id: int, results: list[PingResult]) -> None:
    table = IPAddress.__table__
    reachable = bindparam("probe_reachable", type_=table.c.is_reachable.type)
    checked_at = bindparam("probe_checked_at", type_=table.c.last_checked_at.type)
    statement = table.update().where(table.c.project_id == project_id, table.c.id == bindparam("ip_id")).values(
        is_reachable=reachable,
        last_checked_at=checked_at,
        ping_latency_ms=bindparam("probe_latency"),
        last_seen_at=case((reachable, checked_at), else_=table.c.last_seen_at),
    )
    parameters = [
        {"ip_id": item.ip_id, "probe_reachable": item.reachable, "probe_checked_at": item.checked_at,
         "probe_latency": item.latency_ms}
        for item in sorted(results, key=lambda item: item.ip_id)
    ]
    if parameters:
        db.execute(statement, parameters)


def _current_claim(claim: PingClaim):
    return (
        PingJob.id == claim.job_id,
        PingJob.status == PING_JOB_RUNNING,
        PingJob.claim_token == claim.token,
    )


def _finish_ping_job(claim: PingClaim, results: list[PingResult], error: str = "") -> bool:
    valid_results = _filter_safe_ping_updates(results)
    if results and not valid_results:
        error = "No valid ping probes"
    with SessionLocal() as db:
        # Project deletion takes this lock before deleting jobs and addresses.
        if db.scalar(select(Project.id).where(Project.id == claim.project_id).with_for_update()) is None:
            return False
        now = datetime.utcnow()
        finished = db.execute(update(PingJob).where(
            *_current_claim(claim), PingJob.lease_expires_at > now,
        ).values(
            status=PING_JOB_FAILED if error else PING_JOB_DONE,
            finished_at=now, error=error[:4000], lease_expires_at=None, claim_token=None,
        ))
        if finished.rowcount != 1:
            return False
        _apply_ping_results(db, claim.project_id, valid_results)
        if not error:
            db.execute(update(Project).where(Project.id == claim.project_id).values(last_ping_finished_at=now))
        db.commit()

    logger.info(
        "Ping job finished job_id=%s project_id=%s status=%s queue_wait_seconds=%.3f duration_seconds=%.3f "
        "reachable=%s unreachable=%s probe_errors=%s",
        claim.job_id, claim.project_id, PING_JOB_FAILED if error else PING_JOB_DONE,
        max(0, (claim.started_at - claim.run_after).total_seconds()),
        (datetime.utcnow() - claim.started_at).total_seconds(),
        sum(result.reachable is True for result in valid_results),
        sum(result.reachable is False for result in valid_results),
        len(results) - len(valid_results),
    )
    return True


def _next_run(interval_seconds: int) -> datetime:
    return datetime.utcnow() + timedelta(seconds=interval_seconds)


def _supports_postgres_advisory_locks(db) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def _try_ping_advisory_lock(db, lock_id: int) -> bool:
    if not _supports_postgres_advisory_locks(db):
        return True

    return bool(
        db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:namespace, :lock_id)"),
            {"namespace": PING_ADVISORY_LOCK_NAMESPACE, "lock_id": lock_id},
        )
    )


def _insert_for(db):
    return postgres_insert if _supports_postgres_advisory_locks(db) else sqlite_insert


def enqueue_project_ping(
    db,
    project_id: int,
    *,
    reason: str = "manual",
    run_after: datetime | None = None,
    commit: bool = True,
) -> PingJob | None:
    statement = _insert_for(db)(PingJob).values(
        project_id=project_id,
        status=PING_JOB_QUEUED,
        reason=reason[:40],
        run_after=run_after or datetime.utcnow(),
    )
    job = db.scalar(statement.on_conflict_do_nothing(
        index_elements=[PingJob.project_id], index_where=ACTIVE_PING_JOB,
    ).returning(PingJob))
    if commit:
        db.commit()
    return job


def ensure_project_ping_schedule(db, project_id: int, settings: Settings, *, commit: bool = True) -> PingSchedule:
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
    if commit:
        db.commit()
        db.refresh(schedule)
    return schedule


def ensure_default_project_schedules(db, settings: Settings) -> int:
    if not _try_ping_advisory_lock(db, PING_SCHEDULER_LOCK_ID):
        return 0

    interval = settings.ping_interval_seconds
    missing = select(
        Project.id, literal(PING_SCHEDULE_PROJECT), literal(True), literal(interval), literal(_next_run(interval)),
    ).where(~select(PingSchedule.id).where(PingSchedule.project_id == Project.id).exists()).order_by(Project.id)
    statement = _insert_for(db)(PingSchedule).from_select(
        ["project_id", "scope", "enabled", "interval_seconds", "next_run_at"], missing,
    )
    inserted = db.scalars(statement.on_conflict_do_nothing(
        index_elements=[PingSchedule.project_id],
    ).returning(PingSchedule.id)).all()
    if inserted:
        db.commit()
    return len(inserted)


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


def enqueue_due_ping_schedules(db) -> int:
    if not _try_ping_advisory_lock(db, PING_SCHEDULER_LOCK_ID):
        return 0

    now = datetime.utcnow()
    schedules = db.scalars(
        select(PingSchedule)
        .where(PingSchedule.enabled.is_(True), PingSchedule.next_run_at.is_not(None), PingSchedule.next_run_at <= now)
        .order_by(PingSchedule.next_run_at.asc(), PingSchedule.id.asc())
    ).all()
    reasons_by_project: dict[int, str] = {}
    for schedule in schedules:
        project_ids: list[int] = []
        if schedule.scope == PING_SCHEDULE_PROJECT and schedule.project_id is not None:
            project_ids = [schedule.project_id]
        elif schedule.scope == PING_SCHEDULE_FOLDER and schedule.folder_id is not None:
            project_ids = db.scalars(
                select(Project.id).where(Project.folder_id == schedule.folder_id).order_by(Project.id.asc())
            ).all()

        for project_id in project_ids:
            reasons_by_project.setdefault(project_id, f"{schedule.scope}-schedule")

        schedule.last_run_at = now
        schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)

    enqueued_count = sum(
        enqueue_project_ping(db, project_id, reason=reasons_by_project[project_id], commit=False) is not None
        for project_id in sorted(reasons_by_project)
    )
    if schedules:
        db.commit()
    return enqueued_count


def requeue_stale_ping_jobs(db, settings: Settings) -> int:
    if not _try_ping_advisory_lock(db, PING_SCHEDULER_LOCK_ID):
        return 0

    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=settings.ping_running_job_timeout_seconds)
    requeued_count = (
        db.query(PingJob)
        .filter(
            PingJob.status == PING_JOB_RUNNING,
            or_(PingJob.started_at.is_(None), PingJob.lease_expires_at <= now,
                (PingJob.lease_expires_at.is_(None) & (PingJob.started_at <= stale_before))),
        )
        .update(
            {
                PingJob.status: PING_JOB_QUEUED,
                PingJob.run_after: now,
                PingJob.started_at: None,
                PingJob.lease_expires_at: None,
                PingJob.claim_token: None,
                PingJob.finished_at: None,
                PingJob.error: "Requeued after worker timeout",
            },
            synchronize_session=False,
        )
    )
    if requeued_count:
        db.commit()
    return requeued_count


def _claim_next_ping_job(db, timeout_seconds: int = 7200) -> PingClaim | None:
    now = datetime.utcnow()
    candidate = (
        select(PingJob.id)
        .where(PingJob.status == PING_JOB_QUEUED, PingJob.run_after <= now)
        .order_by(PingJob.run_after.asc(), PingJob.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    token = uuid4().hex
    claimed = db.execute(update(PingJob).where(
        PingJob.id == candidate.scalar_subquery(), PingJob.status == PING_JOB_QUEUED,
    ).values(
        status=PING_JOB_RUNNING, started_at=now, claim_token=token,
        lease_expires_at=now + timedelta(seconds=timeout_seconds), error="",
    ).returning(PingJob.id, PingJob.project_id, PingJob.run_after)).one_or_none()
    if claimed is None:
        db.rollback()
        return None
    db.commit()
    return PingClaim(claimed.id, claimed.project_id, token, claimed.run_after, now)


def _renew_claim(claim: PingClaim, timeout_seconds: int) -> bool:
    with SessionLocal() as db:
        now = datetime.utcnow()
        renewed = db.execute(update(PingJob).where(
            *_current_claim(claim), PingJob.lease_expires_at > now,
        ).values(lease_expires_at=now + timedelta(seconds=timeout_seconds)))
        db.commit()
        return renewed.rowcount == 1


async def _probe_with_lease(claim: PingClaim, rows: list[tuple[int, int, str]], settings: Settings) -> list[PingResult] | None:
    timeout = settings.ping_running_job_timeout_seconds
    task = asyncio.create_task(_probe_rows(rows, settings))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=timeout / 3)
            if done:
                return task.result()
            if not _renew_claim(claim, timeout):
                return None
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def run_next_ping_job(settings: Settings) -> bool:
    with SessionLocal() as db:
        claim = _claim_next_ping_job(db, settings.ping_running_job_timeout_seconds)
    if claim is None:
        return False
    logger.info("Ping job started job_id=%s project_id=%s", claim.job_id, claim.project_id)
    try:
        with SessionLocal() as db:
            db.execute(update(Project).where(
                Project.id == claim.project_id,
                or_(Project.last_ping_started_at.is_(None), Project.last_ping_started_at < claim.started_at),
            ).values(last_ping_started_at=claim.started_at))
            rows = db.execute(
                select(IPAddress.id, IPAddress.project_id, IPAddress.address).where(IPAddress.project_id == claim.project_id)
            ).all()
            db.commit()
        results = await _probe_with_lease(claim, list(rows), settings)
    except Exception as exc:
        logger.exception("Ping job failed job_id=%s project_id=%s", claim.job_id, claim.project_id)
        finished = _finish_ping_job(claim, [], error=str(exc) or type(exc).__name__)
    else:
        finished = results is not None and _finish_ping_job(claim, results)
    if not finished:
        logger.warning("Ping claim lost job_id=%s project_id=%s", claim.job_id, claim.project_id)
    return True


async def run_ping_scheduler(settings: Settings) -> None:
    await asyncio.sleep(5)
    next_poll_at = 0.0
    last_poll_seconds = settings.ping_queue_poll_seconds
    backoff = 1
    while True:
        try:
            with SessionLocal() as db:
                from app.services.settings import current
                settings = current(db, settings)
                if settings.ping_queue_poll_seconds != last_poll_seconds:
                    next_poll_at = min(next_poll_at, time.monotonic() + settings.ping_queue_poll_seconds)
                    last_poll_seconds = settings.ping_queue_poll_seconds
                ensure_default_project_schedules(db, settings)
                requeued_count = requeue_stale_ping_jobs(db, settings)
                if requeued_count:
                    logger.warning("Requeued stale ping jobs: %s", requeued_count)
                enqueue_due_ping_schedules(db)

            if time.monotonic() >= next_poll_at:
                processed = await run_next_ping_job(settings)
                delay = settings.ping_project_pause_seconds if processed else settings.ping_queue_poll_seconds
                next_poll_at = time.monotonic() + delay
            backoff = 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ping worker iteration failed; retry in %s seconds", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_SCHEDULER_BACKOFF_SECONDS)
            continue

        await asyncio.sleep(min(5, max(0.05, next_poll_at - time.monotonic())))
