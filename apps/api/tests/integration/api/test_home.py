"""홈 API — #259.

기록 수 집계는 `test_observation_repository.py`, 기억 문구는 `test_affinity_reason`,
예상 질문은 `test_home_prompts` 가 건다. 여기는 API 가 더하는 것만 본다: 아이 접근 확인,
건강 제외, 이번 주 경계, 오늘 · 다가오는 일정, 하이라이트로 고르는 기억.
"""

import uuid
from datetime import datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import KST, today_kst
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.observation.repository import create_observation
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType
from app.domains.schedule.repository import create_event
from tests.integration.api.conftest import Bearer, issue_bearer, link_child


async def _food(session, child_id, day, affinity_id=None, status=ObservationStatus.ACTIVE):
    session.add(
        ObservationFood(
            child_id=child_id,
            raw_text="원문",
            subject="계란말이",
            confidence_source=ConfidenceSource.PARENT_DIRECT,
            observed_range=Range(day, day + timedelta(days=1), bounds="[)"),
            affinity_id=affinity_id,
            status=status,
        )
    )
    await session.flush()


async def _affinity(session, child_id, key, state, last_observed_on):
    row = ProfileAffinity(
        child_id=child_id,
        merge_key=key,
        domain=MemoryDomain.FOOD,
        state=state,
        last_observed_on=last_observed_on,
    )
    session.add(row)
    await session.flush()
    return row


async def test_other_childs_home_is_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)

    resp = await db_client.get(f"/api/v1/children/{other_cid}/home", headers=headers)

    assert resp.status_code == 403


async def test_counts_skip_health_and_split_at_monday(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    today = today_kst()
    last_sunday = today - timedelta(days=today.weekday() + 1)
    await _food(session, cid, today)
    await _food(session, cid, last_sunday)  # 지난주 — 전체에는 들어가고 이번 주에는 아니다
    await _food(session, cid, today, status=ObservationStatus.INACTIVE)  # 고친 기록도 센다
    await _food(session, cid, today, status=ObservationStatus.DELETED)  # 지운 기록은 안 센다
    await create_observation(
        session,
        domain="health",
        child_id=cid,
        source_writer=parent_id,
        raw_text="원문",
        observed_range=Range(today, today + timedelta(days=1), bounds="[)"),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, "symptom": ["기침"]},
    )

    data = (await db_client.get(f"/api/v1/children/{cid}/home", headers=headers)).json()

    assert (data["observation_count"], data["week_count"]) == (3, 2)


async def test_events_today_and_within_seven_days(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    noon = datetime.now(KST).replace(hour=12, minute=0, second=0, microsecond=0)

    async def event(title, starts_at, ends_at=None):
        return await create_event(
            session,
            child_id=cid,
            title=title,
            event_type=EventType.EPISODIC,
            starts_at=starts_at,
            ends_at=ends_at or starts_at + timedelta(hours=1),
            all_day=False,
            category=EventCategory.ACTIVITY,
            created_by=EventCreatedBy.AGENT,
        )

    # 자정까지 이어져서 몇 시에 돌려도 오늘 일정이면서 아직 안 끝난 일정이다
    await event("오늘 행사", noon, ends_at=noon + timedelta(hours=12))
    await event("6일 뒤", noon + timedelta(days=6))
    await event("8일 뒤", noon + timedelta(days=8))
    await event("어제", noon - timedelta(days=1))

    data = (await db_client.get(f"/api/v1/children/{cid}/home", headers=headers)).json()

    assert [t["title"] for t in data["today"]] == ["오늘 행사"]
    assert data["upcoming_count"] == 2


async def test_highlight_prefers_confirmed_and_is_null_without_memory(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    url = f"/api/v1/children/{cid}/home"
    assert (await db_client.get(url, headers=headers)).json()["highlight"] is None

    today = today_kst()
    older = await _affinity(
        session, cid, "계란 반찬", ProfileState.CONFIRMED, today - timedelta(days=3)
    )
    newer = await _affinity(session, cid, "당근", ProfileState.CANDIDATE, today)
    stale = await _affinity(
        session, cid, "오래된 것", ProfileState.CONFIRMED, today - timedelta(days=300)
    )
    for affinity, day in (
        (older, today - timedelta(days=3)),
        (newer, today),
        (stale, today - timedelta(days=300)),
    ):
        await _food(session, cid, day, affinity.id)

    highlight = (await db_client.get(url, headers=headers)).json()["highlight"]

    # 더 최근에 기록된 후보보다 확인된 기억이 먼저고, 6개월 낡은 기억은 고르지 않는다
    assert highlight["text"] == "계란 반찬"
    assert highlight["ref"] == {"kind": "profile_affinity", "id": str(older.id)}
    assert highlight["state_reason"] == "아직 한 번 기록됐어요. 마지막은 3일 전이에요"
