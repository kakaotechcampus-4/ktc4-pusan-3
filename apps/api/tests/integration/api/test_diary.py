"""내 일기 쓰기 · 지우기 API — #318.

읽기(하루 보기의 `my_diary`)는 `test_calendar.py` 가 건다. 여기는 쓰기 · 지우기가 더하는 것만 본다:
덮어쓰기, 작성자는 로그인한 보호자, 빈 내용 거부, 아이 접근 확인.
"""

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.schedule.repository import find_diary, put_diary
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
