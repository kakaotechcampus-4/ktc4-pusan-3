"""홈 API — #259.

기록 수 집계는 `test_observation_repository.py`, 기억 문구는 `test_affinity_reason`,
예상 질문은 `test_home_prompts` 가 건다. 여기는 API 가 더하는 것만 본다: 아이 접근 확인,
건강 기록도 세기, 이번 주 경계, 오늘 급식 · 오늘 · 다가오는 일정, 하이라이트로 고르는 기억.

급식 메뉴는 전부 지어낸 값이다 (루트 CLAUDE.md §9).
"""

import logging
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import KST, today_kst
from app.api.v1.routers import home as home_router
from app.domains.daycare_meal.repository import NewDaycareMeal, insert_daycare_meals
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
        polarity=1,  # confirmed 는 NULL 을 받지 않는다 (CHECK profile_affinity_confirmed_polarity)
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


async def test_counts_include_health_and_split_at_monday(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, parent_id = bearer
    today = today_kst()
    last_sunday = today - timedelta(days=today.weekday() + 1)
    await _food(session, cid, today)
    await _food(session, cid, last_sunday)  # 지난주 — 전체에는 들어가고 이번 주에는 아니다
    await _food(session, cid, today, status=ObservationStatus.STAND_ALONE)  # 고친 기록도 센다
    await _food(session, cid, today, status=ObservationStatus.DELETED)  # 지운 기록은 안 센다
    await create_observation(  # 건강 기록도 기록 탭처럼 센다 (#764)
        session,
        domain="health",
        child_id=cid,
        source_writer=parent_id,
        raw_text="원문",
        observed_range=Range(today, today + timedelta(days=1), bounds="[)"),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, "symptom": ["기침"]},
    )

    data = (await db_client.get(f"/api/v1/children/{cid}/home", headers=headers)).json()

    assert (data["observation_count"], data["week_count"]) == (4, 3)


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
    # 오늘 00:00 에 끝났다 — 오늘 일정이 아니다 (#318)
    await event("어제 밤", noon - timedelta(hours=14), ends_at=noon - timedelta(hours=12))

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


async def test_highlight_skips_a_candidate_observed_on_a_single_day(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    # 하루치 관찰로 생긴 후보는 "요즘 ~" 으로 띄우지 않는다 — 추천 근거(티어 2)와 같은 기준 (#626)
    headers, _ = bearer
    url = f"/api/v1/children/{cid}/home"
    today = today_kst()
    once = await _affinity(session, cid, "당근", ProfileState.CANDIDATE, today)
    await _food(session, cid, today, once.id)
    assert (await db_client.get(url, headers=headers)).json()["highlight"] is None

    # 같은 날 한 건 더 — 날로 세서 아직 하루다 (#633 리뷰)
    await _food(session, cid, today, once.id)
    assert (await db_client.get(url, headers=headers)).json()["highlight"] is None

    await _food(session, cid, today - timedelta(days=1), once.id)
    highlight = (await db_client.get(url, headers=headers)).json()["highlight"]
    assert highlight["ref"] == {"kind": "profile_affinity", "id": str(once.id)}
    assert highlight["state_reason"].startswith("지금까지 3번")  # 카드 숫자는 기록 건수 그대로


# 급식 테스트의 "지금". 실제 날짜와 먼 고정 시각이라 라우터가 이 값을 보지 않으면 테스트가 깨진다 —
# 한국 자정 직전에 돌려도 테스트가 고른 "오늘" 과 라우터의 "오늘" 이 갈리지 않는다.
_NOON = datetime(2026, 3, 2, 12, 0, tzinfo=KST)


@pytest.fixture
def pinned_now(monkeypatch):
    """홈 라우터가 오늘을 정하는 `datetime.now(KST)` 를 `_NOON` 으로 고정한다.

    라우터 모듈이 import 한 `datetime` 이름만 바꾼다 — 다른 모듈의 시각은 그대로다.
    """

    class _Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return _NOON.astimezone(tz) if tz else _NOON.replace(tzinfo=None)

    monkeypatch.setattr(home_router, "datetime", _Pinned)


async def _meals(session, child_id, *meals: NewDaycareMeal):
    await insert_daycare_meals(session, child_id=child_id, meals=meals)


async def test_today_lists_todays_meals_by_slot_before_events(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, pinned_now
):
    headers, _ = bearer
    today = _NOON.date()
    # 넣는 순서를 섞는다 — 화면 순서는 하루 순서(오전 간식 → 점심 → 오후 간식)다
    await _meals(
        session,
        cid,
        NewDaycareMeal(today, "snack_pm", ("감자", "보리차")),
        NewDaycareMeal(today, "lunch", ("잡곡밥", "소고기미역국", "두부조림")),
        NewDaycareMeal(today, "snack_am", ("우유",)),
        NewDaycareMeal(today - timedelta(days=1), "lunch", ("어제메뉴",)),
        NewDaycareMeal(today + timedelta(days=1), "lunch", ("내일메뉴",)),
    )
    noon = _NOON
    event = await create_event(
        session,
        child_id=cid,
        title="오늘 행사",
        event_type=EventType.EPISODIC,
        starts_at=noon,
        ends_at=noon + timedelta(hours=12),
        all_day=False,
        category=EventCategory.ACTIVITY,
        created_by=EventCreatedBy.AGENT,
    )

    data = (await db_client.get(f"/api/v1/children/{cid}/home", headers=headers)).json()

    assert data["today"] == [
        {"kind": "meal", "title": "오전 간식 · 우유", "origin": "기관 급식표"},
        {
            "kind": "meal",
            "title": "점심 · 잡곡밥, 소고기미역국, 두부조림",
            "origin": "기관 급식표",
        },
        {"kind": "meal", "title": "오후 간식 · 감자, 보리차", "origin": "기관 급식표"},
        {"kind": "event", "event_id": str(event.id), "title": "오늘 행사"},
    ]


async def test_today_has_no_meal_line_without_todays_meal(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, pinned_now
):
    # 급식이 없는 날은 예전과 같은 응답이다 — 빈 급식 줄을 세우지 않는다
    headers, _ = bearer
    today = _NOON.date()
    await _meals(session, cid, NewDaycareMeal(today + timedelta(days=1), "lunch", ("내일메뉴",)))

    data = (await db_client.get(f"/api/v1/children/{cid}/home", headers=headers)).json()

    assert data["today"] == []


async def test_other_childs_meals_stay_behind_the_access_check(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, pinned_now
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    await _meals(session, other_cid, NewDaycareMeal(_NOON.date(), "lunch", ("남의메뉴",)))

    denied = await db_client.get(f"/api/v1/children/{other_cid}/home", headers=headers)
    mine = (await db_client.get(f"/api/v1/children/{cid}/home", headers=headers)).json()

    assert denied.status_code == 403
    assert "남의메뉴" not in denied.text
    assert mine["today"] == []  # 내 아이 홈에도 남의 아이 급식이 섞이지 않는다


async def test_meal_menu_text_is_not_logged(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    caplog,
    pinned_now,
):
    headers, _ = bearer
    await _meals(session, cid, NewDaycareMeal(_NOON.date(), "lunch", ("로그금지메뉴",)))

    with caplog.at_level(logging.DEBUG):
        resp = await db_client.get(f"/api/v1/children/{cid}/home", headers=headers)

    assert "로그금지메뉴" in resp.text
    assert "로그금지메뉴" not in caplog.text
