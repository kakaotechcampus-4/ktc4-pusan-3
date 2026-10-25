"""함께 보는 보호자 목록 · 연결 끊기 — #336.

누가 누구를 끊을 수 있는지(재형님 결정 10-06)와, 끊을 때 지우는 것(개인 기록)과
남기는 것(공동 기록과 그 "적은 사람")을 본다.
"""

import uuid
from datetime import UTC, date, datetime

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.domains.chat.models import ChatTurn
from app.domains.child.models import ParentChild, ParentChildRelation
from app.domains.identity.models import Parent
from app.domains.schedule.models import (
    DiaryEntry,
    EventCategory,
    EventCreatedBy,
    EventType,
    Reminder,
    SharedPhoto,
)
from app.domains.schedule.repository import create_event, create_reminder, put_diary
from tests.integration.api.conftest import (
    Bearer,
    in_memory,
    issue_bearer,
    link_child,
    stash_in_memory,
)

DAY = date(2026, 3, 10)
AT = datetime(2026, 3, 10, 1, tzinfo=UTC)


async def _member(
    session: AsyncSession, cid: uuid.UUID, token: str, relation=ParentChildRelation.OTHER
) -> Bearer:
    headers, pid = await issue_bearer(session, token=token)
    await link_child(session, parent_id=pid, child_id=cid, relation=relation)
    return headers, pid


async def _personal_records(session: AsyncSession, cid: uuid.UUID, pid: uuid.UUID) -> None:
    """그 보호자가 이 아이에 남기는 개인 기록 — 일기 1 · 알림 1 · 대화 1."""
    event = await create_event(
        session,
        child_id=cid,
        title="소풍",
        event_type=EventType.EPISODIC,
        starts_at=AT,
        ends_at=None,
        all_day=False,
        category=EventCategory.ACTIVITY,
        created_by=EventCreatedBy.CAREGIVER,
    )
    await put_diary(session, child_id=cid, parent_id=pid, day=DAY, content="일기")
    await create_reminder(session, child_id=cid, event_id=event.id, parent_id=pid, remind_at=AT)
    session.add(
        ChatTurn(
            parent_id=pid,
            child_id=cid,
            run_id=uuid.uuid4().hex,
            conversation_date=DAY,
            raw_text="synthetic line",
            replies=[],
            created_at=AT,
        )
    )
    await session.flush()


async def test_list_shows_role_in_connected_order(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    headers, owner = bearer
    await session.execute(update(Parent).where(Parent.id == owner).values(nickname="엄마"))
    _, member = await _member(session, cid, "member", ParentChildRelation.GRANDPARENT)
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)

    resp = await db_client.get(f"/api/v1/children/{cid}/parents", headers=headers)
    other = await db_client.get(f"/api/v1/children/{other_cid}/parents", headers=headers)

    assert resp.status_code == 200
    got = [
        (p["parent_id"], p["nickname"], p["relation"], p["role"]) for p in resp.json()["parents"]
    ]
    assert got == [
        (str(owner), "엄마", "other", "owner"),
        (str(member), None, "grandparent", "member"),
    ]
    assert other.status_code == 403


async def test_who_can_unlink_whom(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """member 가 남을 끊으면 403, owner 는 누구도 못 끊어 409, 연결 안 된 대상은 누가 보내도 404."""
    owner_headers, owner = bearer
    member_headers, _ = await _member(session, cid, "member")
    _, other_member = await _member(session, cid, "other-member")
    _, stranger = await issue_bearer(session, token="stranger")

    def url(pid: uuid.UUID) -> str:
        return f"/api/v1/children/{cid}/parents/{pid}"

    member_on_member = await db_client.delete(url(other_member), headers=member_headers)
    member_on_owner = await db_client.delete(url(owner), headers=member_headers)
    owner_on_self = await db_client.delete(url(owner), headers=owner_headers)
    owner_on_stranger = await db_client.delete(url(stranger), headers=owner_headers)
    member_on_stranger = await db_client.delete(url(stranger), headers=member_headers)

    codes = [
        (r.status_code, r.json()["error"]["code"])
        for r in (
            member_on_member,
            member_on_owner,
            owner_on_self,
            owner_on_stranger,
            member_on_stranger,
        )
    ]
    assert codes == [
        (403, "owner_only"),
        (409, "owner_required"),
        (409, "owner_required"),
        (404, "not_found"),
        (404, "not_found"),
    ]
    listed = await db_client.get(f"/api/v1/children/{cid}/parents", headers=owner_headers)
    assert len(listed.json()["parents"]) == 3


async def test_unlink_deletes_only_their_personal_records(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """끊긴 보호자의 일기 · 알림 · 대화만 지운다.

    다른 보호자의 것과 공동 기록의 "적은 사람"은 남는다."""
    owner_headers, owner = bearer
    _, member = await _member(session, cid, "member")
    await _personal_records(session, cid, owner)
    await _personal_records(session, cid, member)
    photo = SharedPhoto(
        child_id=cid, uploader_parent_id=member, date=DAY, image_url="https://example.test/p.jpg"
    )
    session.add(photo)
    await session.flush()

    resp = await db_client.delete(f"/api/v1/children/{cid}/parents/{member}", headers=owner_headers)
    # 테스트 세션은 savepoint 위에서 돈다. 라우터가 commit 하지 않았으면 여기서 삭제가 되돌아간다.
    await session.rollback()

    assert resp.status_code == 204
    links = await session.scalars(select(ParentChild.parent_id).where(ParentChild.child_id == cid))
    assert list(links) == [owner]
    diaries = await session.scalars(select(DiaryEntry.author_parent_id))
    reminders = await session.scalars(select(Reminder.parent_id))
    assert list(diaries) == [owner]
    assert list(reminders) == [owner]
    chats = await session.scalars(select(ChatTurn.parent_id))
    assert list(chats) == [owner]
    await session.refresh(photo)
    assert photo.uploader_parent_id == member


async def test_unlink_forgets_only_their_in_memory_entries(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    clean_in_memory_stores,
):
    """끊긴 보호자의 메모리 보관함 항목만 바로 지운다.

    같은 아이의 다른 보호자 것은 남는다 (#561)."""
    owner_headers, owner = bearer
    _, member = await _member(session, cid, "member")
    theirs = stash_in_memory(parent_id=member, child_id=cid)
    owners = stash_in_memory(parent_id=owner, child_id=cid)

    resp = await db_client.delete(f"/api/v1/children/{cid}/parents/{member}", headers=owner_headers)

    assert resp.status_code == 204
    assert in_memory(theirs, parent_id=member, child_id=cid) == (False, False)
    assert in_memory(owners, parent_id=owner, child_id=cid) == (True, True)


async def test_member_leaves_and_loses_access_even_on_replay(
    db_client: AsyncClient, session: AsyncSession, cid: uuid.UUID
):
    """스스로 나가면 그 아이 API 는 403 — 나가기 전에 성공한 요청을 같은 키로 다시 보내도
    저장된 응답을 재생하지 않는다 (docs/api/idempotency-v1.md §3-1)."""
    idempotency.clear()
    headers, member = await _member(session, cid, "member")
    submit = {
        "url": f"/api/v1/children/{cid}/events",
        "json": {
            "event": {
                "title": "소풍",
                "starts_at": "2026-03-10T10:00:00+09:00",
                "event_type": "episodic",
                "category": "activity",
            },
            "items": [],
        },
        "headers": {**headers, "Idempotency-Key": "before-leaving"},
    }

    before = await db_client.post(**submit)
    left = await db_client.delete(f"/api/v1/children/{cid}/parents/{member}", headers=headers)
    replay = await db_client.post(**submit)

    assert (before.status_code, left.status_code, replay.status_code) == (201, 204, 403)
    assert replay.json()["error"]["code"] == "child_access_denied"
