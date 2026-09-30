"""DB repository 로 관찰을 저장한 뒤 Curator 가 동작하는지 검증.

InMemoryStore 가 아닌 실제 DB create_observation → Curator → recompute 전체 경로.
DB 저장소 전환 시 이 테스트가 통과하면 파이프라인 연결이 안전하다.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ObservationFood
from app.domains.memory.observation.repository import ObservationDomain, create_observation
from app.domains.memory.profile.models import ProfileAffinity, ProfileState
from app.rules.profile import STRENGTH_DEFAULT


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="DB파이프라인", birth_date=date(2023, 1, 1))
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


async def _save_observation(session, *, child_id, parent_id, subject, observed_on):
    """실제 DB repository 로 관찰을 저장한다."""
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


async def _run_curator(session, child_id, today):
    from app.agents.curator.embedding.linker import link_observations
    from app.domains.memory.curator.db_store import DbCuratorStore
    from app.domains.memory.curator.recompute import recompute_after_linking

    store = DbCuratorStore(session)
    result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child_id)
    if result.affected_profile_ids:
        await recompute_after_linking(session, result=result, today=today)
    return result


class TestDbRepositoryToCurator:
    """create_observation (DB repository) → Curator → Profile 상태 전이."""

    async def test_DB_저장_3건_후_Curator_가_confirmed_까지(self, session, family):
        owner, child = family
        today = date(2026, 9, 26)

        # ── DB repository 로 관찰 3건 저장 ──
        for i in range(3):
            record = await _save_observation(
                session,
                child_id=child.id,
                parent_id=owner.id,
                subject="사과",
                observed_on=today - timedelta(days=i),
            )
            # DB 에 실제로 저장됐는지 확인
            row = await session.get(ObservationFood, record.id)
            assert row is not None
            assert row.embedding is None  # 아직 벡터 없음
            assert row.affinity_id is None  # 아직 미연결

        # ── Curator 실행 ──
        result = await _run_curator(session, child.id, today)

        # ── 검증: 3건 모두 처리됨 ──
        assert result.embed_calls == 1  # 같은 subject 한 번만 임베딩
        assert len(result.affected_profile_ids) == 1

        # ── 검증: observation 에 벡터 + affinity_id 채워짐 ──
        obs_rows = (await session.scalars(
            select(ObservationFood).where(ObservationFood.child_id == child.id)
        )).all()
        for row in obs_rows:
            assert row.embedding is not None, "벡터가 저장됨"
            assert row.affinity_id is not None, "Profile 에 연결됨"

        # ── 검증: Profile 이 confirmed ──
        from uuid import UUID

        pid = UUID(result.affected_profile_ids[0])
        profile = await session.get(ProfileAffinity, pid)
        assert profile.state == ProfileState.CONFIRMED
        assert profile.strength == pytest.approx(STRENGTH_DEFAULT * 1.10)
        assert profile.last_observed_on == today
        assert profile.merge_key == "사과"

    async def test_DB_저장_후_삭제_하면_Curator_가_무시(self, session, family):
        """저장 → 삭제 → Curator: deleted 관찰은 건너뛴다."""
        owner, child = family
        today = date(2026, 9, 26)

        record = await _save_observation(
            session,
            child_id=child.id,
            parent_id=owner.id,
            subject="배",
            observed_on=today,
        )

        # soft delete
        from app.domains.memory.observation.repository import delete_observation

        await delete_observation(
            session, domain=ObservationDomain.FOOD,
            child_id=child.id, observation_id=record.id,
        )

        # Curator 실행 — deleted 관찰은 대상이 아님
        result = await _run_curator(session, child.id, today)
        assert result.embed_calls == 0
        assert len(result.outcomes) == 0

    async def test_DB_저장_여러_subject가_각각_Profile(self, session, family):
        owner, child = family
        today = date(2026, 9, 26)

        for subject in ["사과", "사과", "사과", "당근"]:
            await _save_observation(
                session,
                child_id=child.id,
                parent_id=owner.id,
                subject=subject,
                observed_on=today,
            )

        result = await _run_curator(session, child.id, today)

        assert len(result.affected_profile_ids) == 2  # 사과, 당근

        from uuid import UUID

        profiles = []
        for pid_str in result.affected_profile_ids:
            p = await session.get(ProfileAffinity, UUID(pid_str))
            profiles.append(p)

        apple = next(p for p in profiles if p.merge_key == "사과")
        carrot = next(p for p in profiles if p.merge_key == "당근")
        assert apple.state == ProfileState.CONFIRMED  # O=3
        assert carrot.state == ProfileState.CANDIDATE  # O=1
