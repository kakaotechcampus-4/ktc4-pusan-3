"""아이 등록 뒤 02 화면 — `POST /children/{cid}/onboarding` (C 묶음).

화면(`apps/web/src/app/child/[childId]/onboarding/page.tsx`)은 적은 칸만 보낸다 —
`relation` · `gender`(기본값이면 안 보냄) · `height_cm` · `weight_kg`.
아무것도 안 적으면 보내지 않는다.
응답 본문은 화면이 읽지 않는다(성공하면 아이 정보를 다시 불러오고 홈으로 간다).

여기서 지키는 것.
    ① 관계 · 성별 · 첫 키·몸무게를 한 트랜잭션에 — 관계는 **보내는 보호자 자신의** 관계다
    ② 키·몸무게를 안 적었으면 성장 기록을 만들지 않는다 — 잰 것이 없는 기록이다
    ③ 잰 날은 서버가 찍는다 (한국 날짜 오늘)
    ④ Idempotency-Key 필수, 같은 키는 처음 응답을 돌려주고 기록은 한 번만
    ⑤ 알레르기 · 한 줄은 이 창구로 받지 않는다 — 알레르기의 쓰기 경로는 승인 게이트 하나다 (§2)

등장하는 사람은 전부 테스트용 가짜다. 보호자 1 = 아이를 등록한 보호자(owner),
보호자 2 = 초대로 연결된 보호자(member), 보호자 3 = 이 아이와 상관없는 보호자.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api import idempotency
from app.api.v1.routers import children as children_router
from app.domains.child.models import Child, ChildGrowthLog, ParentChild, ParentChildRelation

from .conftest import issue_bearer, link_child

TODAY = date(2026, 10, 5)


def onboarding_url(child_id) -> str:
    return f"/api/v1/children/{child_id}/onboarding"


def with_key(headers: dict, key: str = "onboarding-key-1") -> dict:
    return {**headers, "Idempotency-Key": key}


@pytest.fixture(autouse=True)
def _fixed_today_and_clean_keys(monkeypatch):
    monkeypatch.setattr(children_router, "today_kst", lambda: TODAY)
    idempotency.clear()
    yield
    idempotency.clear()


async def growth_rows(session, child_id) -> list[ChildGrowthLog]:
    rows = await session.scalars(select(ChildGrowthLog).where(ChildGrowthLog.child_id == child_id))
    return list(rows.all())


async def relation_of(session, *, child_id, parent_id) -> ParentChildRelation:
    return await session.scalar(
        select(ParentChild.relation).where(
            ParentChild.child_id == child_id, ParentChild.parent_id == parent_id
        )
    )


async def test_보호자1이_관계_성별_키_몸무게를_한_번에_저장한다(db_client, session, bearer, cid):
    headers, parent_1 = bearer
    body = {"relation": "mother", "gender": "female", "height_cm": 98.5, "weight_kg": 15.2}

    response = await db_client.post(onboarding_url(cid), json=body, headers=with_key(headers))

    assert response.status_code == 200
    data = response.json()
    assert (data["child"]["relation"], data["child"]["gender"]) == ("mother", "female")
    assert data["growth"]["measured_on"] == "2026-10-05"
    assert (data["growth"]["height_cm"], data["growth"]["weight_kg"]) == (98.5, 15.2)

    assert (
        await relation_of(session, child_id=cid, parent_id=parent_1) is ParentChildRelation.MOTHER
    )
    (row,) = await growth_rows(session, cid)
    assert (row.measured_on, row.height_cm, row.weight_kg) == (
        TODAY,
        Decimal("98.5"),
        Decimal("15.20"),
    )
    assert row.created_by == parent_1


async def test_키_몸무게를_안_적으면_성장_기록을_만들지_않는다(db_client, session, bearer, cid):
    headers, _ = bearer

    response = await db_client.post(
        onboarding_url(cid), json={"relation": "father"}, headers=with_key(headers)
    )

    assert response.status_code == 200
    assert response.json()["growth"] is None
    assert await growth_rows(session, cid) == []


async def test_몸무게만_적으면_몸무게만_있는_기록(db_client, session, bearer, cid):
    headers, _ = bearer

    response = await db_client.post(
        onboarding_url(cid), json={"weight_kg": 15.4}, headers=with_key(headers)
    )

    assert response.status_code == 200
    (row,) = await growth_rows(session, cid)
    assert (row.height_cm, row.weight_kg) == (None, Decimal("15.40"))


async def test_같은_키로_두_번_보내면_처음_응답을_돌려주고_기록은_한_번(
    db_client, session, bearer, cid
):
    headers, _ = bearer
    body = {"gender": "male", "height_cm": 99.1}

    first = await db_client.post(onboarding_url(cid), json=body, headers=with_key(headers))
    second = await db_client.post(onboarding_url(cid), json=body, headers=with_key(headers))

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert len(await growth_rows(session, cid)) == 1


async def test_Idempotency_Key_가_없으면_400_이고_아무것도_안_바뀐다(
    db_client, session, bearer, cid
):
    headers, parent_1 = bearer

    response = await db_client.post(
        onboarding_url(cid), json={"relation": "mother", "height_cm": 98.5}, headers=headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"
    assert await relation_of(session, child_id=cid, parent_id=parent_1) is ParentChildRelation.OTHER
    assert await growth_rows(session, cid) == []


@pytest.mark.parametrize(
    "body",
    [
        {},  # 화면은 아무것도 안 적으면 보내지 않는다 — 와도 저장할 것이 없다
        {"safety": [{"type": "allergy", "label": "땅콩"}]},  # 알레르기는 승인 게이트로만
        {"safety_status": "none"},
        {"one_line": "요즘 블록 놀이를 좋아해요"},  # 한 줄은 홈 입력으로만
        {"height_cm": 0},
        {"weight_kg": -1},
        {"height_cm": 301},  # 오타 막기 — DB 칸(999.9)에 닿기 전에
        {"gender": "unknown"},
        {"relation": "uncle"},
    ],
)
async def test_받지_않는_본문은_400_이고_아무것도_안_바뀐다(db_client, session, bearer, cid, body):
    headers, _ = bearer

    response = await db_client.post(onboarding_url(cid), json=body, headers=with_key(headers))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert await growth_rows(session, cid) == []
    gender = await session.scalar(select(Child.gender).where(Child.id == cid))
    assert gender == "undisclosed"


async def test_보호자2의_온보딩은_보호자2의_관계만_바꾼다(db_client, session, bearer, cid):
    _, parent_1 = bearer
    headers_2, parent_2 = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=parent_2, child_id=cid)

    response = await db_client.post(
        onboarding_url(cid), json={"relation": "father"}, headers=with_key(headers_2)
    )

    assert response.status_code == 200
    assert response.json()["child"]["role"] == "member"
    assert (
        await relation_of(session, child_id=cid, parent_id=parent_2) is ParentChildRelation.FATHER
    )
    assert await relation_of(session, child_id=cid, parent_id=parent_1) is ParentChildRelation.OTHER


async def test_보호자3이_남의_아이에_보내면_403(db_client, session, bearer, cid):
    _, parent_1 = bearer
    headers, _ = await issue_bearer(session, token="test-token-stranger")

    response = await db_client.post(
        onboarding_url(cid),
        json={"relation": "mother", "height_cm": 98.5},
        headers=with_key(headers),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"
    assert await growth_rows(session, cid) == []
    assert await relation_of(session, child_id=cid, parent_id=parent_1) is ParentChildRelation.OTHER
