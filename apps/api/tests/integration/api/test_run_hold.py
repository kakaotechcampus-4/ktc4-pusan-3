"""run_hold 표 — 워커 밖 보관함의 SQL 동작 (#877 · docs/ops/stateless-v1.md 1단계).

지금 메모리 보관함(`api/runs/pending_reply.py`)이 지키던 규칙을 표가 그대로 지키는지 건다.
- 꺼낼 수 있는 것은 넣은 보호자 · 같은 아이 · 사용 중 아님 · 15분 안 일 때뿐 (전부 None 으로 합친다)
- 표시를 풀어도 15분은 처음 넣은 때부터 센다 (#564)
- 탈퇴 · 아이 삭제는 FK CASCADE, 연결 끊기는 그 보호자 · 그 아이 것만 지운다 (retention-v1)
- 동시에 온 두 claim 중 하나만 받는다 — 워커가 여럿이면 DB 한 문장이 이것을 맡는다

시각은 `now=` 로 고정해 넘긴다. 값은 전부 합성값이다 (루트 §9).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.run_hold.models import HoldKind, RunHold
from app.domains.run_hold.repository import (
    claim_hold,
    delete_expired_holds,
    delete_link_holds,
    drop_hold,
    get_hold,
    open_hold_keys,
    put_hold,
    release_hold,
)
from app.infra.db.session import engine
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

KIND = HoldKind.PENDING_REPLY
TTL = timedelta(minutes=15)
T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
PAYLOAD = {"hint_text": "synthetic", "question": "synthetic question"}


async def _put(session, key, *, parent_id, child_id, payload=PAYLOAD, now=T0):
    await put_hold(
        session,
        kind=KIND,
        key=key,
        parent_id=parent_id,
        child_id=child_id,
        payload=payload,
        ttl=TTL,
        now=now,
    )


async def _child_of_someone_else(session: AsyncSession) -> uuid.UUID:
    """다른 아이. 보호자 한 명은 아이 하나에만 이어져서 새 보호자로 만든다.

    `uq_parent_child_parent_id` 때문이다.

    run_hold 의 FK 는 보호자 · 아이 행이 있는지만 본다 — 둘이 이어져 있는지는 보지 않는다.
    """
    _, owner = await issue_bearer(session, token=f"test-token-{uuid.uuid4().hex}")
    return await link_child(session, parent_id=owner)


# --- 정상 동작 ------------------------------------------------------------------------------


async def test_넣은_것을_같은_보호자_같은_아이가_꺼낸다(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)

    got = await claim_hold(session, kind=KIND, key="r1", parent_id=me, child_id=cid, now=T0)

    assert got == PAYLOAD


async def test_get_은_확인만_하고_사용_중으로_바꾸지_않는다(
    session: AsyncSession, bearer: Bearer, cid
):
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)

    owner = {"kind": KIND, "key": "r1", "parent_id": me, "child_id": cid, "now": T0}
    assert await get_hold(session, **owner) == PAYLOAD
    assert await claim_hold(session, **owner) == PAYLOAD


async def test_표시를_풀면_다시_꺼낼_수_있다(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)
    owner = {"kind": KIND, "key": "r1", "parent_id": me, "child_id": cid, "now": T0}
    await claim_hold(session, **owner)

    await release_hold(session, kind=KIND, key="r1")

    assert await claim_hold(session, **owner) == PAYLOAD


async def test_지우면_다시_꺼낼_수_없다(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)

    await drop_hold(session, kind=KIND, key="r1")

    assert await get_hold(session, kind=KIND, key="r1", parent_id=me, child_id=cid, now=T0) is None


async def test_같은_key_로_다시_넣으면_덮고_15분을_새로_센다(
    session: AsyncSession, bearer: Bearer, cid
):
    """한 run 에서 질문이 다시 나오면 나중 맥락이 앞의 것을 덮는다 — 메모리 보관함과 같다."""
    _, me = bearer
    owner = {"kind": KIND, "key": "r1", "parent_id": me, "child_id": cid}
    await _put(session, "r1", parent_id=me, child_id=cid, payload={"v": "a"}, now=T0)
    await claim_hold(session, **owner, now=T0)

    await _put(
        session,
        "r1",
        parent_id=me,
        child_id=cid,
        payload={"v": "b"},
        now=T0 + timedelta(minutes=10),
    )

    assert await claim_hold(session, **owner, now=T0 + timedelta(minutes=20)) == {"v": "b"}


async def test_open_hold_keys_는_지금_답할_수_있는_것만_돌려준다(
    session: AsyncSession, bearer: Bearer, cid
):
    _, me = bearer
    other_headers, other = await issue_bearer(session, token="test-token-other")
    await link_child(session, parent_id=other, child_id=cid)
    await _put(session, "open", parent_id=me, child_id=cid)
    await _put(session, "busy", parent_id=me, child_id=cid)
    await claim_hold(session, kind=KIND, key="busy", parent_id=me, child_id=cid, now=T0)
    await _put(session, "old", parent_id=me, child_id=cid, now=T0 - timedelta(minutes=16))
    await _put(session, "theirs", parent_id=other, child_id=cid)

    opened = await open_hold_keys(
        session,
        kind=KIND,
        keys=["open", "busy", "old", "theirs", "missing"],
        parent_id=me,
        child_id=cid,
        now=T0,
    )

    assert opened == {"open"}


async def test_open_hold_keys_는_빈_목록이면_빈_set(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer

    assert await open_hold_keys(session, kind=KIND, keys=[], parent_id=me, child_id=cid) == set()


# --- 꺼낼 수 없는 경우 — 전부 None ------------------------------------------------------------


async def test_사용_중이면_다시_꺼낼_수_없다(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)
    owner = {"kind": KIND, "key": "r1", "parent_id": me, "child_id": cid, "now": T0}
    await claim_hold(session, **owner)

    assert await get_hold(session, **owner) is None
    assert await claim_hold(session, **owner) is None


async def test_다른_보호자는_꺼낼_수_없다(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer
    _, other = await issue_bearer(session, token="test-token-other")
    await link_child(session, parent_id=other, child_id=cid)
    await _put(session, "r1", parent_id=me, child_id=cid)

    assert (
        await claim_hold(session, kind=KIND, key="r1", parent_id=other, child_id=cid, now=T0)
        is None
    )


async def test_다른_아이의_질문은_꺼낼_수_없다(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer
    other_child = await _child_of_someone_else(session)
    await _put(session, "r1", parent_id=me, child_id=cid)

    assert (
        await claim_hold(session, kind=KIND, key="r1", parent_id=me, child_id=other_child, now=T0)
        is None
    )


async def test_없는_key_는_None(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer

    assert (
        await claim_hold(session, kind=KIND, key="nope", parent_id=me, child_id=cid, now=T0) is None
    )


async def test_15분이_지나면_꺼낼_수_없다(session: AsyncSession, bearer: Bearer, cid):
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)
    owner = {"kind": KIND, "key": "r1", "parent_id": me, "child_id": cid}

    assert await get_hold(session, **owner, now=T0 + timedelta(minutes=14, seconds=59)) == PAYLOAD
    assert await get_hold(session, **owner, now=T0 + TTL + timedelta(seconds=1)) is None


async def test_표시를_풀어도_15분은_처음_넣은_때부터_센다(
    session: AsyncSession, bearer: Bearer, cid
):
    """실패할 때마다 15분이 새로 시작되면 보관 기준(retention-v1.md)을 넘긴다 (#564)."""
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)
    owner = {"kind": KIND, "key": "r1", "parent_id": me, "child_id": cid}
    await claim_hold(session, **owner, now=T0 + timedelta(minutes=10))

    await release_hold(session, kind=KIND, key="r1")

    assert await get_hold(session, **owner, now=T0 + TTL + timedelta(seconds=1)) is None


async def test_만료된_행은_claim_해도_사용_중이_되지_않는다(
    session: AsyncSession, bearer: Bearer, cid
):
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)

    got = await claim_hold(
        session, kind=KIND, key="r1", parent_id=me, child_id=cid, now=T0 + timedelta(minutes=16)
    )

    assert got is None
    row = await session.scalar(select(RunHold).where(RunHold.key == "r1"))
    assert row is not None and row.in_use is False


async def test_지운_아이로_넣으면_FK_오류가_난다(session: AsyncSession, bearer: Bearer, cid):
    """늦게 끝난 run 이 지운 아이 앞으로 저장하려는 경우 — 표가 막는다.

    조용히 버리는 것은 파사드(pending_reply.put) 몫이다.
    """
    from sqlalchemy.exc import IntegrityError

    _, me = bearer
    await session.execute(delete(Child).where(Child.id == cid))

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await _put(session, "r1", parent_id=me, child_id=cid)


# --- 지우기 --------------------------------------------------------------------------------


async def _rows(session: AsyncSession) -> list[tuple[uuid.UUID, uuid.UUID]]:
    result = await session.execute(select(RunHold.parent_id, RunHold.child_id))
    return sorted(result.all())


async def test_아이를_지우면_그_아이의_행이_모두_사라진다(
    session: AsyncSession, bearer: Bearer, cid
):
    _, me = bearer
    _, member = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=member, child_id=cid)
    keep = await _child_of_someone_else(session)
    await _put(session, "mine", parent_id=me, child_id=cid)
    await _put(session, "member", parent_id=member, child_id=cid)
    await _put(session, "other-child", parent_id=me, child_id=keep)

    await session.execute(delete(Child).where(Child.id == cid))

    assert await _rows(session) == [(me, keep)]


async def test_보호자를_지우면_그_보호자의_행이_사라진다(
    session: AsyncSession, bearer: Bearer, cid
):
    _, me = bearer
    _, member = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=member, child_id=cid)
    await _put(session, "mine", parent_id=me, child_id=cid)
    await _put(session, "member", parent_id=member, child_id=cid)

    await session.execute(delete(Parent).where(Parent.id == member))

    assert await _rows(session) == [(me, cid)]


async def test_연결_끊기는_그_보호자_그_아이_것만_지운다(
    session: AsyncSession, bearer: Bearer, cid
):
    _, me = bearer
    _, member = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=member, child_id=cid)
    other_child = await _child_of_someone_else(session)
    await _put(session, "mine", parent_id=me, child_id=cid)
    await _put(session, "member-here", parent_id=member, child_id=cid)
    await _put(session, "member-there", parent_id=member, child_id=other_child)

    await delete_link_holds(session, parent_id=member, child_id=cid)

    assert await _rows(session) == sorted([(me, cid), (member, other_child)])


async def test_만료_삭제는_만료된_것만_지우고_개수를_돌려준다(
    session: AsyncSession, bearer: Bearer, cid
):
    _, me = bearer
    await _put(session, "old", parent_id=me, child_id=cid, now=T0 - timedelta(minutes=16))
    await _put(session, "fresh", parent_id=me, child_id=cid, now=T0)

    removed = await delete_expired_holds(session, now=T0)

    assert removed == 1
    keys = (await session.scalars(select(RunHold.key))).all()
    assert keys == ["fresh"]


# --- 표 자체 -------------------------------------------------------------------------------


async def test_표는_UNLOGGED_다(session: AsyncSession):
    """DB 가 비정상 종료되면 비워진다 — 메모리 보관함과 같은 동작이고 WAL 에 원문이 남지 않는다."""
    persistence = await session.scalar(
        text("SELECT relpersistence::text FROM pg_class WHERE relname = 'run_hold'")
    )

    assert persistence == "u"


# --- 동시성 — 실제로 커밋하는 연결 두 개 ---------------------------------------------------------


@pytest.fixture
async def committed_owner():
    """실제로 커밋한 보호자 · 아이. 끝나면 지운다(행은 CASCADE 로 같이 사라진다)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        parent = Parent()
        s.add(parent)
        await s.flush()
        child = Child(owner_parent_id=parent.id, nickname="테스트아이", birth_date=T0.date())
        s.add(child)
        await s.commit()
        ids = (parent.id, child.id)
    yield factory, ids
    async with factory() as s:
        await s.execute(delete(Child).where(Child.id == ids[1]))
        await s.execute(delete(Parent).where(Parent.id == ids[0]))
        await s.commit()


async def test_동시에_두_번_claim_하면_하나만_받는다(committed_owner):
    """워커 둘에 같은 답이 거의 동시에 들어온 경우. 확인과 표시가 한 문장이라 하나만 통과한다."""
    factory, (parent_id, child_id) = committed_owner
    key = uuid.uuid4().hex
    async with factory() as s:
        await _put(s, key, parent_id=parent_id, child_id=child_id, now=datetime.now(UTC))
        await s.commit()

    async def claim_in_own_connection():
        async with factory() as s:
            got = await claim_hold(s, kind=KIND, key=key, parent_id=parent_id, child_id=child_id)
            await s.commit()
            return got

    results = await asyncio.gather(claim_in_own_connection(), claim_in_own_connection())

    assert sorted(r is not None for r in results) == [False, True]


async def test_FK_검사는_보호자를_아이보다_먼저_잠근다(session: AsyncSession):
    """탈퇴는 보호자 → 아이 순서로 잠근다. 보관함에 넣을 때 FK 검사가 아이 → 보호자 순서면 탈퇴와
    겹칠 때 교착이 날 수 있다 (#899 jjaa0803 리뷰 🟠2). FK 검사 트리거는 이름 순으로 돈다."""
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT ref.relname FROM pg_trigger t
                JOIN pg_class tbl ON tbl.oid = t.tgrelid
                JOIN pg_class ref ON ref.oid = t.tgconstrrelid
                WHERE tbl.relname = 'run_hold' AND t.tgisinternal
                  AND t.tgfoid = 'pg_catalog."RI_FKey_check_ins"'::regproc
                ORDER BY t.tgname
                """
                )
            )
        )
        .scalars()
        .all()
    )

    assert rows == ["parent", "child"]


async def test_만료_시각_그때는_이미_꺼낼_수_없다(session: AsyncSession, bearer: Bearer, cid):
    """보관은 "15분 동안" 이다 — 정확히 15분이 된 순간부터는 없다.

    읽기는 `expires_at > now`, 지우기는 `expires_at <= now` 로 맞물린다.
    """
    _, me = bearer
    await _put(session, "r1", parent_id=me, child_id=cid)

    at_expiry = await get_hold(
        session, kind=KIND, key="r1", parent_id=me, child_id=cid, now=T0 + TTL
    )
    assert at_expiry is None
    assert await delete_expired_holds(session, now=T0 + TTL) == 1
