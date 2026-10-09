"""실제 Jev 판정기로 연결 → 승격 → 감쇠 전체 흐름.

실행: OPENROUTER_API_KEY=sk-or-... uv run pytest tests/integration/test_curator_live_scenario.py -v -m live
키 없으면 전부 skip.

임베딩은 가짜 (벡터는 연결 판단에 안 쓰임). 판정만 실제 Jev.
날짜를 조작해 승격·감쇠·부활을 한 테스트에서 추적한다.
"""

import os
from datetime import date, timedelta
from uuid import UUID

import pytest
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ConfidenceSource, ObservationFood
from app.domains.memory.profile.models import ProfileAffinity, ProfileState
from app.rules.profile import STRENGTH_DEFAULT

_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
live = pytest.mark.live
pytestmark = pytest.mark.skipif(not _API_KEY, reason="OPENROUTER_API_KEY 없음")


class _FakeEmbedder:
    async def embed(self, texts):
        return [[0.1] * 1536 for _ in texts]


def _make_judge():
    """실제 JevJudge. 환경변수에서 키를 읽는다."""
    from app.agents.common.config import AgentSettings

    settings = AgentSettings(
        CURATOR_JUDGE_API_KEY=_API_KEY,
        CURATOR_JUDGE_BASE_URL="https://openrouter.ai/api/alpha",
        CURATOR_JUDGE_MODEL="typesafe/jev-1.13",
    )
    from app.agents.curator.embedding.jev import JevJudge

    return JevJudge(settings)


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="시나리오", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


def _food(child_id, *, subject, polarity=1, observed_on):
    return ObservationFood(
        child_id=child_id,
        raw_text=f"{subject} 관련 관찰",
        subject=subject,
        polarity=polarity,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(observed_on, observed_on + timedelta(days=1)),
    )


async def _curator_run(session, child_id, today, judge):
    from app.agents.curator.embedding.linker import link_observations
    from app.domains.memory.curator.db_store import DbCuratorStore
    from app.domains.memory.curator.recompute import recompute_after_linking

    store = DbCuratorStore(session)
    result = await link_observations(store, _FakeEmbedder(), judge, child_id=child_id)
    if result.affected_profile_ids:
        await recompute_after_linking(session, result=result, today=today)
    return result


# ---------------------------------------------------------------------------
# 같은 대상 판정 — Jev 가 동의어를 잡는가
# ---------------------------------------------------------------------------


@live
class TestLiveIdentityJudgment:
    """Jev 가 같은 대상을 올바르게 판정하는지 확인한다."""

    async def test_당근_홍당무_같은_대상으로_판정(self, session, family):
        """당근 → 홍당무 Profile 에 연결돼야 한다."""
        _, child = family
        day1 = date(2026, 9, 20)
        judge = _make_judge()

        # 홍당무 Profile 이 먼저 있다 (관찰 1건)
        session.add(_food(child.id, subject="홍당무", observed_on=day1))
        await session.flush()
        r1 = await _curator_run(session, child.id, day1, judge)
        hongdangmu_pid = r1.affected_profile_ids[0]

        # "당근" 관찰 → Jev 가 홍당무와 같다고 판정해야 함
        day2 = day1 + timedelta(days=1)
        session.add(_food(child.id, subject="당근", observed_on=day2))
        await session.flush()
        r2 = await _curator_run(session, child.id, day2, judge)

        # 새 Profile 이 안 생기고 홍당무에 연결
        linked = [o for o in r2.outcomes if o.status == "linked"]
        assert len(linked) == 1, f"연결 1건이어야 한다: {r2.outcomes}"
        assert linked[0].affinity_id == hongdangmu_pid
        assert linked[0].match in ("judged", "reversed"), f"Jev 판정: {linked[0].match}"

    async def test_계란_달걀_같은_대상으로_판정(self, session, family):
        _, child = family
        day1 = date(2026, 9, 20)
        judge = _make_judge()

        session.add(_food(child.id, subject="계란", observed_on=day1))
        await session.flush()
        r1 = await _curator_run(session, child.id, day1, judge)
        egg_pid = r1.affected_profile_ids[0]

        day2 = day1 + timedelta(days=1)
        session.add(_food(child.id, subject="달걀", observed_on=day2))
        await session.flush()
        r2 = await _curator_run(session, child.id, day2, judge)

        linked = [o for o in r2.outcomes if o.status == "linked"]
        assert len(linked) == 1
        assert linked[0].affinity_id == egg_pid

    async def test_딸기_딸기잼_다른_대상으로_판정(self, session, family):
        """딸기잼은 딸기와 다른 대상 → 새 Profile."""
        _, child = family
        day1 = date(2026, 9, 20)
        judge = _make_judge()

        session.add(_food(child.id, subject="딸기", observed_on=day1))
        await session.flush()
        await _curator_run(session, child.id, day1, judge)

        day2 = day1 + timedelta(days=1)
        session.add(_food(child.id, subject="딸기잼", observed_on=day2))
        await session.flush()
        r2 = await _curator_run(session, child.id, day2, judge)

        # 딸기잼은 새 Profile 로 생성
        created = [o for o in r2.outcomes if o.status == "created"]
        assert len(created) == 1, f"새 Profile 이어야 한다: {r2.outcomes}"


# ---------------------------------------------------------------------------
# 전체 생애 주기 — Jev 판정 + 승격 + 감쇠 + 부활
# ---------------------------------------------------------------------------


@live
class TestLiveFullLifecycle:
    """실제 Jev 로 동의어를 연결하고, 날짜 조작으로 승격→archived→부활까지."""

    async def test_당근_성향_전체_생애주기(self, session, family):
        _, child = family
        judge = _make_judge()
        day1 = date(2026, 9, 1)

        # ── Step 1: "홍당무" 첫 관찰 → candidate ──
        session.add(_food(child.id, subject="홍당무", observed_on=day1))
        await session.flush()
        r = await _curator_run(session, child.id, day1, judge)
        pid = UUID(r.affected_profile_ids[0])
        p = await session.get(ProfileAffinity, pid)
        assert p.state == ProfileState.CANDIDATE
        assert p.strength == pytest.approx(STRENGTH_DEFAULT)
        print(f"  Step 1: {p.merge_key} = {p.state}, strength={p.strength:.3f}")

        # ── Step 2: "당근" (Jev 판정) → 같은 Profile 에 연결, O=2 → candidate ──
        day3 = day1 + timedelta(days=2)
        session.add(_food(child.id, subject="당근", observed_on=day3))
        await session.flush()
        r = await _curator_run(session, child.id, day3, judge)
        await session.refresh(p)
        assert p.state == ProfileState.CANDIDATE
        assert len(r.created_profile_ids) == 0, "새 Profile 안 생겨야 함"
        print(f"  Step 2: O=2, state={p.state}, strength={p.strength:.3f}")

        # ── Step 3: "홍당무" 세 번째 → O=3 → confirmed ──
        day5 = day1 + timedelta(days=4)
        session.add(_food(child.id, subject="홍당무", observed_on=day5))
        await session.flush()
        r = await _curator_run(session, child.id, day5, judge)
        await session.refresh(p)
        s_confirmed = STRENGTH_DEFAULT * 1.10
        assert p.state == ProfileState.CONFIRMED
        assert p.strength == pytest.approx(s_confirmed)
        print(f"  Step 3: O=3 → confirmed, strength={p.strength:.3f}")

        # ── Step 4: 22일 경과 → archived ──
        day_archived = day5 + timedelta(days=22)
        from app.domains.memory.profile.service import recompute_profile

        await recompute_profile(session, profile_id=pid, today=day_archived)
        await session.refresh(p)
        s_archived = s_confirmed * 0.90
        assert p.state == ProfileState.ARCHIVED
        assert p.strength == pytest.approx(s_archived)
        print(f"  Step 4: 22일 경과 → archived, strength={p.strength:.3f}")

        # ── Step 5: 새 관찰 3건 → confirmed 부활 ──
        day_revival = day_archived + timedelta(days=1)
        for i in range(3):
            session.add(
                _food(child.id, subject="홍당무", observed_on=day_revival + timedelta(days=i))
            )
        await session.flush()
        day_last = day_revival + timedelta(days=2)
        r = await _curator_run(session, child.id, day_last, judge)
        await session.refresh(p)
        s_revived = s_archived * 1.10
        assert p.state == ProfileState.CONFIRMED
        assert p.strength == pytest.approx(s_revived)
        assert p.last_observed_on == day_last
        print(f"  Step 5: 부활 → confirmed, strength={p.strength:.3f}")

        print(
            f"\n  최종: {p.merge_key} strength 변화: "
            f"0.500 → 0.550(승격) → 0.495(archived) → 0.545(부활)"
        )
