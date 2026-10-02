"""Curator 단계별 독립 검증 + 전체 흐름 E2E.

각 단계를 독립적으로 검증한 뒤, 전체가 이어진 E2E 에서는
매 단계 후 중간 상태를 단언한다.

단계 구분:
  Stage 1 — 임베딩: 벡터 없는 관찰을 찾아 벡터를 붙인다
  Stage 2 — 연결: 관찰을 Profile 에 붙이거나 새 Profile 을 만든다
  Stage 3 — 상태 재계산: 연결된 Profile 의 상태를 갱신한다
  E2E    — Stage 1→2→3 을 한 번에 돌리고 매 단계 후 중간 상태를 확인한다
"""

from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.rules.profile import STRENGTH_DEFAULT


# ---------------------------------------------------------------------------
# 공통 픽스처 · 헬퍼
# ---------------------------------------------------------------------------


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


def _food(child_id, *, subject="사과", polarity=1, observed_on=date(2026, 9, 26)):
    return ObservationFood(
        child_id=child_id,
        raw_text="test",
        subject=subject,
        polarity=polarity,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(observed_on, observed_on + timedelta(days=1)),
    )


class _FakeEmbedder:
    """호출 횟수를 세는 가짜 임베더."""

    def __init__(self):
        self.call_count = 0
        self.last_texts: list[str] = []

    async def embed(self, texts):
        self.call_count += 1
        self.last_texts = list(texts)
        return [[0.1] * 1536 for _ in texts]


class _FakeJudge:
    async def judge(self, *, subject, domain, candidates):
        from app.agents.curator.embedding.judge import JudgeAnswer

        return JudgeAnswer(choice="none", model="fake")


# ---------------------------------------------------------------------------
# Stage 1 — 임베딩 단계 독립 검증
# ---------------------------------------------------------------------------


class TestStage1Embedding:
    """벡터 없는 관찰을 찾아 벡터를 붙인다. 연결은 하지 않는다."""

    async def test_벡터_없는_관찰이_임베딩_대상(self, session, family):
        _, child = family
        obs = _food(child.id)
        session.add(obs)
        await session.flush()

        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        targets = await store.list_unembedded(child_id=child.id)

        assert len(targets) == 1
        assert targets[0].embedding is None

    async def test_임베딩_후_벡터가_저장되고_대상에서_빠진다(self, session, family):
        _, child = family
        obs = _food(child.id)
        session.add(obs)
        await session.flush()

        from app.agents.curator.embedding.embed_step import embed_pending
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        embedder = _FakeEmbedder()
        result = await embed_pending(store, embedder, child_id=child.id)

        assert len(result.embedded) == 1
        assert embedder.call_count == 1

        # DB 에 벡터가 실제로 저장됨
        await session.refresh(obs)
        assert obs.embedding is not None

        # 더 이상 임베딩 대상이 아님
        assert await store.list_unembedded(child_id=child.id) == []

    async def test_같은_subject는_한_번만_임베딩(self, session, family):
        _, child = family
        for _ in range(3):
            session.add(_food(child.id, subject="사과"))
        await session.flush()

        from app.agents.curator.embedding.embed_step import embed_pending
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        embedder = _FakeEmbedder()
        await embed_pending(store, embedder, child_id=child.id)

        assert embedder.call_count == 1
        assert embedder.last_texts == ["사과"]  # 중복 제거

    async def test_임베딩_실패해도_관찰은_그대로(self, session, family):
        """API 실패 시 벡터가 저장되지 않고, 다음 실행에서 다시 대상이 된다."""
        _, child = family
        obs = _food(child.id)
        session.add(obs)
        await session.flush()

        from app.agents.common.llm_client import LLMError
        from app.agents.curator.embedding.embed_step import embed_pending
        from app.domains.memory.curator.db_store import DbCuratorStore

        class _FailingEmbedder:
            async def embed(self, texts):
                raise LLMError("test")

        store = DbCuratorStore(session)
        result = await embed_pending(store, _FailingEmbedder(), child_id=child.id)

        assert len(result.failed) == 1
        await session.refresh(obs)
        assert obs.embedding is None  # 저장 안 됨

        # 다음 실행에서 다시 대상
        assert len(await store.list_unembedded(child_id=child.id)) == 1


# ---------------------------------------------------------------------------
# Stage 2 — 연결 단계 독립 검증 (DB 기반)
# ---------------------------------------------------------------------------


class TestStage2LinkExactName:
    """이름이 같으면 판정기 없이 연결한다."""

    async def test_이름_일치_연결(self, session, family):
        _, child = family
        vec = [0.1] * 1536
        # 기존 Profile
        profile = ProfileAffinity(
            child_id=child.id, merge_key="사과", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=STRENGTH_DEFAULT,
            last_observed_on=date(2026, 9, 20), embedding=vec,
        )
        session.add(profile)
        await session.flush()
        # 같은 이름의 새 관찰 (벡터 있고 미연결)
        obs = _food(child.id, subject="사과")
        obs.embedding = vec
        session.add(obs)
        await session.flush()

        from app.agents.curator.embedding.link_step import link_pending
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_pending(store, None, child_id=child.id)

        assert len(result.outcomes) == 1
        assert result.outcomes[0].status == "linked"
        assert result.outcomes[0].match == "exact"
        assert result.outcomes[0].affinity_id == str(profile.id)


class TestStage2LinkNewCandidate:
    """후보가 없으면 새 candidate 를 만든다."""

    async def test_후보_없으면_생성(self, session, family):
        _, child = family
        obs = _food(child.id, subject="사과")
        obs.embedding = [0.1] * 1536
        session.add(obs)
        await session.flush()

        from app.agents.curator.embedding.link_step import link_pending
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_pending(store, None, child_id=child.id)

        assert len(result.outcomes) == 1
        assert result.outcomes[0].status == "created"
        assert result.outcomes[0].match == "new"

        # DB 에 Profile 이 실제로 생김
        pid = UUID(result.outcomes[0].affinity_id)
        profile = await session.get(ProfileAffinity, pid)
        assert profile is not None
        assert profile.state == ProfileState.CANDIDATE
        assert profile.merge_key == "사과"


class TestStage2LinkSameRunChaining:
    """같은 실행에서 앞에서 만든 Profile 이 다음 관찰의 후보가 된다."""

    async def test_같은_이름_3건이_하나의_profile에_연결(self, session, family):
        _, child = family
        vec = [0.1] * 1536
        for i in range(3):
            obs = _food(child.id, subject="사과", observed_on=date(2026, 9, 26) - timedelta(days=i))
            obs.embedding = vec
            session.add(obs)
        await session.flush()

        from app.agents.curator.embedding.link_step import link_pending
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        result = await link_pending(store, None, child_id=child.id)

        assert len(result.outcomes) == 3
        # 첫 번째: 후보 없음 → 생성
        assert result.outcomes[0].status == "created"
        # 나머지: 이름 일치 → 연결
        assert result.outcomes[1].status == "linked"
        assert result.outcomes[2].status == "linked"
        # 모두 같은 Profile
        pid = result.outcomes[0].affinity_id
        assert all(o.affinity_id == pid for o in result.outcomes)


class TestStage2LinkHoldUncertain:
    """판정기가 uncertain 이면 보류하고, 3회째에 새 candidate 를 만든다."""

    async def test_uncertain_보류_3회_후_생성(self, session, family):
        _, child = family
        vec = [0.1] * 1536
        # 기존 Profile (다른 이름)
        existing = ProfileAffinity(
            child_id=child.id, merge_key="배", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=STRENGTH_DEFAULT,
            last_observed_on=date(2026, 9, 20), embedding=vec,
        )
        session.add(existing)
        await session.flush()

        from app.agents.curator.embedding.judge import JudgeAnswer
        from app.agents.curator.embedding.link_step import link_pending
        from app.domains.memory.curator.db_store import DbCuratorStore

        class _UncertainJudge:
            async def judge(self, **kw):
                return JudgeAnswer(choice="uncertain", model="fake")

        judge = _UncertainJudge()
        store = DbCuratorStore(session)

        # 관찰 1건을 만들어 3번 실행
        obs = _food(child.id, subject="사과")
        obs.embedding = vec
        session.add(obs)
        await session.flush()

        for run in range(3):
            result = await link_pending(store, judge, child_id=child.id)
            if run < 2:
                assert result.outcomes[0].status == "held", f"run {run}: 보류여야 한다"
                assert result.outcomes[0].reason == "judge_uncertain"
            else:
                assert result.outcomes[0].status == "created", f"run {run}: 3회째에 생성"
                assert result.outcomes[0].match == "uncertain_limit"


# ---------------------------------------------------------------------------
# Stage 3 — 상태 재계산 독립 검증
# ---------------------------------------------------------------------------


class TestStage3Recompute:
    """Profile 에 연결된 관찰 수로 상태를 재계산한다."""

    async def test_O_3이면_confirmed_승격(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = ProfileAffinity(
            child_id=child.id, merge_key="사과", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=STRENGTH_DEFAULT,
            last_observed_on=today,
        )
        session.add(profile)
        await session.flush()
        for i in range(3):
            obs = _food(child.id, observed_on=today - timedelta(days=i))
            obs.affinity_id = profile.id
            obs.embedding = [0.1] * 1536
            session.add(obs)
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)

        assert result.state == ProfileState.CONFIRMED

    async def test_O_2면_candidate_유지(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        profile = ProfileAffinity(
            child_id=child.id, merge_key="사과", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=STRENGTH_DEFAULT,
            last_observed_on=today,
        )
        session.add(profile)
        await session.flush()
        for i in range(2):
            obs = _food(child.id, observed_on=today - timedelta(days=i))
            obs.affinity_id = profile.id
            obs.embedding = [0.1] * 1536
            session.add(obs)
        await session.flush()

        from app.domains.memory.profile.service import recompute_profile

        result = await recompute_profile(session, profile_id=profile.id, today=today)

        assert result.state == ProfileState.CANDIDATE

    async def test_last_observed_on_갱신(self, session, family):
        _, child = family
        today = date(2026, 9, 26)
        old_date = today - timedelta(days=10)
        profile = ProfileAffinity(
            child_id=child.id, merge_key="사과", domain=MemoryDomain.FOOD,
            state=ProfileState.CANDIDATE, polarity=1, strength=STRENGTH_DEFAULT,
            last_observed_on=old_date,
        )
        session.add(profile)
        await session.flush()
        obs = _food(child.id, observed_on=today)
        obs.affinity_id = profile.id
        obs.embedding = [0.1] * 1536
        session.add(obs)
        await session.flush()

        from app.domains.memory.profile.repository import get_latest_active_observed_on

        latest = await get_latest_active_observed_on(
            session, affinity_id=profile.id, domain=profile.domain,
        )

        assert latest == today
        assert latest != old_date


# ---------------------------------------------------------------------------
# E2E — Stage 1→2→3 전체 흐름 + 매 단계 중간 상태 확인
# ---------------------------------------------------------------------------


class TestE2EFullFlow:
    """관찰 생성 → 임베딩 → 연결 → 승격까지 한 흐름.
    매 단계 후 중간 상태를 단언한다."""

    async def test_관찰_3건_전체_흐름(self, session, family):
        _, child = family
        today = date(2026, 9, 26)

        # ── 준비: 관찰 3건 저장 ──
        observations = []
        for i in range(3):
            obs = _food(child.id, subject="사과", observed_on=today - timedelta(days=i))
            session.add(obs)
            observations.append(obs)
        await session.flush()

        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)

        # ── 검증: 초기 상태 ──
        for obs in observations:
            assert obs.embedding is None, "아직 벡터 없음"
            assert obs.affinity_id is None, "아직 미연결"
        profiles_before = (await session.scalars(
            select(ProfileAffinity).where(ProfileAffinity.child_id == child.id)
        )).all()
        assert len(profiles_before) == 0, "Profile 아직 없음"

        # ── Stage 1: 임베딩 ──
        from app.agents.curator.embedding.embed_step import embed_pending

        embedder = _FakeEmbedder()
        embed_result = await embed_pending(store, embedder, child_id=child.id)

        assert len(embed_result.embedded) == 3, "3건 모두 임베딩됨"
        assert embedder.call_count == 1, "API 호출은 1번 (같은 subject)"
        for obs in observations:
            await session.refresh(obs)
            assert obs.embedding is not None, "벡터가 DB 에 저장됨"
            assert obs.affinity_id is None, "아직 미연결 — 임베딩 단계는 연결 안 함"

        # ── Stage 2: 연결 ──
        from app.agents.curator.embedding.link_step import link_pending

        link_result = await link_pending(store, _FakeJudge(), child_id=child.id)

        assert len(link_result.outcomes) == 3
        assert link_result.outcomes[0].status == "created", "첫 관찰: 후보 없음 → 새 Profile"
        assert link_result.outcomes[1].status == "linked", "둘째: 이름 일치 → 연결"
        assert link_result.outcomes[2].status == "linked", "셋째: 이름 일치 → 연결"

        profile_id = UUID(link_result.outcomes[0].affinity_id)
        for obs in observations:
            await session.refresh(obs)
            assert obs.affinity_id == profile_id, "3건 모두 같은 Profile"

        profile = await session.get(ProfileAffinity, profile_id)
        assert profile.state == ProfileState.CANDIDATE, "연결 단계는 state 를 안 건드림"

        # ── Stage 3: 상태 재계산 ──
        from app.agents.curator.embedding.linker import LinkResult
        from app.agents.curator.embedding.link_step import LinkOutcome
        from app.domains.memory.curator.recompute import recompute_after_linking

        # linker 가 만드는 LinkResult 를 수동 조립 (Stage 2 결과로)
        full_result = LinkResult(
            child_id=child.id,
            outcomes=link_result.outcomes,
        )
        await recompute_after_linking(session, result=full_result, today=today)

        await session.refresh(profile)
        assert profile.state == ProfileState.CONFIRMED, "O=3 → confirmed 승격"
        assert profile.strength == pytest.approx(STRENGTH_DEFAULT * 1.10), "승격 보너스 +10%"
        assert profile.last_observed_on == today, "가장 최근 관찰 날짜"

    async def test_link_observations_로_1_2_3_한_번에(self, session, family):
        """link_observations 가 Stage 1+2 를 한 번에 돌리고,
        recompute_after_linking 이 Stage 3 을 처리한다."""
        _, child = family
        today = date(2026, 9, 26)
        for i in range(3):
            session.add(_food(child.id, subject="사과", observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore
        from app.domains.memory.curator.recompute import recompute_after_linking

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        assert result.embed_calls == 1
        assert len(result.affected_profile_ids) == 1

        await recompute_after_linking(session, result=result, today=today)

        profile = await session.get(ProfileAffinity, UUID(result.affected_profile_ids[0]))
        assert profile.state == ProfileState.CONFIRMED

    async def test_두_subject가_각각_별도_profile(self, session, family):
        """다른 subject 는 다른 Profile 이 된다."""
        _, child = family
        today = date(2026, 9, 26)
        for i in range(3):
            session.add(_food(child.id, subject="사과", observed_on=today - timedelta(days=i)))
        session.add(_food(child.id, subject="배", observed_on=today))
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore
        from app.domains.memory.curator.recompute import recompute_after_linking

        store = DbCuratorStore(session)
        result = await link_observations(store, _FakeEmbedder(), _FakeJudge(), child_id=child.id)

        await recompute_after_linking(session, result=result, today=today)

        assert len(result.affected_profile_ids) == 2
        profiles = []
        for pid in result.affected_profile_ids:
            p = await session.get(ProfileAffinity, UUID(pid))
            profiles.append(p)

        apple = next(p for p in profiles if p.merge_key == "사과")
        pear = next(p for p in profiles if p.merge_key == "배")
        assert apple.state == ProfileState.CONFIRMED  # O=3
        assert pear.state == ProfileState.CANDIDATE  # O=1

    async def test_두_번째_실행은_이미_처리된_관찰을_건너뛴다(self, session, family):
        """한 번 돌린 뒤 같은 아이로 다시 돌리면 새 작업이 없다."""
        _, child = family
        today = date(2026, 9, 26)
        for i in range(3):
            session.add(_food(child.id, subject="사과", observed_on=today - timedelta(days=i)))
        await session.flush()

        from app.agents.curator.embedding.linker import link_observations
        from app.domains.memory.curator.db_store import DbCuratorStore

        store = DbCuratorStore(session)
        embedder = _FakeEmbedder()

        # 첫 실행
        r1 = await link_observations(store, embedder, _FakeJudge(), child_id=child.id)
        assert r1.embed_calls == 1
        assert len(r1.affected_profile_ids) == 1

        # 두 번째 실행
        r2 = await link_observations(store, embedder, _FakeJudge(), child_id=child.id)
        assert r2.embed_calls == 0, "임베딩 대상 없음"
        assert len(r2.outcomes) == 0, "연결 대상 없음"
