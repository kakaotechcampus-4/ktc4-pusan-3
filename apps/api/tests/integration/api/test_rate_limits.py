"""하루 입력 한도 · 초대 코드 시도 제한 — 워커 밖 카운터 (#877 3단계).

메모리 카운터가 지키던 것을 표(`rate_event`)가 그대로 지키는지 건다.
- 하루 입력: 한도까지 받고 그다음 거절, 보호자마다 따로, 날짜가 바뀌면 0부터 (quota.py)
- 초대 시도: 실패가 1시간에 계정 5회 · IP 30회를 넘으면 거절, 실패가 아니면 돌려준다,
  확인 · 수락이 같은 통을 쓴다 (invite_attempts.py · invite-v1 §4)
- 🚨 워커가 여럿이어도 한도를 넘지 않는다 — 실제로 커밋하는 연결 여럿으로 동시에 부른다

보호자 id 는 지어낸 값이다(표에 FK 가 없다). 값은 합성값이다 (루트 §9).
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import invite_attempts, quota
from app.api.runs import hold_purge
from app.domains.rate_event.models import RateEvent
from app.infra.db.session import engine

DAY = date(2026, 11, 10)
T0 = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


@pytest.fixture
async def rate_db(session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    factory = async_sessionmaker(
        bind=await session.connection(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    for store in (quota, invite_attempts, hold_purge):
        monkeypatch.setattr(store, "_session_factory", factory)
    return factory


# --- 하루 입력 한도 -------------------------------------------------------------------------


@pytest.mark.usefixtures("rate_db")
async def test_한도까지_받고_그다음은_거절한다():
    parent = uuid.uuid4()

    results = [await quota.consume(parent_id=parent, today=DAY, limit=3) for _ in range(4)]

    assert results == [True, True, True, False]


@pytest.mark.usefixtures("rate_db")
async def test_보호자마다_따로_센다():
    a, b = uuid.uuid4(), uuid.uuid4()
    for _ in range(2):
        await quota.consume(parent_id=a, today=DAY, limit=2)

    assert await quota.consume(parent_id=a, today=DAY, limit=2) is False
    assert await quota.consume(parent_id=b, today=DAY, limit=2) is True


@pytest.mark.usefixtures("rate_db")
async def test_날짜가_바뀌면_0부터():
    parent = uuid.uuid4()
    await quota.consume(parent_id=parent, today=DAY, limit=1)

    assert await quota.consume(parent_id=parent, today=DAY, limit=1) is False
    assert await quota.consume(parent_id=parent, today=DAY + timedelta(days=1), limit=1) is True


@pytest.mark.usefixtures("rate_db")
async def test_reset_은_하루_입력_한도만_비운다():
    """eval 이 한도를 끄고 돌 때 쓴다. 초대 시도 제한은 건드리지 않는다."""
    parent = uuid.uuid4()
    await quota.consume(parent_id=parent, today=DAY, limit=1)
    await invite_attempts.acquire(parent_id=parent, ip="198.51.100.1", now=T0)

    await quota.reset()

    assert await quota.consume(parent_id=parent, today=DAY, limit=1) is True
    for _ in range(4):
        await invite_attempts.acquire(parent_id=parent, ip="198.51.100.1", now=T0)
    assert await invite_attempts.acquire(parent_id=parent, ip="198.51.100.1", now=T0) is False


# --- 초대 코드 시도 제한 ----------------------------------------------------------------------


@pytest.mark.usefixtures("rate_db")
async def test_계정은_1시간에_5회까지():
    parent = uuid.uuid4()

    results = [
        await invite_attempts.acquire(parent_id=parent, ip=f"198.51.100.{n}", now=T0)
        for n in range(6)
    ]

    assert results == [True] * 5 + [False]


@pytest.mark.usefixtures("rate_db")
async def test_IP_는_계정을_바꿔도_1시간에_30회까지():
    results = [
        await invite_attempts.acquire(parent_id=uuid.uuid4(), ip="198.51.100.7", now=T0)
        for _ in range(31)
    ]

    assert results == [True] * 30 + [False]


@pytest.mark.usefixtures("rate_db")
async def test_실패가_아니었으면_돌려준다():
    parent = uuid.uuid4()
    for _ in range(5):
        await invite_attempts.acquire(parent_id=parent, ip="198.51.100.1", now=T0)

    await invite_attempts.release(parent_id=parent, ip="198.51.100.1", now=T0)

    assert await invite_attempts.acquire(parent_id=parent, ip="198.51.100.1", now=T0) is True


@pytest.mark.usefixtures("rate_db")
async def test_돌려주는_것은_그_한_건뿐이다():
    """같은 계정이 다른 시각에 잡은 실패까지 지우면 한도가 풀린다."""
    parent = uuid.uuid4()
    for n in range(5):
        await invite_attempts.acquire(
            parent_id=parent, ip="198.51.100.1", now=T0 + timedelta(seconds=n)
        )

    await invite_attempts.release(parent_id=parent, ip="198.51.100.1", now=T0)

    assert await invite_attempts.acquire(
        parent_id=parent, ip="198.51.100.1", now=T0 + timedelta(seconds=10)
    )
    assert not await invite_attempts.acquire(
        parent_id=parent, ip="198.51.100.1", now=T0 + timedelta(seconds=11)
    )


@pytest.mark.usefixtures("rate_db")
async def test_1시간이_지난_실패는_세지_않는다():
    parent = uuid.uuid4()
    for _ in range(5):
        await invite_attempts.acquire(parent_id=parent, ip="198.51.100.1", now=T0)

    later = T0 + timedelta(hours=1, seconds=1)
    assert await invite_attempts.acquire(parent_id=parent, ip="198.51.100.1", now=later) is True


@pytest.mark.usefixtures("rate_db")
async def test_한도에_걸린_요청은_실패를_더하지_않는다(session: AsyncSession):
    parent = uuid.uuid4()
    for _ in range(6):
        await invite_attempts.acquire(parent_id=parent, ip="198.51.100.1", now=T0)

    count = await session.scalar(
        select(func.count())
        .select_from(RateEvent)
        .where(RateEvent.key == f"invite:parent:{parent}")
    )
    assert count == 5


# --- 지우기 --------------------------------------------------------------------------------


@pytest.mark.usefixtures("rate_db")
async def test_만료_삭제는_이틀_지난_카운터를_지운다(session: AsyncSession):
    parent = uuid.uuid4()
    old_day = DAY - timedelta(days=3)
    await quota.consume(parent_id=parent, today=old_day, limit=5, now=T0 - timedelta(days=3))
    await quota.consume(parent_id=parent, today=DAY, limit=5, now=T0)

    await hold_purge.purge_once(now=T0)

    keys = (await session.scalars(select(RateEvent.key))).all()
    assert keys == [f"input:{parent}:{DAY.isoformat()}"]


def test_프로세스_메모리_카운터가_없다():
    for module, gone in ((quota, "_counts"), (quota, "clear"), (invite_attempts, "_failures")):
        assert not hasattr(module, gone), gone


# --- 동시성 — 실제로 커밋하는 연결 여럿 -----------------------------------------------------------


@pytest.fixture
async def committed(monkeypatch: pytest.MonkeyPatch):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for store in (quota, invite_attempts):
        monkeypatch.setattr(store, "_session_factory", factory)
    keys: list[str] = []
    yield keys
    async with factory() as s:
        await s.execute(delete(RateEvent).where(RateEvent.key.in_(keys)))
        await s.commit()


async def test_하루_한도는_동시에_와도_넘지_않는다(committed):
    """워커 열 곳에 같은 보호자의 입력이 동시에 들어온 경우."""
    parent = uuid.uuid4()
    committed.append(f"input:{parent}:{DAY.isoformat()}")

    results = await asyncio.gather(
        *(quota.consume(parent_id=parent, today=DAY, limit=3) for _ in range(10))
    )

    assert sum(results) == 3


async def test_초대_시도_제한도_동시에_와도_넘지_않는다(committed):
    parent = uuid.uuid4()
    committed += [f"invite:parent:{parent}", "invite:ip:203.0.113.9"]

    results = await asyncio.gather(
        *(invite_attempts.acquire(parent_id=parent, ip="203.0.113.9", now=T0) for _ in range(10))
    )

    assert sum(results) == 5
