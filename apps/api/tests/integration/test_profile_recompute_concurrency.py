"""기억을 다시 계산하는 경로들이 같은 기억을 동시에 고칠 때 (#636)

recompute_profile 은 기억 행을 FOR UPDATE 로 잠그고 DB 값으로 다시 읽는다. 아니면 한쪽이 읽은
낡은 strength 로 다른 쪽이 커밋한 값을 덮어써 보호자 정정의 감소분이 사라진다(lost update).

트랜잭션 둘이 실제로 겹쳐야 해서 롤백 픽스처(session)를 쓰지 않는다 — test_invite_concurrency 와
같다. 심은 행은 끝에 직접 지운다(아이를 지우면 기억 · 관찰 · Correction 은 CASCADE).
"""

import asyncio
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child, ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ConfidenceSource, ObservationFood
from app.domains.memory.profile import service
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.infra.db.session import engine

TODAY = date(2026, 9, 26)
START = 0.55  # 승격 보너스가 붙은 확인됨


@pytest.fixture
async def seeded():
    """확인됨 기억 하나 — 14일 창 안에 관찰이 하루만 남아 배치가 후보로 내린다."""
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        owner = Parent()
        setup.add(owner)
        await setup.flush()
        child = await create_child(
            setup,
            owner_parent_id=owner.id,
            nickname="동시",
            birth_date=date(2023, 1, 1),
            relation=ParentChildRelation.MOTHER,
        )
        profile = ProfileAffinity(
            child_id=child.id,
            merge_key="동시성",
            domain=MemoryDomain.FOOD,
            state=ProfileState.CONFIRMED,
            polarity=1,
            strength=START,
            last_observed_on=TODAY - timedelta(days=13),
        )
        setup.add(profile)
        await setup.flush()
        for ago in (15, 14, 13):
            day = TODAY - timedelta(days=ago)
            setup.add(
                ObservationFood(
                    child_id=child.id,
                    raw_text="test",
                    subject="동시성",
                    polarity=1,
                    confidence_source=ConfidenceSource.PARENT_DIRECT,
                    observed_range=Range(day, day + timedelta(days=1)),
                    affinity_id=profile.id,
                )
            )
        await setup.commit()
    try:
        yield owner.id, child.id, profile.id
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Child).where(Child.id == child.id))
            await cleanup.execute(delete(Parent).where(Parent.id == owner.id))
            await cleanup.commit()


async def test_Curator_가_미리_읽은_뒤_Correction_이_커밋돼도_정정의_감소분이_남는다(
    seeded, monkeypatch
):
    """Curator 경로(refresh_profile)는 잠그기 전에 기억을 먼저 읽는다 — 그 값이 낡아도 쓰면 안 된다.

    recompute_profile 이 잠근 뒤 DB 값으로 다시 채우지 않으면(populate_existing), 미리 읽어 둔
    낡은 strength 로 Correction 이 커밋한 값을 덮어쓴다.
    """
    parent_id, child_id, profile_id = seeded
    curator_read = asyncio.Event()
    release = asyncio.Event()
    real_latest = service.get_latest_active_observed_on

    async with (
        AsyncSession(engine, expire_on_commit=False) as curator_s,
        AsyncSession(engine, expire_on_commit=False) as correction_s,
    ):

        async def pause_after_unlocked_read(session, **kw):
            # refresh_profile 이 기억을 잠금 없이 읽은 뒤 · recompute_profile 이 잠그기 전
            if session is curator_s and not curator_read.is_set():
                curator_read.set()
                await release.wait()
            return await real_latest(session, **kw)

        monkeypatch.setattr(service, "get_latest_active_observed_on", pause_after_unlocked_read)

        async def curator():
            await service.refresh_profile(curator_s, profile_id=profile_id, today=TODAY)
            await curator_s.commit()

        curator_task = asyncio.create_task(curator())
        await curator_read.wait()
        # Curator 는 아직 잠그지 않았다 — Correction 이 막힘 없이 먼저 커밋한다
        await service.handle_profile_correction(
            correction_s,
            child_id=child_id,
            profile_id=profile_id,
            verdict="wrong",
            parent_id=parent_id,
            today=TODAY,
        )
        await correction_s.commit()
        release.set()
        await asyncio.wait_for(curator_task, timeout=5)

    assert await _stored(profile_id) == (ProfileState.CANDIDATE, pytest.approx(START * 0.90 * 0.93))


async def _stored(profile_id):
    async with AsyncSession(engine) as check:
        row = (
            await check.execute(
                select(ProfileAffinity.state, ProfileAffinity.strength).where(
                    ProfileAffinity.id == profile_id
                )
            )
        ).one()
    return row.state, row.strength
