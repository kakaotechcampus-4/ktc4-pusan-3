"""아이 등록(`POST /children`) · 내 정보(`GET /me`) — #92

화면(`apps/web/src/lib/api/types.ts` 의 CreateChildRequest · Me)과 목 서버
(`apps/web/src/mocks/handlers/children.ts`)가 이미 기대하는 모양을 서버가 지킨다.

여기서 지키는 것 넷.
    ① 아이 · 연결 · 아이 동의 2건은 한 트랜잭션 — 하나라도 막히면 아무것도 남지 않는다
    ② 동의 · 법정대리인 확인 · 약관 버전은 아이를 만들기 전에 본다 (가입과 같은 규칙)
    ③ 보호자당 아이 1명 — 먼저 확인하고, 동시에 와도 DB 제약으로 409 하나만 성공
    ④ /me 는 연결된 아이를 owner · member 로 나누고, 빠진 아이 동의를 알려 준다
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.api.v1.routers import children as children_router
from app.api.v1.routers import me as me_router
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.consent.models import Consent, ConsentAction, ConsentScope

from .conftest import issue_bearer, link_child

CHILDREN = "/api/v1/children"
ME = "/api/v1/me"

TODAY = date(2026, 10, 5)
"""나이 문구가 날짜에 따라 바뀌어서 "오늘" 을 고정한다."""

CHILD_POLICY = "draft-0"
"""아이 동의 둘(child_basic · child_health)의 지금 버전. 동의문 2 · 3 을 새로 등록하면 같이 바꾼다."""

CHILD_CONSENTS = [
    {"scope": "child_basic", "policy_version": CHILD_POLICY},
    {"scope": "child_health", "policy_version": CHILD_POLICY},
]


def register_body(**overrides) -> dict:
    """화면 01 이 보내는 요청. 별명 · 생일은 가짜 값이다 (루트 CLAUDE.md §9)."""
    return {
        "nickname": "테스트아이",
        "birth_date": "2025-11-01",
        "consents": CHILD_CONSENTS,
        "guardian_attested": True,
        **overrides,
    }


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(children_router, "today_kst", lambda: TODAY)
    monkeypatch.setattr(me_router, "today_kst", lambda: TODAY)


async def children_of(session, parent_id) -> list[Child]:
    rows = await session.scalars(select(Child).where(Child.owner_parent_id == parent_id))
    return list(rows.all())


# ── POST /children ─────────────────────────────────────────────────


async def test_등록하면_아이_연결_동의를_한_번에_만든다(db_client, session, bearer):
    headers, parent_id = bearer

    response = await db_client.post(CHILDREN, json=register_body(), headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["nickname"] == "테스트아이"
    assert body["age_display"] == "11개월"
    assert body["role"] == "owner"

    (child,) = await children_of(session, parent_id)
    assert str(child.id) == body["id"]
    assert child.birth_date == date(2025, 11, 1)
    link = await session.scalar(select(ParentChild).where(ParentChild.child_id == child.id))
    # 관계는 02 화면이 받는다. 01 에서는 정하지 않은 것이 사실이라 other 로 둔다 (현식님 #92 댓글)
    assert (link.parent_id, link.relation) == (parent_id, ParentChildRelation.OTHER)

    consents = (await session.scalars(select(Consent).where(Consent.child_id == child.id))).all()
    assert {c.scope for c in consents} == {ConsentScope.CHILD_BASIC, ConsentScope.CHILD_HEALTH}
    assert all(c.action is ConsentAction.GRANTED for c in consents)
    assert all(c.actor_parent_id == parent_id for c in consents)
    assert all(c.guardian_attested is True for c in consents)


@pytest.mark.parametrize("missing", ["child_basic", "child_health"])
async def test_아이_동의가_빠지면_403_이고_아무것도_남지_않는다(
    db_client, session, bearer, missing
):
    headers, parent_id = bearer
    consents = [c for c in CHILD_CONSENTS if c["scope"] != missing]

    response = await db_client.post(
        CHILDREN, json=register_body(consents=consents), headers=headers
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_required"
    assert response.json()["error"]["detail"] == {"scopes": [missing]}
    assert await children_of(session, parent_id) == []


async def test_법정대리인_확인이_없으면_403(db_client, session, bearer):
    """동의와 법정대리인 확인은 별개 의무다 (개인정보보호법 제22조의2 ①)."""
    headers, parent_id = bearer

    response = await db_client.post(
        CHILDREN, json=register_body(guardian_attested=False), headers=headers
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_required"
    assert await children_of(session, parent_id) == []


async def test_지금_보여_주는_약관_버전이_아니면_400(db_client, session, bearer):
    headers, parent_id = bearer
    stale = [{"scope": "child_basic", "policy_version": "2026-09-01"}, CHILD_CONSENTS[1]]

    response = await db_client.post(CHILDREN, json=register_body(consents=stale), headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "policy_version_invalid"
    assert await children_of(session, parent_id) == []


async def test_생일이_오늘보다_뒤면_400(db_client, session, bearer):
    """미래에 태어난 아이는 없다. 나이 계산이 터지는 값을 저장하지 않는다."""
    headers, parent_id = bearer

    response = await db_client.post(
        CHILDREN, json=register_body(birth_date="2026-10-06"), headers=headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert await children_of(session, parent_id) == []


@pytest.mark.parametrize("nickname", ["", "   ", "가" * 21])
async def test_이름은_1자에서_20자(db_client, session, bearer, nickname):
    headers, parent_id = bearer

    response = await db_client.post(
        CHILDREN, json=register_body(nickname=nickname), headers=headers
    )

    assert response.status_code == 400
    assert await children_of(session, parent_id) == []


async def test_이미_아이가_있으면_409(db_client, session, bearer):
    headers, parent_id = bearer
    first = await db_client.post(CHILDREN, json=register_body(), headers=headers)

    second = await db_client.post(CHILDREN, json=register_body(), headers=headers)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "child_already_exists"
    assert len(await children_of(session, parent_id)) == 1


async def test_동시에_두_번_와도_DB_제약으로_하나만_남는다(db_client, session, bearer, monkeypatch):
    """먼저 확인(has_child_link)을 둘 다 통과한 경우 — DB 유니크 제약이 두 번째를 막는다."""
    headers, parent_id = bearer
    first = await db_client.post(CHILDREN, json=register_body(), headers=headers)

    async def nothing_yet(session, *, parent_id):
        return False

    monkeypatch.setattr(children_router, "has_child_link", nothing_yet)
    second = await db_client.post(CHILDREN, json=register_body(), headers=headers)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "child_already_exists"
    assert len(await children_of(session, parent_id)) == 1
    total = await session.scalar(
        select(func.count()).select_from(Consent).where(Consent.actor_parent_id == parent_id)
    )
    assert total == 2  # 두 번째 요청의 동의는 아이와 함께 되돌아갔다


# ── GET /me ────────────────────────────────────────────────────────


async def test_아이가_없으면_빈_목록(db_client, bearer):
    """로그인 직후 화면은 이 목록이 비어 있으면 /start 로 간다."""
    headers, parent_id = bearer

    response = await db_client.get(ME, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"id": str(parent_id), "nickname": None, "children": []}


async def test_등록한_아이는_owner_로_보인다(db_client, bearer):
    headers, _ = bearer
    created = (await db_client.post(CHILDREN, json=register_body(), headers=headers)).json()

    response = await db_client.get(ME, headers=headers)

    assert response.json()["children"] == [
        {
            "child_id": created["id"],
            "nickname": "테스트아이",
            "age_display": "11개월",
            "relation": "other",
            "role": "owner",
            "consent_required": [],
        }
    ]


async def test_함께_보는_보호자에게는_member_로_보인다(db_client, session, bearer):
    _, owner = bearer
    cid = await link_child(session, parent_id=owner)
    member_headers, member = await issue_bearer(session, token="test-token-member")
    await link_child(session, parent_id=member, child_id=cid, relation=ParentChildRelation.FATHER)

    (child,) = (await db_client.get(ME, headers=member_headers)).json()["children"]

    assert child["child_id"] == str(cid)
    assert child["role"] == "member"
    assert child["relation"] == "father"


async def test_동의가_빠진_아이는_consent_required_로_알린다(db_client, session, bearer):
    """동의 없이 연결만 된 아이(픽스처) — 화면은 이 목록으로 동의 화면을 다시 띄운다."""
    headers, parent_id = bearer
    await link_child(session, parent_id=parent_id)

    (child,) = (await db_client.get(ME, headers=headers)).json()["children"]

    assert child["consent_required"] == ["child_basic", "child_health"]


async def test_보관된_아이는_보이지_않는다(db_client, session, bearer):
    headers, parent_id = bearer
    cid = await link_child(session, parent_id=parent_id)
    (await session.get(Child, cid)).deleted_at = datetime.now(UTC)
    await session.flush()

    response = await db_client.get(ME, headers=headers)

    assert response.json()["children"] == []
