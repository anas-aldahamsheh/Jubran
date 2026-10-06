"""Request rate limiting (abuse protection for logins, the assistant, orders…).

Counters live in the database so every server worker shares them. Each limit is
a sliding window approximated from the current and previous fixed windows,
which avoids the burst a plain fixed window allows at its boundary.
"""
import hashlib
import math
import random
import time
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.infrastructure.db.models import RateLimitCounterModel
from jubran.settings import settings


@dataclass(frozen=True)
class Limit:
    hits: int
    window_seconds: int


# One place to tune every limit.
LIMITS: dict[str, Limit] = {
    "login_ip": Limit(20, 300),           # any account, from one address
    "login_account": Limit(5, 60),        # one account, from anywhere
    "login_account_hour": Limit(30, 3600),
    "session_start_ip": Limit(30, 60),    # QR scans from one address
    "assistant_customer": Limit(10, 60),  # chat + voice turns of one guest
    "assistant_table": Limit(40, 300),    # all guests of one table visit
    "assistant_audio": Limit(6, 60),      # dictation / audio turns (costly uploads)
    "order_submit": Limit(5, 60),
    "order_amend": Limit(30, 60),         # +/- clicks on a sent order
    "customer_action": Limit(10, 60),     # service requests, complaints, feedback, cancel
    "admin_qr": Limit(30, 60),
    # Public demo only (PUBLIC_DEMO): seats from one address, and the assistant's day for everyone.
    "demo_visit_ip": Limit(6, 600),
    "assistant_demo_day": Limit(settings.PUBLIC_DEMO_ASSISTANT_TURNS_PER_DAY, 86400),
}

_RETENTION_SECONDS = 2 * 86400


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("RATE_LIMITED")
        self.retry_after = retry_after


def _bucket_key(bucket: str, subject: str) -> str:
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:40]
    return f"{bucket}:{digest}"


async def _increment(db: AsyncSession, key: str, window_start: int) -> int:
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(RateLimitCounterModel).values(bucket_key=key, window_start=window_start, hits=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RateLimitCounterModel.bucket_key, RateLimitCounterModel.window_start],
        set_={"hits": RateLimitCounterModel.hits + 1},
    ).returning(RateLimitCounterModel.hits)
    return (await db.execute(stmt)).scalar_one()


async def hit(db: AsyncSession, bucket: str, subject: str, now: float | None = None) -> None:
    """Count one request; raise RateLimitExceeded once the limit is passed.

    Commits immediately so the attempt counts even if the request later fails
    (for example a wrong password).
    """
    if not settings.RATE_LIMITS_ENABLED:
        return
    limit = LIMITS[bucket]
    now = time.time() if now is None else now
    window = limit.window_seconds
    window_start = int(now // window) * window
    key = _bucket_key(bucket, subject)

    current = await _increment(db, key, window_start)
    previous = (await db.execute(
        select(RateLimitCounterModel.hits).where(
            RateLimitCounterModel.bucket_key == key,
            RateLimitCounterModel.window_start == window_start - window,
        )
    )).scalar_one_or_none() or 0
    if random.random() < 0.01:
        await db.execute(delete(RateLimitCounterModel).where(
            RateLimitCounterModel.window_start < int(now) - _RETENTION_SECONDS))
    await db.commit()

    elapsed = now - window_start
    weighted = current + previous * (1 - elapsed / window)
    if weighted > limit.hits:
        retry_after = max(1, math.ceil(window - elapsed))
        raise RateLimitExceeded(retry_after)


async def purge_expired(db: AsyncSession) -> None:
    await db.execute(delete(RateLimitCounterModel).where(
        RateLimitCounterModel.window_start < int(time.time()) - _RETENTION_SECONDS))
    await db.commit()
