import asyncio
import logging
import platform
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import SessionLocal
from app.models import IPAddress, Project

logger = logging.getLogger(__name__)


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
        pass_started_at = asyncio.get_running_loop().time()
        try:
            await run_ping_pass(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ping scheduler pass failed")

        elapsed = asyncio.get_running_loop().time() - pass_started_at
        await asyncio.sleep(max(0, settings.ping_interval_seconds - elapsed))
