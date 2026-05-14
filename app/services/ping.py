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


async def run_ping_pass(settings: Settings) -> int:
    started_at = datetime.utcnow()
    with SessionLocal() as db:
        db.query(Project).update({Project.last_ping_started_at: started_at})
        rows = db.execute(select(IPAddress.id, IPAddress.project_id, IPAddress.address)).all()
        db.commit()

    semaphore = asyncio.Semaphore(settings.ping_concurrency)

    async def guarded_ping(ip_id: int, project_id: int, address: str) -> PingResult:
        async with semaphore:
            return await ping_address(ip_id, project_id, address, settings.ping_timeout_seconds)

    results = await asyncio.gather(*(guarded_ping(ip_id, project_id, address) for ip_id, project_id, address in rows))
    valid_results = [result for result in results if result.probe_ok and result.reachable is not None]
    skipped_project_ids: set[int] = set()

    for project_id in {result.project_id for result in valid_results}:
        project_results = [result for result in valid_results if result.project_id == project_id]
        if len(project_results) >= 3 and not any(result.reachable for result in project_results):
            skipped_project_ids.add(project_id)
            logger.warning(
                "Ping pass found zero reachable hosts for project_id=%s across %s valid probes; keeping previous statuses",
                project_id,
                len(project_results),
            )

    results_to_update = [result for result in valid_results if result.project_id not in skipped_project_ids]

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

        db.query(Project).update({Project.last_ping_finished_at: datetime.utcnow()})
        db.commit()

    return len(results_to_update)


async def run_ping_scheduler(settings: Settings) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            await run_ping_pass(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ping scheduler pass failed")

        await asyncio.sleep(settings.ping_interval_seconds)
