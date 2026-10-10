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
