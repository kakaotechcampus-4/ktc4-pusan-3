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
from datetime import date, timedelta

import pytest
from sqlalchemy import select
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
        self, session: AsyncSession, family, monkeypatch,
    ):
        owner, child = family
        today = date(2026, 9, 26)

        # 관찰 저장
        await _save(
            session, child_id=child.id, parent_id=owner.id,
            subject="딸기", observed_on=today,
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
        obs_rows = (await session.scalars(
            select(ObservationFood).where(ObservationFood.child_id == child.id)
        )).all()
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
