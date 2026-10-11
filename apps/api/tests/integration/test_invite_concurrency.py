"""같은 초대 코드로 동시에 두 번 수락하면 한쪽만 소비한다 (#198 · invite-v1.md §3-3).

트랜잭션 둘이 실제로 겹쳐야 해서 롤백 픽스처(session)를 쓰지 않는다. 커넥션 둘을 따로 열고,
심은 행은 끝에 직접 지운다.
"""

import asyncio
import hashlib
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child, ParentChildRelation
from app.domains.child.repository import consume_invite, create_child, create_invite
from app.domains.identity.models import Parent
from app.infra.db.session import engine

CODE_HASH = hashlib.sha256(b"CONC1234").digest()


async def test_동시에_두_번_소비하면_한쪽만_행을_받는다():
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        owner, first, second = Parent(), Parent(), Parent()
        setup.add_all([owner, first, second])
        await setup.flush()
        child = await create_child(
            setup,
            owner_parent_id=owner.id,
            nickname="동시",
            birth_date=date(2022, 1, 1),
            relation=ParentChildRelation.MOTHER,
        )
        await create_invite(
            setup,
            child_id=child.id,
            created_by=owner.id,
            code_hash=CODE_HASH,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await setup.commit()

    try:
        now = datetime.now(UTC)
        async with (
            AsyncSession(engine, expire_on_commit=False) as a,
            AsyncSession(engine, expire_on_commit=False) as b,
        ):
            # a 가 먼저 소비하고 커밋 전에 행 잠금을 쥔다. b 는 그 잠금에서 기다린다.
            got_a = await consume_invite(a, code_hash=CODE_HASH, parent_id=first.id, now=now)
            pending_b = asyncio.create_task(
                consume_invite(b, code_hash=CODE_HASH, parent_id=second.id, now=now)
            )
            await asyncio.sleep(0.2)
            assert not pending_b.done()

            await a.commit()
            got_b = await pending_b
            await b.commit()

        assert got_a is not None and got_a.used_by == first.id
        assert got_b is None
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Child).where(Child.id == child.id))
            await cleanup.execute(
                delete(Parent).where(Parent.id.in_([owner.id, first.id, second.id]))
            )
            await cleanup.commit()
        await engine.dispose()
