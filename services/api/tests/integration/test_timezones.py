"""Every stored time comes back timezone-aware (UTC), on SQLite as on PostgreSQL."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from jubran.infrastructure.db.models import CustomerSessionModel, PhysicalTableModel, TableSessionModel
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit


@pytest.mark.asyncio
async def test_times_are_aware_and_comparable(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T1")
    db_session.expire_all()
    started = (await db_session.execute(select(TableSessionModel.started_at))).scalar_one()
    expires = (await db_session.execute(select(CustomerSessionModel.expires_at))).scalar_one()
    assert started.tzinfo is not None and expires.tzinfo is not None
    now = datetime.now(timezone.utc)
    assert now - timedelta(minutes=1) < started <= now < expires  # no "naive vs aware" error


@pytest.mark.asyncio
async def test_a_time_in_another_zone_is_stored_as_the_same_moment(db_session):
    await seed_database(db_session)
    amman = timezone(timedelta(hours=3))
    moment = datetime(2026, 9, 28, 21, 30, tzinfo=amman)
    table_id = (await db_session.execute(select(PhysicalTableModel.id).limit(1))).scalar_one()
    visit = TableSessionModel(physical_table_id=table_id, started_at=moment)
    db_session.add(visit)
    await db_session.commit()
    visit_id = visit.id
    db_session.expire_all()
    stored = (await db_session.execute(select(TableSessionModel.started_at)
                                       .where(TableSessionModel.id == visit_id))).scalar_one()
    assert stored == moment and stored.utcoffset() == timedelta(0)
