"""캘린더 달 · 하루 보기 API — #318.

관찰 응답 모양은 `test_observations.py` 가 건다. 여기는 캘린더가 더하는 것만 본다:
아이 접근 확인, 내 일기만, 달 숫자와 하루 목록이 같은 기준, 00:00 에 끝난 일정.
"""

import uuid
from datetime import date, datetime, time, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import KST
from app.domains.chat.repository import add_turn
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
)
from app.domains.memory.observation.repository import create_observation
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventType
from app.domains.schedule.repository import (
    add_shared_photo,
    create_event,
    create_event_item,
    put_diary,
)
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

MONTH = "2026-03"
DAY = date(2026, 3, 10)


async def _food(session, child_id, start, days=1, status=ObservationStatus.ACTIVE):
    session.add(
        ObservationFood(
            child_id=child_id,
            raw_text="원문",
            subject="계란말이",
            confidence_source=ConfidenceSource.PARENT_DIRECT,
            observed_range=Range(start, start + timedelta(days=days), bounds="[)"),
            status=status,
        )
    )
    await session.flush()


async def _event(session, child_id, title, starts_at, ends_at):
    return await create_event(
        session,
        child_id=child_id,
        title=title,
        event_type=EventType.EPISODIC,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=False,
        category=EventCategory.ACTIVITY,
        created_by=EventCreatedBy.CAREGIVER,
    )


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=KST) + timedelta(hours=hour)


async def test_other_childs_calendar_is_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)

    month = await db_client.get(
        f"/api/v1/children/{other_cid}/calendar", params={"month": MONTH}, headers=headers
    )
    day = await db_client.get(f"/api/v1/children/{other_cid}/calendar/{DAY}", headers=headers)

    assert (month.status_code, day.status_code) == (403, 403)


async def test_month_counts_match_day_lists(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """달 보기의 기록 수 = 하루 보기의 기록 목록. 여러 날 기록은 걸친 날마다 나온다."""
    headers, parent_id = bearer
    await _food(session, cid, DAY)
    await _food(session, cid, DAY, days=2)  # 10일 · 11일
    await _food(session, cid, DAY, status=ObservationStatus.INACTIVE)  # 고친 기록도 나온다
    await _food(session, cid, DAY, status=ObservationStatus.DELETED)  # 지운 기록은 안 나온다
    await create_observation(
        session,
        domain="health",
        child_id=cid,
        source_writer=parent_id,
        raw_text="원문",
        observed_range=Range(DAY, DAY + timedelta(days=1), bounds="[)"),
        fields={"confidence_source": ConfidenceSource.PARENT_DIRECT, "symptom": ["기침"]},
    )

    month = (
        await db_client.get(
            f"/api/v1/children/{cid}/calendar", params={"month": MONTH}, headers=headers
        )
    ).json()
    counts = {d["date"]: d["observation_count"] for d in month["days"]}
    assert counts == {"2026-03-10": 3, "2026-03-11": 1}
    for day, count in counts.items():
        detail = await db_client.get(f"/api/v1/children/{cid}/calendar/{day}", headers=headers)
        assert len(detail.json()["observations"]) == count


async def test_event_ending_at_midnight_stays_on_its_day(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    prev = DAY - timedelta(days=1)
    late = await _event(session, cid, "전날 밤", _at(prev, 22), _at(DAY, 0))
    await create_event_item(session, child_id=cid, event_id=late.id, item_name="수건")
    await _event(session, cid, "당일", _at(DAY, 10), _at(DAY, 11))

    month = (
        await db_client.get(
            f"/api/v1/children/{cid}/calendar", params={"month": MONTH}, headers=headers
        )
    ).json()
    on_prev = await db_client.get(f"/api/v1/children/{cid}/calendar/{prev}", headers=headers)
    on_day = await db_client.get(f"/api/v1/children/{cid}/calendar/{DAY}", headers=headers)

    assert [d["date"] for d in month["days"] if d["has_event"]] == [str(prev), str(DAY)]
    [prev_event] = on_prev.json()["events"]
    assert (prev_event["title"], [i["item_name"] for i in prev_event["items"]]) == (
        "전날 밤",
        ["수건"],
    )
    assert [e["title"] for e in on_day.json()["events"]] == ["당일"]


async def test_only_my_diary_but_shared_photos(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """일기 · 대화는 쓴 보호자 본인 것만, 사진은 연결된 보호자가 함께 본다."""
    headers, me = bearer
    _, partner = await issue_bearer(session, token="partner")
    await link_child(session, parent_id=partner, child_id=cid)
    other_day = DAY + timedelta(days=1)
    await put_diary(session, child_id=cid, parent_id=me, day=DAY, content="내 일기")
    await put_diary(session, child_id=cid, parent_id=partner, day=other_day, content="남 일기")
    # 대화도 일기처럼 내 것만 센다 (#228) — 같은 날 남의 대화는 세지 않는다
    for parent_id in (me, partner):
        await add_turn(
            session,
            parent_id=parent_id,
            child_id=cid,
            run_id=uuid.uuid4().hex,
            conversation_date=DAY,
            raw_text="synthetic line",
            reply_to=None,
            replies=[],
            created_at=datetime.combine(DAY, time(9), tzinfo=KST),
        )
    await add_shared_photo(
        session, child_id=cid, parent_id=partner, day=other_day, image_url="https://x/1.jpg"
    )

    month = (
        await db_client.get(
            f"/api/v1/children/{cid}/calendar", params={"month": MONTH}, headers=headers
        )
    ).json()
    mine = (await db_client.get(f"/api/v1/children/{cid}/calendar/{DAY}", headers=headers)).json()
    theirs = (
        await db_client.get(f"/api/v1/children/{cid}/calendar/{other_day}", headers=headers)
    ).json()

    assert [(d["date"], d["has_my_diary"], d["has_shared_photo"]) for d in month["days"]] == [
        (str(DAY), True, False),
        (str(other_day), False, True),
    ]
    assert mine["my_diary"]["content"] == "내 일기"
    assert theirs["my_diary"] is None
    assert mine["chat_count"] == 1
    assert [p["image_url"] for p in theirs["shared_photos"]] == ["https://x/1.jpg"]


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("calendar?month=2026-13", 400),
        # 형식은 맞지만 날짜 계산이 넘친다 — 500 이 아니라 형식 오류와 같은 코드 (#318 리뷰)
        ("calendar?month=0001-01", 400),
        ("calendar?month=9999-12", 400),
        ("calendar/9999-12-31", 422),  # 경로 값이라 422 — `/calendar/abc` 와 같다
    ],
)
async def test_out_of_range_dates_are_rejected(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID, path: str, status: int
):
    headers, _ = bearer

    resp = await db_client.get(f"/api/v1/children/{cid}/{path}", headers=headers)

    assert (resp.status_code, resp.json()["error"]["code"]) == (status, "validation_failed")


async def test_day_lists_every_observation_past_one_page(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    for _ in range(101):  # 한 쪽은 100건이다
        await _food(session, cid, DAY)

    detail = await db_client.get(f"/api/v1/children/{cid}/calendar/{DAY}", headers=headers)

    assert len(detail.json()["observations"]) == 101
