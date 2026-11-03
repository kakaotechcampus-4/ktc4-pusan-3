"""기억을 다시 계산하는 경로들이 같은 기억을 동시에 고칠 때 (#636)

경로는 넷이다 — Curator 연결 · Correction · Memory 의 관찰 수정/삭제 · 하루 배치.
  · 기억 행 — recompute_profile 이 FOR UPDATE 로 잠그고 DB 값으로 다시 읽는다. 아니면 한쪽이 읽은
    낡은 strength 로 다른 쪽이 커밋한 값을 덮어써 보호자 정정의 감소분이 사라진다(lost update).
  · 아이 잠금 — 기억 여러 개를 잠그는 쪽(Curator · Memory 수정/삭제 · 배치)은 lock_child_memory 를
    먼저 잡는다. 아니면 서로 반대 순서로 잠가 Postgres 가 한쪽을 DeadlockDetected 로 죽인다.

트랜잭션 둘이 실제로 겹쳐야 해서 롤백 픽스처(session)를 쓰지 않는다 — test_invite_concurrency 와
같다. 배치 전체(run_recompute)는 DB 의 모든 기억을 커밋하므로, 아이 한 명 몫(_recompute_child)만
돌린다. 심은 행은 끝에 직접 지운다(아이를 지우면 기억 · 관찰 · Correction 은 CASCADE).
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
from app.workers.profile_recompute import _recompute_child

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


async def test_배치가_읽은_뒤_Correction_이_커밋돼도_정정의_감소분이_남는다(seeded, monkeypatch):
    parent_id, child_id, profile_id = seeded
    batch_read = asyncio.Event()  # 배치가 기억을 읽었다
    release = asyncio.Event()  # 배치를 이어서 쓰게 한다
    real_count = service.count_active_in_window

    async with (
        AsyncSession(engine, expire_on_commit=False) as batch_s,
        AsyncSession(engine, expire_on_commit=False) as correction_s,
    ):

        async def pause_batch_after_read(session, **kw):
            # recompute_profile 이 기억을 읽은 직후 · 쓰기 전. 배치 쪽 세션만 멈춘다
            if session is batch_s and not batch_read.is_set():
                batch_read.set()
                await release.wait()
            return await real_count(session, **kw)

        monkeypatch.setattr(service, "count_active_in_window", pause_batch_after_read)

        batch = asyncio.create_task(_recompute_child(batch_s, child_id, [profile_id], today=TODAY))
        await batch_read.wait()

        async def correct():
            await service.handle_profile_correction(
                correction_s,
                child_id=child_id,
                profile_id=profile_id,
                verdict="wrong",
                parent_id=parent_id,
                today=TODAY,
            )
            await correction_s.commit()

        correction = asyncio.create_task(correct())
        await asyncio.sleep(0.3)  # 잠금이 없으면 이 사이에 Correction 이 먼저 커밋한다
        release.set()
        await asyncio.wait_for(asyncio.gather(batch, correction), timeout=5)

    async with AsyncSession(engine) as check:
        state, strength = (
            await check.execute(
                select(ProfileAffinity.state, ProfileAffinity.strength).where(
                    ProfileAffinity.id == profile_id
                )
            )
        ).one()

    # 어느 쪽이 먼저든 차례로 돌면 같은 값이다 — 강등 ×0.90 과 wrong ×0.93 이 둘 다 붙는다
    # 배치가 덮어쓰면 START × 0.90 = 0.495 로 남는다 (wrong 감소분이 사라짐)
    assert state == ProfileState.CANDIDATE
    assert strength == pytest.approx(START * 0.90 * 0.93)


async def test_Curator_가_같은_아이를_잡고_있으면_배치가_기다린다(seeded):
    """Curator 는 관찰을 붙이고 재계산하는 동안 아이 단위 advisory lock 을 쥔다(trigger.py).

    배치가 이 잠금을 같은 (namespace, key) 로 잡지 않으면, Curator 가 읽은 낡은 상태로 배치의
    전이를 덮어쓸 수 있다. 잠금을 쥔 Curator 대역을 세우고, 배치가 그동안 끝나지 않는지 본다.
    """
    _, child_id, profile_id = seeded
    async with (
        AsyncSession(engine) as curator_s,
        AsyncSession(engine, expire_on_commit=False) as batch_s,
    ):
        await service.lock_child_memory(
            curator_s, child_id
        )  # trigger.py `_run_curator` 와 같은 잠금
        batch = asyncio.create_task(_recompute_child(batch_s, child_id, [profile_id], today=TODAY))
        await asyncio.sleep(0.3)
        assert not batch.done()  # Curator 가 쥐고 있는 동안은 기다린다

        await curator_s.commit()  # xact 잠금이 풀린다
        assert await asyncio.wait_for(batch, timeout=5) == (1, 1)


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


@pytest.fixture
async def two_linked():
    """기억 둘(A · B) — 각각 묶인 관찰 하나. Memory run 이 그 관찰들을 지운다."""
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        owner = Parent()
        setup.add(owner)
        await setup.flush()
        child = await create_child(
            setup,
            owner_parent_id=owner.id,
            nickname="교착",
            birth_date=date(2023, 1, 1),
            relation=ParentChildRelation.MOTHER,
        )
        pairs = []
        for key in ("A", "B"):
            profile = ProfileAffinity(
                child_id=child.id,
                merge_key=key,
                domain=MemoryDomain.FOOD,
                state=ProfileState.CANDIDATE,
                polarity=1,
                last_observed_on=TODAY,
            )
            setup.add(profile)
            await setup.flush()
            obs = ObservationFood(
                child_id=child.id,
                raw_text="test",
                subject=key,
                polarity=1,
                confidence_source=ConfidenceSource.PARENT_DIRECT,
                observed_range=Range(TODAY, TODAY + timedelta(days=1)),
                affinity_id=profile.id,
            )
            setup.add(obs)
            await setup.flush()
            pairs.append((profile.id, obs.id))
        await setup.commit()
    try:
        yield child.id, pairs
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Child).where(Child.id == child.id))
            await cleanup.execute(delete(Parent).where(Parent.id == owner.id))
            await cleanup.commit()


async def test_Memory_run_과_Curator_가_기억_둘을_반대_순서로_고쳐도_교착하지_않는다(two_linked):
    """Memory run 은 관찰을 고칠 때마다 그 기억을 다시 계산하고, 행 잠금을 커밋까지 쥔다.

    run 이 B → A 순서로 고치는 사이에 앞 run 의 Curator 가 A → B 로 다시 계산하면, 아이 잠금이
    없을 때 run 이 B 를 · Curator 가 A 를 쥔 채 서로를 기다려 Postgres 가 한쪽을
    DeadlockDetected 로 죽인다. run 쪽이 지면 보호자 입력이 저장되지 않는다.
    Memory 도 아이 잠금을 먼저 잡아 차례로 돌아야 한다.
    """
    from app.domains.memory.observation.repository import ObservationDomain
    from app.domains.memory.store.db_store import DbMemoryStore

    child_id, [(profile_a, obs_a), (profile_b, obs_b)] = two_linked
    async with (
        AsyncSession(engine, expire_on_commit=False) as run_s,
        AsyncSession(engine, expire_on_commit=False) as curator_s,
    ):
        store = DbMemoryStore(run_s, child_id=child_id, today=TODAY)
        # run — B 의 관찰을 지운다. 커밋 전이라 B 기억 행 잠금을 쥐고 있다
        assert await store.delete_observation(
            domain=ObservationDomain.FOOD, observation_id=str(obs_b)
        )

        async def curator():  # trigger.py 와 같은 순서 — 아이 잠금 → 연결된 기억들 재계산 → 커밋
            await service.lock_child_memory(curator_s, child_id)
            for pid in (profile_a, profile_b):
                await service.refresh_profile(curator_s, profile_id=pid, today=TODAY)
            await curator_s.commit()

        curator_task = asyncio.create_task(curator())
        await asyncio.sleep(0.3)  # 아이 잠금이 없으면 Curator 가 이 사이 A 를 잠그고 B 를 기다린다

        # run — 이어서 A 의 관찰을 지운다
        assert await store.delete_observation(
            domain=ObservationDomain.FOOD, observation_id=str(obs_a)
        )
        await run_s.commit()
        await asyncio.wait_for(curator_task, timeout=5)  # 교착이면 여기서 DeadlockDetected

    # 둘 다 끝났다 — 묶인 관찰이 없어진 기억은 O=0 이라 후보 그대로
    assert (await _stored(profile_a))[0] == ProfileState.CANDIDATE
    assert (await _stored(profile_b))[0] == ProfileState.CANDIDATE


async def test_run_이_연결_안_된_관찰을_고친_뒤_Curator_가_그걸_연결하려_해도_교착하지_않는다(
    two_linked,
):
    """연결 안 된 관찰을 고칠 때는 다시 계산할 기억이 없다 — 그래도 아이 잠금을 먼저 잡아야 한다.

    run 이 연결 안 된 X 를 고쳐 X 의 행 잠금을 쥔 사이, Curator 가 아이 잠금을 쥐고 X 를 연결하려
    X 를 기다린다. 그 뒤 run 이 연결된 Y 를 고치며 아이 잠금을 기다리면 둘이 서로를 기다린다.
    """
    from sqlalchemy import update

    from app.domains.memory.observation.repository import ObservationDomain
    from app.domains.memory.store.db_store import DbMemoryStore

    child_id, [(profile_a, obs_x), (_, obs_y)] = two_linked
    async with AsyncSession(engine) as unlink:  # X 를 "Curator 가 아직 연결 안 한 관찰" 로 만든다
        await unlink.execute(
            update(ObservationFood).where(ObservationFood.id == obs_x).values(affinity_id=None)
        )
        await unlink.commit()

    async with (
        AsyncSession(engine, expire_on_commit=False) as run_s,
        AsyncSession(engine, expire_on_commit=False) as curator_s,
    ):
        store = DbMemoryStore(run_s, child_id=child_id, today=TODAY)
        # run — 연결 안 된 X 를 지운다. 커밋 전이라 X 행 잠금을 쥐고 있다
        assert await store.delete_observation(
            domain=ObservationDomain.FOOD, observation_id=str(obs_x)
        )

        async def curator():  # 아이 잠금 → 연결 안 된 X 를 기억 A 에 연결 → 커밋
            await service.lock_child_memory(curator_s, child_id)
            await curator_s.execute(
                update(ObservationFood)
                .where(ObservationFood.id == obs_x)
                .values(affinity_id=profile_a)
            )
            await curator_s.commit()

        curator_task = asyncio.create_task(curator())
        await asyncio.sleep(0.3)

        # run — 이어서 연결된 Y 를 지운다 (기억 재계산 → 아이 잠금)
        assert await store.delete_observation(
            domain=ObservationDomain.FOOD, observation_id=str(obs_y)
        )
        await run_s.commit()
        await asyncio.wait_for(curator_task, timeout=5)


async def test_새_기록_저장은_Curator_를_기다리지_않는다(two_linked):
    """아이 잠금은 관찰을 고치거나 지울 때만 잡는다 — 새 관찰 저장까지 잡으면, 보호자가 연달아
    입력할 때마다 앞 입력의 Curator(임베딩 호출 몇 초)를 기다려 기록 단계가 느려진다.
    """
    from app.agents.common.datetime_rules import DateRange
    from app.domains.memory.store.db_store import DbMemoryStore

    child_id, _ = two_linked
    async with (
        AsyncSession(engine) as curator_s,
        AsyncSession(engine, expire_on_commit=False) as run_s,
    ):
        await service.lock_child_memory(curator_s, child_id)  # Curator 가 도는 중
        store = DbMemoryStore(run_s, child_id=child_id, today=TODAY)
        create = store.create_observation(
            domain="food",
            child_id=child_id,
            source_writer=None,
            raw_text="test",
            observed_on=TODAY,
            observed_range=DateRange(start=TODAY, end=TODAY + timedelta(days=1)),
            fields={"subject": "새것", "polarity": 1, "confidence_source": "parent_direct"},
        )
        await asyncio.wait_for(create, timeout=1)  # 아이 잠금을 기다리면 여기서 시간 초과
        await run_s.rollback()
        await curator_s.rollback()


@pytest.mark.parametrize("change", ["update", "delete"])
async def test_관찰을_고치는_run_은_Curator_가_끝날_때까지_기다린다(two_linked, change):
    """위의 짝 — 고치기 · 지우기는 아이 잠금을 기다린다.

    이게 없으면 교착 테스트가 타이밍 때문에 우연히 통과해도 알 수 없다.
    """
    from app.domains.memory.observation.repository import ObservationDomain
    from app.domains.memory.store.db_store import DbMemoryStore

    child_id, [(_, obs_a), _] = two_linked
    async with (
        AsyncSession(engine) as curator_s,
        AsyncSession(engine, expire_on_commit=False) as run_s,
    ):
        await service.lock_child_memory(curator_s, child_id)  # Curator 가 도는 중
        store = DbMemoryStore(run_s, child_id=child_id, today=TODAY)
        target = {"domain": ObservationDomain.FOOD, "observation_id": str(obs_a)}
        pending = asyncio.create_task(
            store.update_observation(**target, fields={"subject": "바뀜"})
            if change == "update"
            else store.delete_observation(**target)
        )
        await asyncio.sleep(0.3)
        assert not pending.done()  # Curator 가 쥐고 있는 동안은 기다린다

        await curator_s.commit()  # Curator 끝 — 잠금이 풀린다
        assert await asyncio.wait_for(pending, timeout=5)
        await run_s.rollback()
