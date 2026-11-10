"""사진 run 보관 — 읽은 결과를 run_hold 에 30분 (#877 2단계 · notice-ocr-v1 §42).

메모리 보관함이 지키던 것을 표가 그대로 지키는지 건다.
- 꺼낼 수 있는 것은 연 보호자뿐이다 (남의 run 은 없는 run)
- 30분이 지나면 없다
- 저장 응답을 남기면 다시 꺼낼 때 그 응답이 있다 — 같은 저장을 다시 보내도 두 번 넣지 않는다
- 저장 중에는 행을 잠근다 — 워커 둘에 같은 저장이 동시에 와도 뒤 요청은 앞 요청이 끝난 뒤
  응답을 본다

읽은 결과는 test_photos.py 의 합성 공지를 쓴다 (루트 §9).
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.runs import photo_run
from app.domains.child.models import Child
from app.domains.daycare_meal.models import DaycareSlot
from app.domains.identity.models import Parent
from app.infra.db.session import engine
from tests.integration.api.conftest import Bearer, issue_bearer, link_child
from tests.integration.api.test_photos import _meal_plan, _reading

pytestmark = pytest.mark.usefixtures("pending_db")


def _filled(run: photo_run.PhotoRun) -> photo_run.PhotoRun:
    """사진을 다 읽은 run — 확인 화면 항목까지 만든 상태."""
    reading = _reading(meal_plan=_meal_plan())
    run.reading = reading
    photo_run.build_entries(run, reading, today=date(2026, 10, 27))
    return run


async def test_읽은_결과를_넣고_연_보호자가_그대로_꺼낸다(bearer: Bearer, cid: uuid.UUID):
    _, me = bearer
    run = _filled(photo_run.open_photo_run(run_id="p1", parent_id=me, child_id=cid))
    assert run.items and run.meals

    await photo_run.keep(run)
    got = await photo_run.get("p1", parent_id=me)

    assert got is not None
    assert (got.run_id, got.parent_id, got.child_id) == ("p1", me, cid)
    assert got.reading == run.reading
    assert got.items == run.items
    assert got.meals == run.meals
    assert got.response is None


async def test_남의_run_은_없는_run_이다(session: AsyncSession, bearer: Bearer, cid: uuid.UUID):
    _, me = bearer
    _, other = await issue_bearer(session, token="test-token-other")
    await link_child(session, parent_id=other, child_id=cid)
    await photo_run.keep(_filled(photo_run.open_photo_run(run_id="p1", parent_id=me, child_id=cid)))

    assert await photo_run.get("p1", parent_id=other) is None


async def test_30분이_지나면_없다(bearer: Bearer, cid: uuid.UUID):
    _, me = bearer
    t0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    run = _filled(photo_run.open_photo_run(run_id="p1", parent_id=me, child_id=cid))
    await photo_run.keep(run, now=t0)

    assert await photo_run.get("p1", parent_id=me, now=t0 + timedelta(minutes=29)) is not None
    assert (
        await photo_run.get("p1", parent_id=me, now=t0 + timedelta(minutes=30, seconds=1)) is None
    )


async def test_저장_응답을_남기면_다시_꺼낼_때_있다(
    session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    _, me = bearer
    await photo_run.keep(_filled(photo_run.open_photo_run(run_id="p1", parent_id=me, child_id=cid)))

    locked = await photo_run.lock(session, "p1", parent_id=me)
    assert locked is not None and locked.response is None
    await photo_run.finish(session, locked, {"calendar_date": "2026-11-06"})
    await session.commit()

    again = await photo_run.lock(session, "p1", parent_id=me)
    assert again is not None and again.response == {"calendar_date": "2026-11-06"}


async def test_lock_도_주인과_만료를_본다(session: AsyncSession, bearer: Bearer, cid: uuid.UUID):
    _, me = bearer
    _, other = await issue_bearer(session, token="test-token-other")
    await link_child(session, parent_id=other, child_id=cid)
    await photo_run.keep(_filled(photo_run.open_photo_run(run_id="p1", parent_id=me, child_id=cid)))

    assert await photo_run.lock(session, "p1", parent_id=other) is None
    assert await photo_run.lock(session, "missing", parent_id=me) is None


async def test_아이를_지우면_사라진다(session: AsyncSession, bearer: Bearer, cid: uuid.UUID):
    _, me = bearer
    await photo_run.keep(_filled(photo_run.open_photo_run(run_id="p1", parent_id=me, child_id=cid)))

    await session.execute(delete(Child).where(Child.id == cid))

    assert await photo_run.get("p1", parent_id=me) is None


async def test_지운_아이로_넣으면_예외_없이_넘어간다(
    session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    _, me = bearer
    run = _filled(photo_run.open_photo_run(run_id="p1", parent_id=me, child_id=cid))
    await session.execute(delete(Child).where(Child.id == cid))

    await photo_run.keep(run)  # 예외가 나지 않는다

    assert await photo_run.get("p1", parent_id=me) is None


def test_끼니_칸은_날짜와_끼니로_돌아온다():
    """meals 의 값은 (날짜, 끼니) 짝이다 — JSON 을 거쳐도 date · DaycareSlot 으로 돌아와야 한다."""
    slot = photo_run.MealSlot(date(2026, 11, 2), DaycareSlot.LUNCH)
    run = photo_run.PhotoRun("p1", uuid.uuid4(), uuid.uuid4(), meals={"m": slot})

    back = photo_run._load(photo_run._dump(run))

    assert back.meals == {"m": slot}


def test_프로세스_메모리_보관함이_없다():
    for gone in ("_runs", "_purge", "forget_child"):
        assert not hasattr(photo_run, gone), gone


# --- 동시 저장 — 실제로 커밋하는 연결 두 개 -------------------------------------------------------


@pytest.fixture
async def committed_owner(monkeypatch: pytest.MonkeyPatch):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(photo_run, "_session_factory", factory)
    async with factory() as s:
        parent = Parent()
        s.add(parent)
        await s.flush()
        child = Child(owner_parent_id=parent.id, nickname="테스트아이", birth_date=date(2023, 1, 1))
        s.add(child)
        await s.commit()
        ids = (parent.id, child.id)
    yield factory, ids
    async with factory() as s:
        await s.execute(delete(Child).where(Child.id == ids[1]))
        await s.execute(delete(Parent).where(Parent.id == ids[0]))
        await s.commit()


async def test_같은_저장이_동시에_오면_뒤_요청은_앞_요청의_응답을_본다(committed_owner):
    factory, (parent_id, child_id) = committed_owner
    run_id = uuid.uuid4().hex
    await photo_run.keep(
        _filled(photo_run.open_photo_run(run_id=run_id, parent_id=parent_id, child_id=child_id))
    )
    first_locked = asyncio.Event()
    saved = 0

    async def commit(first: bool):
        nonlocal saved
        async with factory() as s:
            run = await photo_run.lock(s, run_id, parent_id=parent_id)
            if first:
                first_locked.set()
                await asyncio.sleep(0.2)  # 뒤 요청이 잠금에서 기다리게
            if run.response is not None:
                return run.response
            saved += 1
            await photo_run.finish(s, run, {"calendar_date": "2026-11-06"})
            await s.commit()
            return run.response

    async def second():
        await first_locked.wait()
        return await commit(first=False)

    a, b = await asyncio.gather(commit(first=True), second())

    assert a == b == {"calendar_date": "2026-11-06"}
    assert saved == 1


async def test_읽을_수_없는_사진_run_은_없는_run_이다(
    session: AsyncSession, bearer: Bearer, cid: uuid.UUID, caplog
):
    """PhotoRun 모양을 바꾼 배포 뒤 옛 행 — 공지 원문을 로그에 남기지 않고 404 로 끝낸다."""
    from app.domains.run_hold.models import HoldKind
    from app.domains.run_hold.repository import put_hold

    _, me = bearer
    for key in ("bad-get", "bad-lock"):
        await put_hold(
            session,
            kind=HoldKind.PHOTO_RUN,
            key=key,
            parent_id=me,
            child_id=cid,
            payload={"run_id": key, "items": "synthetic secret"},
            ttl=photo_run.TTL,
        )

    assert await photo_run.get("bad-get", parent_id=me) is None
    assert await photo_run.lock(session, "bad-lock", parent_id=me) is None
    assert "synthetic secret" not in caplog.text
