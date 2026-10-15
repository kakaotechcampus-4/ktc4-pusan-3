"""health_safety → Food · Activity 안전 포트 어댑터를 실제 DB 로 (#405)."""

from contextlib import asynccontextmanager
from datetime import date

import pytest

from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.safety.agent_reader import DbSafetyReader
from app.domains.safety.models import SafetyKind
from app.domains.safety.repository import create_safety, retract_safety


@pytest.fixture
async def family(session):
    owner, stranger = Parent(), Parent()
    session.add_all([owner, stranger])
    await session.flush()
    child = await create_child(
        session,
        owner_parent_id=owner.id,
        nickname="첫째",
        birth_date=date(2023, 1, 1),
        relation=ParentChildRelation.MOTHER,
    )
    other = await create_child(
        session,
        owner_parent_id=stranger.id,
        nickname="다른 아이",
        birth_date=date(2024, 1, 1),
        relation=ParentChildRelation.FATHER,
    )
    return owner, stranger, child, other


@pytest.fixture
async def safety_rows(session, family):
    owner, stranger, child, other = family

    async def add(child_id, parent_id, kind, label):
        return await create_safety(
            session, child_id=child_id, parent_id=parent_id, kind=kind, label=label
        )

    await add(child.id, owner.id, SafetyKind.ALLERGY, "우유")
    await add(child.id, owner.id, SafetyKind.CHRONIC_DISEASE, "유당불내증")
    await add(child.id, owner.id, SafetyKind.BEHAVIORAL, "높은 곳 무서워함")
    pollen = await add(child.id, owner.id, SafetyKind.ENVIRONMENTAL, "꽃가루")
    await retract_safety(session, child_id=child.id, safety_id=pollen.id, parent_id=owner.id)
    await add(other.id, stranger.id, SafetyKind.ALLERGY, "땅콩")
    await session.flush()
    return child


def reader(session) -> DbSafetyReader:
    @asynccontextmanager
    async def open_session():
        yield session  # 테스트 트랜잭션 안에서 읽는다 — 바깥 rollback 이 지운다

    return DbSafetyReader(open_session)


async def test_Food_는_active_만_kind_는_전부(session, safety_rows):
    entries = await reader(session).food_safety(child_id=safety_rows.id)

    assert sorted((e.kind, e.label, e.status) for e in entries) == [
        ("allergy", "우유", "active"),
        ("behavioral", "높은 곳 무서워함", "active"),
        ("chronic_disease", "유당불내증", "active"),
    ]


async def test_Activity_는_allergy_environmental_만_status_는_전부(session, safety_rows):
    entries = await reader(session).activity_safety(child_id=safety_rows.id)

    assert sorted((e.kind, e.label, e.status) for e in entries) == [
        ("allergy", "우유", "active"),
        ("environmental", "꽃가루", "retracted"),
    ]


async def test_다른_아이의_행은_넘기지_않는다(session, safety_rows):
    food = await reader(session).food_safety(child_id=safety_rows.id)
    activity = await reader(session).activity_safety(child_id=safety_rows.id)

    assert "땅콩" not in {e.label for e in (*food, *activity)}
