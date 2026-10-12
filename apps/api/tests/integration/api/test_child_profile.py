"""아이 프로필 보기 · 고치기 — `GET` · `PATCH /children/{cid}` (C 묶음).

화면(`apps/web/src/lib/api/types.ts` 의 ChildProfile · UpdateChildRequest)과
목 서버(`apps/web/src/mocks/handlers/profile.ts`)가 이미 기대하는 모양을 서버가 지킨다.

여기서 지키는 것.
    ① 관계(relation) · 역할(role)은 **보는 보호자 기준**이다
       — 같은 아이라도 엄마와 아빠가 다르게 받는다
    ② 성별을 정하지 않은 아이는 undisclosed (루트 CLAUDE.md §2)
    ③ 남의 아이는 403 — 아이가 있는지 없는지 드러내지 않는다 (deps/child.py)

등장하는 사람은 전부 테스트용 가짜다. 보호자 1 = 아이를 등록한 보호자(owner),
보호자 2 = 초대로 연결된 보호자(member), 보호자 3 = 이 아이와 상관없는 보호자.
"""

from datetime import date

import pytest
from sqlalchemy import text

from app.api.v1.routers import children as children_router
from app.domains.child.models import ParentChildRelation

from .conftest import issue_bearer, link_child

TODAY = date(2026, 10, 5)
"""나이 문구가 날짜에 따라 바뀌어서 "오늘" 을 고정한다. 픽스처 아이 생일은 2023-01-01."""


def profile_url(child_id) -> str:
    return f"/api/v1/children/{child_id}"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(children_router, "today_kst", lambda: TODAY)


# ── GET /children/{cid} ────────────────────────────────────────────


async def test_보호자1은_자기_아이_프로필을_owner_로_받는다(db_client, bearer, cid):
    headers, _ = bearer

    response = await db_client.get(profile_url(cid), headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(cid),
        "nickname": "테스트아이",
        "birth_date": "2023-01-01",
        "age_display": "만 3세",
        "gender": "undisclosed",
        "relation": "other",
        "role": "owner",
    }


async def test_보호자2는_같은_아이를_member_와_자기_관계로_받는다(db_client, session, cid):
    headers, parent_2 = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=parent_2, child_id=cid, relation=ParentChildRelation.FATHER)

    response = await db_client.get(profile_url(cid), headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert (body["relation"], body["role"]) == ("father", "member")


async def test_보호자3이_남의_아이를_보면_403(db_client, session, cid):
    headers, _ = await issue_bearer(session, token="test-token-stranger")

    response = await db_client.get(profile_url(cid), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"


# ── PATCH /children/{cid} ──────────────────────────────────────────


async def test_성별만_보내면_성별만_바뀐다(db_client, session, bearer, cid):
    headers, _ = bearer

    response = await db_client.patch(profile_url(cid), json={"gender": "female"}, headers=headers)

    assert response.status_code == 200
    child = response.json()["child"]
    assert child["gender"] == "female"
    # 안 보낸 칸은 그대로다
    assert (child["nickname"], child["birth_date"]) == ("테스트아이", "2023-01-01")
    # 요청과 테스트가 같은 세션이라 commit 안 된 값도 보인다 — 되감은 뒤 DB 에 남은 것만 본다
    await session.rollback()
    saved = await session.scalar(text("SELECT gender FROM child WHERE id = :id"), {"id": cid})
    assert saved == "female"


async def test_생일을_고치면_나이_문구도_다시_계산한다(db_client, bearer, cid):
    headers, _ = bearer

    response = await db_client.patch(
        profile_url(cid), json={"nickname": "콩이", "birth_date": "2025-11-01"}, headers=headers
    )

    assert response.status_code == 200
    child = response.json()["child"]
    assert (child["nickname"], child["birth_date"], child["age_display"]) == (
        "콩이",
        "2025-11-01",
        "11개월",
    )


async def test_보호자2도_고칠_수_있다(db_client, session, cid):
    # 지금은 연결된 보호자 누구나 고친다 (domains/child/repository.py update_child).
    # 역할로 막기로 정하면 이 테스트를 바꾼다
    headers, parent_2 = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=parent_2, child_id=cid, relation=ParentChildRelation.FATHER)

    response = await db_client.patch(profile_url(cid), json={"gender": "male"}, headers=headers)

    assert response.status_code == 200
    assert (response.json()["child"]["role"], response.json()["child"]["gender"]) == (
        "member",
        "male",
    )


@pytest.mark.parametrize("birth_date", ["2026-10-05", "2006-01-01"])
async def test_생일_범위의_양_끝은_받는다(db_client, bearer, cid, birth_date):
    # 오늘(한국 날짜)과 올해 - 20년의 1월 1일 — 범위 안이다
    headers, _ = bearer

    response = await db_client.patch(
        profile_url(cid), json={"birth_date": birth_date}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["child"]["birth_date"] == birth_date


@pytest.mark.parametrize("birth_date", ["2026-10-06", "2005-12-31"])
async def test_생일이_화면_달력_범위_밖이면_400(db_client, bearer, cid, birth_date):
    # 아이 등록과 같은 범위 — 올해 - 20년의 1월 1일 ~ 오늘 (한국 날짜)
    headers, _ = bearer

    response = await db_client.patch(
        profile_url(cid), json={"birth_date": birth_date}, headers=headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert response.json()["error"]["detail"]["fields"] == ["birth_date"]


@pytest.mark.parametrize(
    "body",
    [
        {},  # 고칠 것이 없다
        {"gender": "unknown"},  # 세 값 밖
        {"nickname": None},  # 비우기는 없다 — 별명 · 생일 · 성별은 늘 값이 있다
        {"birth_date": None},
        {"gender": None},
        {"nickname": "   "},  # 공백만
    ],
)
async def test_고칠_내용이_틀리면_400_이고_아무것도_안_바뀐다(db_client, bearer, cid, body):
    headers, _ = bearer

    response = await db_client.patch(profile_url(cid), json=body, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    again = (await db_client.get(profile_url(cid), headers=headers)).json()
    assert (again["nickname"], again["gender"]) == ("테스트아이", "undisclosed")


async def test_보호자3이_남의_아이를_고치면_403(db_client, session, cid):
    headers, _ = await issue_bearer(session, token="test-token-stranger")

    response = await db_client.patch(profile_url(cid), json={"gender": "male"}, headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"
