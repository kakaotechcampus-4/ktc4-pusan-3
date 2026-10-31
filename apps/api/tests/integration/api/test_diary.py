"""내 일기 쓰기 · 지우기 API — #318.

읽기(하루 보기의 `my_diary`)는 `test_calendar.py` 가 건다. 여기는 쓰기 · 지우기가 더하는 것만 본다:
덮어쓰기, 작성자는 로그인한 보호자, 빈 내용 · 길이 초과 · NUL 거부, 아이 접근 확인,
연결 끊기와 겹칠 때.
"""

import asyncio
import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.schemas.diary import DIARY_CONTENT_MAX
from app.domains.child.models import Child
from app.domains.child.parents import unlink_parent
from app.domains.identity.models import Parent
from app.domains.schedule.models import DiaryEntry
from app.domains.schedule.repository import find_diary, put_diary
from app.infra.db.session import engine
from tests.integration.api.conftest import Bearer, issue_bearer, link_child

DAY = date(2026, 3, 10)


async def _my_diary(client: AsyncClient, cid: uuid.UUID, headers: dict) -> dict | None:
    return (await client.get(f"/api/v1/children/{cid}/calendar/{DAY}", headers=headers)).json()[
        "my_diary"
    ]


async def test_writing_again_overwrites(db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID):
    headers, _ = bearer
    url = f"/api/v1/children/{cid}/diary/{DAY}"

    await db_client.put(url, json={"content": "처음"}, headers=headers)
    resp = await db_client.put(url, json={"content": "  고친 일기\n"}, headers=headers)

    assert resp.status_code == 200
    # 앞뒤 공백 · 줄바꿈은 그대로 저장한다 — 공백 검사는 거부할 때만 쓴다
    assert resp.json()["content"] == "  고친 일기\n"
    assert await _my_diary(db_client, cid, headers) == resp.json()


async def test_delete_removes_only_my_diary(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """작성자는 요청이 아니라 로그인한 보호자다 — 같은 날 다른 보호자의 일기는 남는다."""
    headers, me = bearer
    _, partner = await issue_bearer(session, token="partner")
    await link_child(session, parent_id=partner, child_id=cid)
    await put_diary(session, child_id=cid, parent_id=me, day=DAY, content="내 일기")
    await put_diary(session, child_id=cid, parent_id=partner, day=DAY, content="남 일기")
    url = f"/api/v1/children/{cid}/diary/{DAY}"

    first = await db_client.delete(url, headers=headers)
    again = await db_client.delete(url, headers=headers)  # 없어도 204

    assert (first.status_code, again.status_code) == (204, 204)
    assert await _my_diary(db_client, cid, headers) is None
    kept = await find_diary(session, child_id=cid, parent_id=partner, day=DAY)
    assert kept is not None and kept.content == "남 일기"


@pytest.mark.parametrize("content", ["", " \n\t"])
async def test_blank_content_is_rejected_without_deleting(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, content: str
):
    headers, me = bearer
    await put_diary(session, child_id=cid, parent_id=me, day=DAY, content="내 일기")

    resp = await db_client.put(
        f"/api/v1/children/{cid}/diary/{DAY}", json={"content": content}, headers=headers
    )

    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "validation_failed")
    assert (await _my_diary(db_client, cid, headers))["content"] == "내 일기"


async def test_content_up_to_the_limit_is_saved(
    db_client: AsyncClient, bearer: Bearer, cid: uuid.UUID
):
    headers, _ = bearer
    content = "가" * DIARY_CONTENT_MAX

    resp = await db_client.put(
        f"/api/v1/children/{cid}/diary/{DAY}", json={"content": content}, headers=headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["content"] == content


# NUL 은 Postgres text 칸이 받지 못해 막지 않으면 500 이 난다
@pytest.mark.parametrize(
    "content", ["가" * (DIARY_CONTENT_MAX + 1), "a\u0000b"], ids=["too_long", "nul"]
)
async def test_too_long_or_nul_content_is_rejected_without_overwriting(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, content: str
):
    headers, me = bearer
    await put_diary(session, child_id=cid, parent_id=me, day=DAY, content="내 일기")

    resp = await db_client.put(
        f"/api/v1/children/{cid}/diary/{DAY}", json={"content": content}, headers=headers
    )

    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "validation_failed")
    assert (await _my_diary(db_client, cid, headers))["content"] == "내 일기"


async def test_other_childs_diary_is_forbidden(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer
):
    headers, _ = bearer
    _, stranger = await issue_bearer(session, token="stranger")
    other_cid = await link_child(session, parent_id=stranger)
    url = f"/api/v1/children/{other_cid}/diary/{DAY}"

    put = await db_client.put(url, json={"content": "일기"}, headers=headers)
    delete = await db_client.delete(url, headers=headers)

    assert (put.status_code, delete.status_code) == (403, 403)


# 연결 끊기와 겹치는 두 순서 (#427). 두 요청이 실제로 겹쳐야 해서 한 트랜잭션 안의 `session`
# 으로는 재현할 수 없다. 커밋하는 세션을 따로 열고, 끝나면 만든 보호자 · 아이를 지운다
# (연결 · 일기는 CASCADE).


@pytest.fixture
async def committed_member():
    """(세션 공장, 아이 id, 함께 보는 보호자 id) — 실제로 커밋된 아이 하나와 보호자 둘."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        owner, member = Parent(), Parent()
        setup.add_all([owner, member])
        await setup.flush()
        child_id = await link_child(setup, parent_id=owner.id)
        await link_child(setup, parent_id=member.id, child_id=child_id)
        await setup.commit()
    yield factory, child_id, member.id
    async with factory() as cleanup:
        await cleanup.execute(delete(Child).where(Child.id == child_id))
        await cleanup.execute(delete(Parent).where(Parent.id.in_([owner.id, member.id])))
        await cleanup.commit()


async def _diaries_left(factory, child_id: uuid.UUID, parent_id: uuid.UUID) -> int:
    async with factory() as check:
        return await check.scalar(
            select(func.count())
            .select_from(DiaryEntry)
            .where(DiaryEntry.child_id == child_id, DiaryEntry.author_parent_id == parent_id)
        )


async def test_unlink_in_flight_leaves_no_diary_of_the_unlinked_parent(committed_member):
    """끊기가 연결과 일기를 지우고 커밋하기 전에 일기 저장이 들어오면, 저장은 None 이다."""
    factory, child_id, member = committed_member

    async def write_diary():
        async with factory() as writer:
            row = await put_diary(
                writer, child_id=child_id, parent_id=member, day=DAY, content="일기"
            )
            await writer.commit()
            return row

    async with factory() as unlinker:
        assert await unlink_parent(unlinker, child_id=child_id, parent_id=member)
        writing = asyncio.create_task(write_diary())
        await asyncio.sleep(0.3)  # 저장이 연결 행 잠금에서 기다리게 둔다
        await unlinker.commit()

    assert await asyncio.wait_for(writing, timeout=5) is None
    assert await _diaries_left(factory, child_id, member) == 0


async def test_unlink_waits_for_a_diary_being_written_then_deletes_it(committed_member):
    """일기 저장이 먼저 연결을 잠갔으면, 끊기는 그 커밋을 기다렸다가 방금 쓴 일기까지 지운다."""
    factory, child_id, member = committed_member

    async def unlink():
        async with factory() as unlinker:
            unlinked = await unlink_parent(unlinker, child_id=child_id, parent_id=member)
            await unlinker.commit()
            return unlinked

    async with factory() as writer:
        assert await put_diary(writer, child_id=child_id, parent_id=member, day=DAY, content="일기")
        unlinking = asyncio.create_task(unlink())
        await asyncio.sleep(0.3)  # 끊기가 연결 행 잠금에서 기다리게 둔다
        await writer.commit()

    assert await asyncio.wait_for(unlinking, timeout=5) is True
    assert await _diaries_left(factory, child_id, member) == 0


@pytest.mark.parametrize("target", ["child", "member"])
async def test_withdraw_in_flight_does_not_deadlock_a_diary(committed_member, target):
    """탈퇴가 아이(등록 보호자 탈퇴) · 보호자(본인 탈퇴)를 먼저 잠갔을 때 일기 저장이 들어와도
    교착 없이 끝나고, 일기는 남지 않는다.

    `withdraw_parent` 는 중간에 멈출 수 없어서, 그 순서(행을 FOR UPDATE → 지움 → CASCADE)를
    그대로 따른다.
    """
    factory, child_id, member = committed_member
    model, row_id = (Child, child_id) if target == "child" else (Parent, member)

    async def write_diary():
        async with factory() as writer:
            row = await put_diary(
                writer, child_id=child_id, parent_id=member, day=DAY, content="일기"
            )
            await writer.commit()
            return row

    async with factory() as withdrawing:
        await withdrawing.scalar(select(model.id).where(model.id == row_id).with_for_update())
        writing = asyncio.create_task(write_diary())
        await asyncio.sleep(0.3)  # 저장이 탈퇴가 잠근 행에서 기다리게 둔다
        await withdrawing.execute(delete(model).where(model.id == row_id))
        await withdrawing.commit()

    assert await asyncio.wait_for(writing, timeout=5) is None
    assert await _diaries_left(factory, child_id, member) == 0
