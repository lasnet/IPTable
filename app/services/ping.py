import asyncio
import logging
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
    reachable: bool
    latency_ms: float | None
    checked_at: datetime


_PING_TIME_RE = re.compile(r"time[=<]([0-9.]+)\s*ms")


async def ping_address(ip_id: int, address: str, timeout_seconds: int) -> PingResult:
    checked_at = datetime.utcnow()
    try:
        process = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            "1",
            "-W",
            str(timeout_seconds),
            address,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds + 2)
    except (FileNotFoundError, TimeoutError, asyncio.TimeoutError):
        return PingResult(ip_id=ip_id, reachable=False, latency_ms=None, checked_at=checked_at)

    output = stdout.decode("utf-8", errors="ignore")
    latency = None
    match = _PING_TIME_RE.search(output)
    if match:
        latency = float(match.group(1))

    return PingResult(ip_id=ip_id, reachable=process.returncode == 0, latency_ms=latency, checked_at=checked_at)


async def run_ping_pass(settings: Settings) -> int:
    started_at = datetime.utcnow()
    with SessionLocal() as db:
        db.query(Project).update({Project.last_ping_started_at: started_at})
        rows = db.execute(select(IPAddress.id, IPAddress.address)).all()
        db.commit()

    semaphore = asyncio.Semaphore(settings.ping_concurrency)

    async def guarded_ping(ip_id: int, address: str) -> PingResult:
        async with semaphore:
            return await ping_address(ip_id, address, settings.ping_timeout_seconds)

    results = await asyncio.gather(*(guarded_ping(ip_id, address) for ip_id, address in rows))

    with SessionLocal() as db:
        for result in results:
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

    return len(results)


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
