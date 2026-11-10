"""남은 inactive 기록을 deleted 로 옮기는 마이그레이션 (#736 3단계).

    ① inactive 만 deleted 가 되고 검색 벡터가 빈다. 기억에 묶인 칸(affinity_id)은 그대로다
    ② 기억에 묶이지 않은 행 · 검색 벡터가 없는 표(생활 · 건강)도 에러 없이 옮긴다
    ③ 다른 상태(active · stand_alone · 원래 deleted)는 손대지 않는다
    ④ 기억은 다시 계산하지 않는다 — 옮기기 전과 후에 다시 계산한 결과
       (상태 · 강도 · 마지막 관찰일)가 같다

격리 스키마에 표를 손으로 만들지 않고, 마이그레이션이 만든 실제 표에서 테스트 트랜잭션 안에 돌린다.
발화 · 아이 정보는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from runpy import run_path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
    ObservationStatus,
    RoutineCategory,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.memory.profile.service import refresh_profile

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "90ac5aae7ad1_move_inactive_observations_to_deleted.py"
)
TODAY = date(2026, 11, 9)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
VECTOR = [0.1] * 1536


def _range(days_ago: int) -> Range:
    day = TODAY - timedelta(days=days_ago)
    return Range(day, day + timedelta(days=1), bounds="[)")


def _common(child_id, days_ago: int) -> dict:
    return {
        "child_id": child_id,
        "raw_text": "synthetic",
        "confidence_source": ConfidenceSource.PARENT_DIRECT,
        "observed_range": _range(days_ago),
    }


async def _migrate(session) -> None:
    connection = await session.connection()

    def upgrade(conn):
        migration = run_path(str(MIGRATION))
        with Operations.context(MigrationContext.configure(conn)):
            migration["upgrade"]()

    await connection.run_sync(upgrade)


async def test_inactive_만_deleted_로_옮기고_기억은_그대로다(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="옮기기", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    profile = ProfileAffinity(
        child_id=child.id,
        domain=MemoryDomain.FOOD,
        polarity=1,
        merge_key="사과",
        state=ProfileState.CONFIRMED,
        strength=0.55,
        last_observed_on=TODAY,
    )
    session.add(profile)
    await session.flush()

    def food(days_ago: int) -> ObservationFood:
        return ObservationFood(
            **_common(child.id, days_ago),
            subject="사과",
            polarity=1,
            affinity_id=profile.id,
            embedding=VECTOR,
        )

    # 기억 하나에 묶인 식사 — active 셋 · 잘못된 기록 하나(가장 최근) · 이번만 · 원래 지운 것
    active = [food(d) for d in (1, 3, 5)]
    wrong_food, stand_alone, deleted = food(0), food(2), food(4)
    # 기억에 묶이지 않은 잘못된 기록(교육 · 놀이) · 검색 벡터가 없는 표의 잘못된 기록
    loose = ObservationEducation(
        **_common(child.id, 1), subject="그림책", topic="그림책", embedding=VECTOR
    )
    play = ObservationActivity(
        **_common(child.id, 1), subject="블록", activity="블록 쌓기", embedding=VECTOR
    )
    routine = ObservationRoutine(
        **_common(child.id, 1), subject="양치", routine_category=RoutineCategory.SELF_CARE
    )
    health = ObservationHealth(**_common(child.id, 1), symptom=["기침"])
    rows = [*active, wrong_food, stand_alone, deleted, loose, play, routine, health]
    session.add_all(rows)
    await session.flush()
    for row, status in [
        (wrong_food, ObservationStatus.INACTIVE),
        (stand_alone, ObservationStatus.STAND_ALONE),
        (deleted, ObservationStatus.DELETED),
        (loose, ObservationStatus.INACTIVE),
        (play, ObservationStatus.INACTIVE),
        (routine, ObservationStatus.INACTIVE),
        (health, ObservationStatus.INACTIVE),
    ]:
        model = type(row)
        await session.execute(update(model).where(model.id == row.id).values(status=status))
    for model in {type(row) for row in rows}:
        await session.execute(
            update(model).where(model.child_id == child.id).values(updated_at=LONG_AGO)
        )

    await refresh_profile(session, profile_id=profile.id, today=TODAY)
    before = (profile.state, profile.strength, profile.last_observed_on)

    await _migrate(session)

    async def reread(row):
        return await session.get(type(row), row.id, populate_existing=True)

    for row in (wrong_food, loose, play, routine, health):
        moved = await reread(row)
        assert moved.status is ObservationStatus.DELETED
        assert moved.updated_at > LONG_AGO
    for row in (wrong_food, loose, play):
        assert (await reread(row)).embedding is None
    assert (await reread(wrong_food)).affinity_id == profile.id  # 묶인 칸은 store 삭제처럼 남는다
    assert (await reread(loose)).affinity_id is None

    for row, status in [
        *[(a, ObservationStatus.ACTIVE) for a in active],
        (stand_alone, ObservationStatus.STAND_ALONE),
        (deleted, ObservationStatus.DELETED),
    ]:
        kept = await reread(row)
        assert (kept.status, kept.updated_at) == (status, LONG_AGO)
        assert kept.embedding is not None

    for model in (
        ObservationFood,
        ObservationEducation,
        ObservationActivity,
        ObservationRoutine,
        ObservationHealth,
    ):
        left = await session.scalar(
            select(model.id).where(
                model.child_id == child.id, model.status == ObservationStatus.INACTIVE
            )
        )
        assert left is None

    await refresh_profile(session, profile_id=profile.id, today=TODAY)
    await session.refresh(profile)
    assert (profile.state, profile.strength, profile.last_observed_on) == before
    # 잘못된 기록(오늘)은 처음부터 마지막 관찰일에 들어가지 않았다
    assert before[2] == TODAY - timedelta(days=1)
