"""준비물 체크 API — #318.

체크 시각 규칙(미체크 → 체크만 새 시각, 다시 체크는 처음 시각 유지, 해제는 null)과
아이 접근 확인을 본다.
테스트는 한 트랜잭션 안이라 DB 의 now() 가 늘 같다 — 그래서 처음 체크 시각을 과거로 넣어 두고
그 값이 남는지 · 바뀌는지로 가른다.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.quota import KST
from app.domains.schedule.models import EventCategory, EventCreatedBy, EventItem, EventType
from app.domains.schedule.repository import create_event, create_event_item
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

DAY = date(2026, 3, 10)
EARLIER = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


async def _item(session: AsyncSession, child_id: uuid.UUID) -> EventItem:
    start = datetime(DAY.year, DAY.month, DAY.day, 10, tzinfo=KST)
    event = await create_event(
        session,
        child_id=child_id,
        title="소풍",
        event_type=EventType.EPISODIC,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        all_day=False,
        category=EventCategory.ACTIVITY,
        created_by=EventCreatedBy.CAREGIVER,
    )
    return await create_event_item(session, child_id=child_id, event_id=event.id, item_name="수건")


async def test_prepared_at_follows_the_state_change(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    item = await _item(session, cid)
    await session.execute(
        update(EventItem)
        .where(EventItem.item_id == item.item_id)
        .values(is_prepared=True, prepared_at=EARLIER)
    )
    url = f"/api/v1/children/{cid}/event-items/{item.item_id}"

    async def patch(is_prepared: bool) -> dict:
        resp = await db_client.patch(url, json={"is_prepared": is_prepared}, headers=headers)
        assert resp.status_code == 200
        return resp.json()["item"]

    again = await patch(True)  # 체크 → 체크: 처음 시각 유지
    cleared = await patch(False)  # 체크 → 미체크: null
    still_clear = await patch(False)  # 미체크 → 미체크: null
    rechecked = await patch(True)  # 미체크 → 체크: 새 시각

    assert datetime.fromisoformat(again["prepared_at"]) == EARLIER
    assert (cleared["is_prepared"], cleared["prepared_at"]) == (False, None)
    assert (still_clear["is_prepared"], still_clear["prepared_at"]) == (False, None)
    assert rechecked["is_prepared"] is True
    assert rechecked["prepared_at"] is not None
    assert datetime.fromisoformat(rechecked["prepared_at"]) != EARLIER

    day = (await db_client.get(f"/api/v1/children/{cid}/calendar/{DAY}", headers=headers)).json()
    [shown] = day["events"][0]["items"]
    assert shown == rechecked


async def test_other_childs_item_is_not_touched(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """남의 아이 경로는 403, 남의 아이 준비물을 내 아이 경로로 보내면 404 — 둘 다 바꾸지 않는다."""
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    theirs = await _item(session, other_cid)
    body = {"is_prepared": True}

    via_other = await db_client.patch(
        f"/api/v1/children/{other_cid}/event-items/{theirs.item_id}", json=body, headers=headers
    )
    via_mine = await db_client.patch(
        f"/api/v1/children/{cid}/event-items/{theirs.item_id}", json=body, headers=headers
    )

    assert (via_other.status_code, via_mine.status_code) == (403, 404)
    await session.refresh(theirs)
    assert (theirs.is_prepared, theirs.prepared_at) == (False, None)
