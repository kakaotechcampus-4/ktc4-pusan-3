"""trigger_curator_background 경로를 타는 통합 테스트.

기존 test_curator_db_pipeline.py 는 _run_curator 를 직접 부르고,
test_memory_to_curator.py 는 link_observations 를 직접 부른다.
이 테스트는 trigger_curator_background → _run_curator → advisory lock
→ link_observations → recompute 전체 경로를 검증한다.

trigger_curator_background 는 async_session_factory 로 별도 세션을 여는데,
conftest 의 session 은 바깥 트랜잭션으로 감싸 끝에 rollback 하므로
별도 세션에서는 그 관찰이 안 보인다. 그래서 async_session_factory 를
monkeypatch 해서 테스트 세션을 돌려준다.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ObservationFood
from app.domains.memory.observation.repository import ObservationDomain, create_observation
from app.domains.memory.profile.models import ProfileAffinity, ProfileState


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="트리거테스트", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


class _FakeEmbedder:
    async def embed(self, texts):
        return [[0.1] * 1536 for _ in texts]


class _FakeJudge:
    async def judge(self, *, subject, domain, candidates):
        from app.agents.curator.embedding.judge import JudgeAnswer

        return JudgeAnswer(choice="none", model="fake")


async def _save(session, *, child_id, parent_id, subject, observed_on):
    return await create_observation(
        session,
        domain=ObservationDomain.FOOD,
        child_id=child_id,
        source_writer=parent_id,
        raw_text=f"{subject} 관련 관찰",
        observed_range=Range(observed_on, observed_on + timedelta(days=1)),
        fields={
            "subject": subject,
            "polarity": 1,
            "confidence_source": "parent_direct",
        },
    )


class TestTriggerCuratorBackground:
    """trigger_curator_background → advisory lock → embed → link → recompute."""

    async def test_trigger가_관찰을_임베딩하고_프로필을_만든다(
        self,
        session: AsyncSession,
        family,
        monkeypatch,
    ):
        owner, child = family
        today = date(2026, 9, 26)

        # 관찰 저장
        await _save(
            session,
            child_id=child.id,
            parent_id=owner.id,
            subject="딸기",
            observed_on=today,
        )
        await session.flush()

        # async_session_factory를 monkeypatch — 테스트 세션을 돌려준다
        @asynccontextmanager
        async def fake_factory():
            yield session

        import app.domains.memory.curator.trigger as trigger_mod

        monkeypatch.setattr(trigger_mod, "async_session_factory", fake_factory)

        # trigger_curator_background 호출
        task = trigger_mod.trigger_curator_background(
            child_id=child.id,
            today=today,
            embedder=_FakeEmbedder(),
            judge=_FakeJudge(),
        )

        # background task 완료 대기
        await asyncio.wait_for(task, timeout=5)

        # 관찰에 embedding + affinity_id 채워졌는지
        obs_rows = (
            await session.scalars(
                select(ObservationFood).where(ObservationFood.child_id == child.id)
            )
        ).all()
        assert len(obs_rows) == 1
        assert obs_rows[0].embedding is not None, "embedding이 채워져야 한다"
        assert obs_rows[0].affinity_id is not None, "profile에 연결되어야 한다"

        # profile 생성 확인
        profile = await session.get(ProfileAffinity, obs_rows[0].affinity_id)
        assert profile is not None
        assert profile.merge_key == "딸기"
        assert profile.state == ProfileState.CANDIDATE

    # 동시 실행 테스트는 advisory lock 이 별도 세션에서 동작해야 하므로
    # conftest 의 rollback 세션으로는 검증할 수 없다.
    # advisory lock 의 직렬화는 DB 수준 동작이라 위 단건 테스트로 경로 확인만 한다.


@pytest.mark.parametrize(
    "birth, cutoff",
    [
        (date(2024, 1, 1), date(2025, 7, 1)),
        (date(2024, 8, 31), date(2026, 2, 28)),
        (date(2022, 8, 31), date(2024, 2, 29)),
    ],
)
async def test_관찰_시작일로_걸러_나중에_실행해도_연결하지_않는다(
    session, family, monkeypatch, birth, cutoff
):
    owner, child = family
    child.birth_date = birth
    for subject, start, end in [
        ("early", cutoff - timedelta(days=1), cutoff),
        ("spanning", cutoff - timedelta(days=1), cutoff + timedelta(days=2)),
        ("eligible", cutoff, cutoff + timedelta(days=1)),
    ]:
        await create_observation(
            session,
            domain=ObservationDomain.FOOD,
            child_id=child.id,
            source_writer=owner.id,
            raw_text="test",
            observed_range=Range(start, end),
            fields={"subject": subject, "polarity": 1, "confidence_source": "parent_direct"},
        )
    await session.flush()

    @asynccontextmanager
    async def fake_factory():
        yield session

    import app.domains.memory.curator.trigger as trigger_mod

    monkeypatch.setattr(trigger_mod, "async_session_factory", fake_factory)
    for today in (cutoff, cutoff + timedelta(days=365)):
        await trigger_mod.trigger_curator_background(
            child_id=child.id, today=today, embedder=_FakeEmbedder(), judge=_FakeJudge()
        )
        rows = (
            await session.scalars(
                select(ObservationFood).where(ObservationFood.child_id == child.id)
            )
        ).all()
        assert len(rows) == 3
        for row in rows:
            assert row.embedding is not None
            assert (row.affinity_id is not None) == (row.subject == "eligible")
        profiles = (
            await session.scalars(
                select(ProfileAffinity).where(ProfileAffinity.child_id == child.id)
            )
        ).all()
        assert [p.merge_key for p in profiles] == ["eligible"]


async def test_lock_획득_뒤에_바뀐_생일을_DB에서_읽는다(session, family, monkeypatch):
    owner, child = family
    await _save(
        session,
        child_id=child.id,
        parent_id=owner.id,
        subject="딸기",
        observed_on=date(2025, 7, 1),
    )
    await session.flush()
    execute = session.execute

    async def execute_with_birth_change(statement, *args, **kwargs):
        result = await execute(statement, *args, **kwargs)
        if "pg_advisory_xact_lock" in str(statement):
            # 잠금 대기가 끝났을 때 DB 생일은 바뀌었지만 ORM 캐시에는 옛 생일이 남은 상황.
            await execute(
                update(Child)
                .where(Child.id == child.id)
                .values(birth_date=date(2025, 1, 1))
                .execution_options(synchronize_session=False)
            )
        return result

    @asynccontextmanager
    async def fake_factory():
        yield session

    import app.domains.memory.curator.trigger as trigger_mod

    monkeypatch.setattr(session, "execute", execute_with_birth_change)
    monkeypatch.setattr(trigger_mod, "async_session_factory", fake_factory)
    await trigger_mod.trigger_curator_background(
        child_id=child.id,
        today=date(2026, 10, 14),
        embedder=_FakeEmbedder(),
        judge=_FakeJudge(),
    )
    row = (
        await session.scalars(select(ObservationFood).where(ObservationFood.child_id == child.id))
    ).one()
    assert row.embedding is not None
    assert row.affinity_id is None
    assert (
        await session.scalars(select(ProfileAffinity).where(ProfileAffinity.child_id == child.id))
    ).all() == []


@pytest.mark.parametrize("deleted", [False, True], ids=["missing", "soft_deleted"])
async def test_없거나_삭제된_아이는_실패_로그_없이_건너뛴다(
    session, family, monkeypatch, caplog, deleted
):
    owner, child = family
    child_id = child.id
    if deleted:
        await _save(
            session,
            child_id=child_id,
            parent_id=owner.id,
            subject="딸기",
            observed_on=date(2026, 10, 14),
        )
        child.deleted_at = datetime.now(timezone.utc)
    else:
        await session.delete(child)
    await session.flush()

    @asynccontextmanager
    async def fake_factory():
        yield session

    import app.domains.memory.curator.trigger as trigger_mod

    monkeypatch.setattr(trigger_mod, "async_session_factory", fake_factory)
    await trigger_mod.trigger_curator_background(
        child_id=child_id,
        today=date(2026, 10, 14),
        embedder=_FakeEmbedder(),
        judge=_FakeJudge(),
    )
    assert "curator 실행 실패" not in caplog.text
    assert (
        await session.scalars(select(ProfileAffinity).where(ProfileAffinity.child_id == child_id))
    ).all() == []
    if deleted:
        row = (
            await session.scalars(
                select(ObservationFood).where(ObservationFood.child_id == child_id)
            )
        ).one()
        assert row.embedding is None
        assert row.affinity_id is None
