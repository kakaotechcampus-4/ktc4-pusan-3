"""알레르기 · 건강 기록 — `GET` · `POST /children/{cid}/health-safety` · `DELETE …/{id}` (E 묶음).

화면(`apps/web/src/lib/api/types.ts` 의 HealthSafety · CreateHealthSafetyRequest ·
HealthSafetyListResponse)과 목 서버(`apps/web/src/mocks/handlers/children.ts`)가 기대하는
모양을 지킨다.

여기서 지키는 것 (루트 CLAUDE.md §2 · §3).
    ① 등록은 승인 게이트 ㉡ — 보호자가 직접 보낸 것만, Idempotency-Key 필수, 같은 키는 처음 응답
    ② 같은 항목(종류 + 이름)이 이미 있으면 409 — 내린 항목은 다시 등록할 수 있다
    ③ 종류와 맞지 않는 심각도는 400 (알레르기는 class_0~6, 나머지는 mild~anaphylaxis)
    ④ 건강정보 동의(child_health)가 없으면 저장하지 않는다 — 403 consent_required
    ⑤ 내리기는 행을 지우지 않고 retracted 로 — 목록에서 빠지고 Agent 필터도 안 읽는다

등장하는 사람은 전부 테스트용 가짜다. 보호자 1 = 아이 A 를 등록한 보호자,
보호자 2 = 아이 A 와 상관없는 다른 집 보호자(아이 B 의 보호자). 알레르기 이름도 예시 값이다.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api import idempotency
from app.api.errors import constraint_name
from app.api.v1.routers import health_safety as health_safety_router
from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import record_consent
from app.domains.identity.models import Parent
from app.domains.policy.repository import find_active_versions
from app.domains.safety.models import HealthSafety, SafetyKind, SafetyStatus
from app.domains.safety.repository import create_safety
from app.main import app

from .conftest import issue_bearer, link_child


def safety_url(child_id, safety_id=None) -> str:
    base = f"/api/v1/children/{child_id}/health-safety"
    return base if safety_id is None else f"{base}/{safety_id}"


def with_key(headers: dict, key: str = "safety-key-1") -> dict:
    return {**headers, "Idempotency-Key": key}


@pytest.fixture(autouse=True)
def _clean_keys():
    idempotency.clear()
    yield
    idempotency.clear()


async def grant_child_health(session, *, child_id, parent_id) -> None:
    """아이 등록이 받는 건강정보 동의 — link_child 픽스처는 동의 없이 아이를 만든다."""
    versions = {
        row.scope: row for row in await find_active_versions(session, now=datetime.now(UTC))
    }
    await record_consent(
        session,
        actor_parent_id=parent_id,
        child_id=child_id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.GRANTED,
        policy_version_id=versions[ConsentScope.CHILD_HEALTH].id,
        guardian_attested=True,
    )


@pytest.fixture
async def consented(session, bearer, cid):
    """보호자 1 이 아이 A 에 건강정보 동의를 해 둔 상태."""
    _, parent_1 = bearer
    await grant_child_health(session, child_id=cid, parent_id=parent_1)
    return cid


async def rows_of(session, child_id) -> list[HealthSafety]:
    rows = await session.scalars(select(HealthSafety).where(HealthSafety.child_id == child_id))
    return list(rows.all())


# ── GET ────────────────────────────────────────────────────────────


async def test_목록은_내리지_않은_기록만_돌려준다(db_client, session, bearer, cid):
    headers, parent_1 = bearer
    await session.execute(update(Parent).where(Parent.id == parent_1).values(nickname="지은"))
    milk = await create_safety(
        session,
        child_id=cid,
        parent_id=parent_1,
        kind=SafetyKind.ALLERGY,
        label="우유",
        severity="class_3",
        reactions=["두드러기"],
    )
    gone = await create_safety(
        session, child_id=cid, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="땅콩"
    )
    gone.status = SafetyStatus.RETRACTED
    await session.flush()

    response = await db_client.get(safety_url(cid), headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert [item["id"] for item in data["items"]] == [str(milk.id)]
    item = data["items"][0]
    assert {key: item[key] for key in ("kind", "type", "label", "severity", "reactions")} == {
        "kind": "health_safety",
        "type": "allergy",
        "label": "우유",
        "severity": "class_3",
        "reactions": ["두드러기"],
    }
    assert item["created_by"] == {"parent_id": str(parent_1), "nickname": "지은"}
    assert data["updated_at"]


async def test_이름_없는_계정이_적었으면_created_by_는_null(db_client, session, bearer, cid):
    # 화면이 "null님이 확인" 을 그리지 않게 — 이름은 #172 이후 가입에서만 필수라 옛 계정은 비어 있다
    headers, parent_1 = bearer
    await create_safety(
        session, child_id=cid, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="우유"
    )

    response = await db_client.get(safety_url(cid), headers=headers)

    assert response.json()["items"][0]["created_by"] is None


async def test_보호자2가_남의_아이_기록을_보면_403(db_client, session, cid):
    headers, _ = await issue_bearer(session, token="test-token-other-home")

    response = await db_client.get(safety_url(cid), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"


# ── POST (승인 게이트 ㉡) ──────────────────────────────────────────


async def test_보호자1이_알레르기를_등록하면_201(db_client, session, bearer, consented):
    headers, parent_1 = bearer
    body = {"type": "allergy", "label": "우유", "severity": "class_2", "reactions": ["두드러기"]}

    response = await db_client.post(safety_url(consented), json=body, headers=with_key(headers))

    assert response.status_code == 201
    safety = response.json()["safety"]
    assert (safety["type"], safety["label"], safety["severity"]) == ("allergy", "우유", "class_2")
    # 요청과 테스트가 같은 세션이라 commit 안 된 값도 보인다 — 되감은 뒤 DB 에 남은 것만 본다
    await session.rollback()
    (row,) = await rows_of(session, consented)
    assert (row.status, row.created_by) == (SafetyStatus.ACTIVE, parent_1)


async def test_심각도_메모를_null_로_보내면_안_보낸_것으로_받는다(
    db_client, session, bearer, consented
):
    headers, _ = bearer
    body = {"type": "allergy", "label": "우유", "severity": None, "notes": None}

    response = await db_client.post(safety_url(consented), json=body, headers=with_key(headers))

    assert response.status_code == 201
    assert (response.json()["safety"]["severity"], response.json()["safety"]["notes"]) == (
        None,
        None,
    )


async def test_실패한_요청은_키를_남기지_않는다(db_client, session, bearer, cid):
    # 동의가 없어 403 → 동의한 뒤 같은 키로 다시 보내면 처음 403 을 재생하지 않고 등록된다
    headers, parent_1 = bearer
    body = {"type": "allergy", "label": "우유"}

    refused = await db_client.post(safety_url(cid), json=body, headers=with_key(headers))
    await grant_child_health(session, child_id=cid, parent_id=parent_1)
    accepted = await db_client.post(safety_url(cid), json=body, headers=with_key(headers))

    assert (refused.status_code, accepted.status_code) == (403, 201)


async def test_같은_키로_두_번_보내면_처음_응답이고_기록은_한_번(
    db_client, session, bearer, consented
):
    headers, _ = bearer
    body = {"type": "allergy", "label": "우유"}

    first = await db_client.post(safety_url(consented), json=body, headers=with_key(headers))
    second = await db_client.post(safety_url(consented), json=body, headers=with_key(headers))

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json() == first.json()
    assert len(await rows_of(session, consented)) == 1


async def test_Idempotency_Key_가_없으면_400(db_client, session, bearer, consented):
    headers, _ = bearer

    response = await db_client.post(
        safety_url(consented), json={"type": "allergy", "label": "우유"}, headers=headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"
    assert await rows_of(session, consented) == []


async def test_같은_항목이_있으면_409_내린_항목은_다시_등록할_수_있다(
    db_client, session, bearer, consented
):
    headers, parent_1 = bearer
    await create_safety(
        session, child_id=consented, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="우유"
    )
    retracted = await create_safety(
        session, child_id=consented, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="땅콩"
    )
    retracted.status = SafetyStatus.RETRACTED
    await session.flush()

    duplicate = await db_client.post(
        safety_url(consented),
        json={"type": "allergy", "label": "우유"},
        headers=with_key(headers, "key-milk"),
    )
    again = await db_client.post(
        safety_url(consented),
        json={"type": "allergy", "label": "땅콩"},
        headers=with_key(headers, "key-peanut"),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "already_exists"
    assert again.status_code == 201


async def test_DB_도_active_인_같은_항목을_두_줄_두지_않는다(session, bearer, consented):
    """코드 확인(has_active_safety)을 거치지 않아도 부분 고유 인덱스가 두 번째 줄을 막는다.

    내린(retracted) 줄은 인덱스 밖이라, 같은 이름을 내리고 다시 등록하는 것은 된다.
    """
    _, parent_1 = bearer
    first = await create_safety(
        session, child_id=consented, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="땅콩"
    )

    with pytest.raises(IntegrityError) as caught:
        async with session.begin_nested():
            await create_safety(
                session,
                child_id=consented,
                parent_id=parent_1,
                kind=SafetyKind.ALLERGY,
                label="땅콩",
            )

    assert constraint_name(caught.value) == "uq_health_safety_active_item"
    first.status = SafetyStatus.RETRACTED
    await session.flush()
    await create_safety(
        session, child_id=consented, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="땅콩"
    )


async def test_동시에_온_같은_항목은_DB_가_막고_409(
    db_client, session, bearer, consented, monkeypatch
):
    """두 보호자가 거의 동시에 누르면 둘 다 코드 확인에서 "아직 없다" 를 본다.

    그 상황을 흉내 낸다 — 먼저 저장된 줄이 있는데 코드 확인은 "없다" 고 답하게 한다.
    DB 가 두 번째를 거절하고, 서버는 그것을 500 이 아니라 409 로 돌려줘야 한다.
    """
    headers, parent_1 = bearer
    await create_safety(
        session, child_id=consented, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="땅콩"
    )

    async def not_yet(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(health_safety_router, "has_active_safety", not_yet)

    response = await db_client.post(
        safety_url(consented), json={"type": "allergy", "label": "땅콩"}, headers=with_key(headers)
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_exists"
    rows = await rows_of(session, consented)
    assert [row.status for row in rows] == [SafetyStatus.ACTIVE]


@pytest.mark.parametrize(
    "body",
    [
        {"type": "allergy", "label": "우유", "severity": "mild"},  # 알레르기는 class 로
        {"type": "chronic_disease", "label": "천식", "severity": "class_3"},  # 나머지는 mild~
        {"type": "food", "label": "우유"},  # 모르는 종류
        {"type": "allergy", "label": "   "},  # 이름이 비었다
        {"type": "allergy", "label": "우유", "category": "식품"},  # #298 에서 지운 칸
        {"type": "allergy", "label": "우유", "reactions": ["   "]},  # 공백뿐인 반응
    ],
)
async def test_받을_수_없는_본문은_400(db_client, session, bearer, consented, body):
    headers, _ = bearer

    response = await db_client.post(safety_url(consented), json=body, headers=with_key(headers))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert await rows_of(session, consented) == []


async def test_같은_항목에_틀린_심각도면_409_가_아니라_400(db_client, session, bearer, consented):
    # 목(children.ts)과 같은 순서 — 본문이 틀린 것을 먼저 알린다
    headers, parent_1 = bearer
    await create_safety(
        session, child_id=consented, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="우유"
    )

    response = await db_client.post(
        safety_url(consented),
        json={"type": "allergy", "label": "우유", "severity": "mild"},
        headers=with_key(headers),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"


async def test_건강정보_동의가_없으면_403_이고_저장하지_않는다(db_client, session, bearer, cid):
    headers, _ = bearer

    response = await db_client.post(
        safety_url(cid), json={"type": "allergy", "label": "우유"}, headers=with_key(headers)
    )

    assert response.status_code == 403
    error = response.json()["error"]
    assert (error["code"], error["detail"]["scopes"]) == ("consent_required", ["child_health"])
    assert await rows_of(session, cid) == []


async def test_보호자1이_남의_아이에_등록하면_403(db_client, session, bearer):
    headers, _ = bearer
    _, parent_2 = await issue_bearer(session, token="test-token-other-home")
    child_b = await link_child(session, parent_id=parent_2)
    await grant_child_health(session, child_id=child_b, parent_id=parent_2)

    response = await db_client.post(
        safety_url(child_b), json={"type": "allergy", "label": "우유"}, headers=with_key(headers)
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"
    assert await rows_of(session, child_b) == []


# ── DELETE (내리기) ────────────────────────────────────────────────


async def test_내리면_204_목록에서_빠지고_다시_내리면_404(db_client, session, bearer, cid):
    headers, parent_1 = bearer
    row = await create_safety(
        session, child_id=cid, parent_id=parent_1, kind=SafetyKind.ALLERGY, label="우유"
    )

    first = await db_client.delete(safety_url(cid, row.id), headers=headers)
    second = await db_client.delete(safety_url(cid, row.id), headers=headers)

    assert (first.status_code, second.status_code) == (204, 404)
    assert (await db_client.get(safety_url(cid), headers=headers)).json()["items"] == []
    # 행은 남는다 — 지우지 않고 내린다
    await session.rollback()
    await session.refresh(row)
    assert (row.status, row.updated_by) == (SafetyStatus.RETRACTED, parent_1)


async def test_내_아이_주소로_다른_아이의_기록을_내리면_404(db_client, session, bearer, cid):
    headers, _ = bearer
    _, parent_2 = await issue_bearer(session, token="test-token-other-home")
    child_b = await link_child(session, parent_id=parent_2)
    row_b = await create_safety(
        session, child_id=child_b, parent_id=parent_2, kind=SafetyKind.ALLERGY, label="우유"
    )

    # 보호자 1 이 자기 아이 A 의 주소에 아이 B 의 기록 id 를 실어 보낸다
    response = await db_client.delete(safety_url(cid, row_b.id), headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    await session.refresh(row_b)
    assert row_b.status is SafetyStatus.ACTIVE


def test_Swagger_에_등록_응답_모양이_나온다():
    # API 계약 정본은 Swagger 다 — JSONResponse 를 돌려줘도 201 의 모양이 문서에 있어야 한다
    schema = app.openapi()
    created = schema["paths"]["/api/v1/children/{cid}/health-safety"]["post"]["responses"]["201"]
    ref = created["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/CreateHealthSafetyResponse")
