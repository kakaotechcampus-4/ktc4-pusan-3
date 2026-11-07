"""위치 동의 철회와 위치 기록이 겹쳐도 철회 뒤에 기록이 남지 않는다 (#693 리뷰).

잠금 없이 하면 이렇게 된다. 러너가 동의를 읽고 기록을 넣기 전에 철회가 커밋된다. 철회의 삭제는
아직 없는 행이라 지우지 못하고, 기록은 6개월 남는다 — 위치 약관 제10조 ① "철회하면 파기" 위반이다.

철회는 실제 라우트(`change_consent`)로 부른다 — 라우트가 잠금을 빠뜨리면 여기서 멈춰야 한다.
트랜잭션 둘이 실제로 겹쳐야 해서 롤백 픽스처(session)를 쓰지 않는다. 커넥션 둘을 따로 열고,
심은 행은 끝에 직접 지운다 (test_report_concurrency.py 와 같은 방식).
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import AuthContext
from app.api.v1.routers import consents
from app.api.v1.schemas.consents import ConsentRequest
from app.domains.consent.location_log import record_location_use_if_granted
from app.domains.consent.models import Consent, ConsentScope, LocationPurpose, LocationUseLog
from app.domains.consent.repository import grant_account_scope
from app.domains.identity.models import Parent
from app.domains.policy.repository import find_active_versions
from app.infra.db.session import engine
from tests.integration.api.conftest import backend_pid, wait_for_lock_wait


async def _parent_with_location() -> tuple[uuid.UUID, str]:
    """위치 동의를 켠 보호자 하나를 커밋해 심는다. 철회에 쓸 약관 버전 이름도 돌려준다."""
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        parent = Parent()
        setup.add(parent)
        await setup.flush()
        rows = await find_active_versions(setup, now=datetime.now(UTC))
        version = next(row for row in rows if row.scope is ConsentScope.LOCATION)
        await grant_account_scope(
            setup, parent_id=parent.id, scope=ConsentScope.LOCATION, policy_version_id=version.id
        )
        await setup.commit()
        return parent.id, version.version


async def _withdraw(session: AsyncSession, parent_id: uuid.UUID, version: str) -> None:
    """POST /consents 의 위치 끄기. 라우트가 끝에서 커밋한다."""
    await consents.change_consent(
        ConsentRequest(scope="location", action="withdrawn", policy_version=version),
        AuthContext(parent_id=parent_id, session_id=uuid.uuid4()),
        session,
    )


async def _record(session: AsyncSession, parent_id: uuid.UUID) -> bool:
    return await record_location_use_if_granted(
        session, parent_id=parent_id, purpose=LocationPurpose.SUGGESTION
    )


async def _count(parent_id: uuid.UUID) -> int:
    async with AsyncSession(engine) as probe:
        return await probe.scalar(
            select(func.count())
            .select_from(LocationUseLog)
            .where(LocationUseLog.parent_id == parent_id)
        )


async def _cleanup(parent_id: uuid.UUID) -> None:
    async with AsyncSession(engine) as cleanup:
        # 동의의 대상 FK 는 RESTRICT 라 먼저 지운다. 기록은 보호자 행의 CASCADE 로 지워진다
        await cleanup.execute(delete(Consent).where(Consent.subject_parent_id == parent_id))
        await cleanup.execute(delete(Parent).where(Parent.id == parent_id))
        await cleanup.commit()


async def _settle(*tasks: asyncio.Task) -> None:
    """단언이 실패해도 기다리던 쿼리를 남긴 채 세션을 닫지 않는다 (test_report_concurrency)."""
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_철회가_먼저_잡으면_기록은_기다렸다가_위치를_쓰지_않는다(monkeypatch):
    parent_id, version = await _parent_with_location()
    # 철회가 기록을 지운 뒤 커밋 전에 멈춘다 — 그 사이에 기록이 끼어드는지 본다
    erased, release = asyncio.Event(), asyncio.Event()
    real_erase = consents.erase_location_logs

    async def erase_then_wait(session, *, parent_id):
        await real_erase(session, parent_id=parent_id)
        erased.set()
        await release.wait()

    monkeypatch.setattr(consents, "erase_location_logs", erase_then_wait)
    try:
        async with (
            AsyncSession(engine, expire_on_commit=False) as a,
            AsyncSession(engine, expire_on_commit=False) as b,
        ):
            pid_a = await backend_pid(a)
            withdrawing = asyncio.create_task(_withdraw(a, parent_id, version))
            await asyncio.wait_for(erased.wait(), timeout=5)
            recording = asyncio.create_task(_record(b, parent_id))
            try:
                await wait_for_lock_wait(blocker=pid_a)
                release.set()
                await withdrawing
                assert await recording is False
            finally:
                release.set()
                await _settle(withdrawing, recording)
            await b.commit()
        assert await _count(parent_id) == 0
    finally:
        await _cleanup(parent_id)


async def test_기록이_먼저_잡으면_철회는_기다렸다가_그_기록까지_지운다():
    parent_id, version = await _parent_with_location()
    try:
        async with (
            AsyncSession(engine, expire_on_commit=False) as a,
            AsyncSession(engine, expire_on_commit=False) as b,
        ):
            assert await _record(b, parent_id) is True
            pid_b = await backend_pid(b)
            withdrawing = asyncio.create_task(_withdraw(a, parent_id, version))
            try:
                await wait_for_lock_wait(blocker=pid_b)
                await b.commit()
                await withdrawing
            finally:
                await _settle(withdrawing)
        assert await _count(parent_id) == 0
    finally:
        await _cleanup(parent_id)
