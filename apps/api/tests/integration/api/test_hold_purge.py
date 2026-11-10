"""만료된 run_hold 행 지우기 (#877 · docs/ops/stateless-v1.md 1단계 "지우기").

처리방침은 되묻기 맥락을 "15분 안에 지운다" 고 적었다. 하루 1회 정리 배치로는 모자라서 api 가
1분마다 지운다. 읽을 때도 만료를 거르지만(test_run_hold.py), 원문이 표에 남아 있지 않게 하는 것이
이 루프의 몫이다.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.runs import hold_purge
from app.domains.run_hold.models import HoldKind, RunHold
from app.domains.run_hold.repository import put_hold
from tests.integration.api.conftest import Bearer


@pytest.fixture
async def purge_db(session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    factory = async_sessionmaker(
        bind=await session.connection(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(hold_purge, "_session_factory", factory)


def test_1분마다_지운다():
    assert hold_purge.PURGE_INTERVAL_S == 60.0


@pytest.mark.usefixtures("purge_db")
async def test_purge_once_는_만료된_행만_지운다(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer
    now = datetime.now(UTC)
    for key, at in (("old", now - timedelta(hours=1)), ("fresh", now)):
        await put_hold(
            session,
            kind=HoldKind.PENDING_REPLY,
            key=key,
            parent_id=me,
            child_id=cid,
            payload={"v": key},
            ttl=timedelta(minutes=15),
            now=at,
        )

    removed = await hold_purge.purge_once()

    assert removed == 1
    assert (await session.scalars(select(RunHold.key))).all() == ["fresh"]


async def test_지우다_오류가_나도_루프는_멈추지_않는다(monkeypatch: pytest.MonkeyPatch, caplog):
    """DB 가 잠깐 안 닿아도 다음 주기에 다시 지운다.

    오류 하나로 루프가 죽으면 원문이 계속 남는다.
    """
    calls = 0
    second = asyncio.Event()

    async def flaky_purge():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("synthetic")
        second.set()
        return 0

    monkeypatch.setattr(hold_purge, "purge_once", flaky_purge)

    task = asyncio.create_task(hold_purge.run_purger(interval_s=0))
    await asyncio.wait_for(second.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls >= 2
    assert "ConnectionError" in caplog.text
