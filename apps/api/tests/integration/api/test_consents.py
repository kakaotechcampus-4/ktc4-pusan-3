"""설정 화면에서 동의 켜고 끄기 — `GET /consents` · `POST /consents` (#181).

화면(`apps/web/src/components/consent-section.tsx`)은 **선택 동의만** 스위치로 그린다
(지금은 위치 하나).
켜고 끈 뒤에는 응답을 쓰지 않고 GET 을 다시 불러온다. 그래서 정확해야 하는 쪽은 GET 의 effective 다.

여기서 지키는 것 (#181 본문).
    ① 받는 버전은 GET /policies 가 지금 보여 주는 버전 하나 — 가입 검사와 같은 함수
    ② append-only — 끄기는 withdrawn 행을 더한다. 이력을 고치지 않는다
    ③ 같은 요청이 두 번 와도 기록은 한 번
    ④ 필수 동의는 끌 수 없다 — 필수를 끄는 것은 곧 탈퇴다
    ⑤ 계정 동의는 로그인한 보호자 본인, 아이 동의는 그 아이에 연결된 보호자만

등장하는 사람은 전부 테스트용 가짜다. 보호자 1 = 아이 A 를 등록한 보호자,
보호자 2 = 아이 A 와 상관없는 다른 집 보호자(아이 B 의 보호자).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domains.consent.models import Consent, ConsentAction, ConsentScope
from app.domains.policy.repository import find_active_versions, register_version

from .conftest import issue_bearer, link_child

CONSENTS = "/api/v1/consents"


@pytest.fixture
async def current(session) -> dict[str, str]:
    """scope → 지금 보여 주는 약관 버전. 새 버전을 등록해도 테스트가 따라간다."""
    rows = await find_active_versions(session, now=datetime.now(UTC))
    return {row.scope.value: row.version for row in rows}


async def account_rows(session, parent_id, scope: ConsentScope) -> list[Consent]:
    rows = await session.scalars(
        select(Consent)
        .where(Consent.subject_parent_id == parent_id, Consent.scope == scope)
        .order_by(Consent.acted_at, Consent.id)
    )
    return list(rows.all())


def body(scope: str, action: str, version: str, **extra) -> dict:
    return {"scope": scope, "action": action, "policy_version": version, **extra}


# ── POST /consents ─────────────────────────────────────────────────


async def test_보호자1이_위치를_켜고_끄면_이력이_쌓이고_effective_가_따라간다(
    db_client, session, bearer, current
):
    headers, parent_1 = bearer

    on = await db_client.post(
        CONSENTS, json=body("location", "granted", current["location"]), headers=headers
    )
    off = await db_client.post(
        CONSENTS, json=body("location", "withdrawn", current["location"]), headers=headers
    )

    assert (on.status_code, off.status_code) == (201, 201)
    assert on.json()["consent"]["action"] == "granted"
    assert on.json()["effective"]["location"] is True
    assert off.json()["consent"]["policy_version"] == current["location"]
    assert off.json()["effective"]["location"] is False
    # 요청과 테스트가 같은 세션이라 commit 안 된 값도 보인다 — 되감은 뒤 남은 것만 본다
    await session.rollback()
    rows = await account_rows(session, parent_1, ConsentScope.LOCATION)
    assert [row.action for row in rows] == [ConsentAction.GRANTED, ConsentAction.WITHDRAWN]
    assert all(row.actor_parent_id == parent_1 for row in rows)


async def test_같은_요청을_두_번_보내도_기록은_한_번(db_client, session, bearer, current):
    headers, parent_1 = bearer
    request = body("location", "granted", current["location"])

    first = await db_client.post(CONSENTS, json=request, headers=headers)
    second = await db_client.post(CONSENTS, json=request, headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json()["consent"]["id"] == first.json()["consent"]["id"]
    assert len(await account_rows(session, parent_1, ConsentScope.LOCATION)) == 1


@pytest.mark.parametrize("scope", ["service_terms", "privacy_account"])
async def test_계정_필수_동의는_끌_수_없다(db_client, session, bearer, current, scope):
    headers, parent_1 = bearer

    response = await db_client.post(
        CONSENTS, json=body(scope, "withdrawn", current[scope]), headers=headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert await account_rows(session, parent_1, ConsentScope(scope)) == []


@pytest.mark.parametrize("scope", ["child_basic", "child_health"])
async def test_아이_필수_동의도_끌_수_없다(db_client, session, bearer, cid, current, scope):
    headers, _ = bearer

    response = await db_client.post(
        CONSENTS,
        json=body(scope, "withdrawn", current[scope], child_id=str(cid), guardian_attested=True),
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    rows = await session.scalars(select(Consent).where(Consent.child_id == cid))
    assert rows.all() == []


async def publish_next_location(session) -> str:
    """위치 약관의 새 버전을 지금 보이는 버전으로 올린다 (테스트 트랜잭션 안에서만)."""
    version = "test-next"
    await register_version(
        session,
        scope=ConsentScope.LOCATION,
        version=version,
        label="위치정보 수집·이용 (테스트용 새 버전)",
        content="테스트용 본문",
        content_hash="test-hash",
        # 지금 보이는 버전보다 늦게 시작해야 find_active_versions 가 이것을 고른다
        effective_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    return version


async def test_새_약관_버전에_다시_동의하면_새_행이_남는다(db_client, session, bearer, current):
    headers, parent_1 = bearer
    await db_client.post(
        CONSENTS, json=body("location", "granted", current["location"]), headers=headers
    )
    next_version = await publish_next_location(session)

    response = await db_client.post(
        CONSENTS, json=body("location", "granted", next_version), headers=headers
    )

    assert response.status_code == 201
    assert response.json()["consent"]["policy_version"] == next_version
    rows = await account_rows(session, parent_1, ConsentScope.LOCATION)
    assert [row.action for row in rows] == [ConsentAction.GRANTED, ConsentAction.GRANTED]


async def test_지금_안_보이는_옛_버전으로도_철회는_된다(db_client, session, bearer, current):
    # 위치 약관 제10조 "언제든 끌 수 있다" — 약관이 바뀌었다고 끄는 길이 막히면 안 된다
    headers, parent_1 = bearer
    old_version = current["location"]
    await db_client.post(CONSENTS, json=body("location", "granted", old_version), headers=headers)
    await publish_next_location(session)

    response = await db_client.post(
        CONSENTS, json=body("location", "withdrawn", old_version), headers=headers
    )

    assert response.status_code == 201
    assert response.json()["effective"]["location"] is False
    rows = await account_rows(session, parent_1, ConsentScope.LOCATION)
    assert [row.action for row in rows] == [ConsentAction.GRANTED, ConsentAction.WITHDRAWN]


@pytest.mark.parametrize("action", ["granted", "withdrawn"])
async def test_등록된_적_없는_버전이면_400_policy_version_invalid(
    db_client, session, bearer, action
):
    headers, parent_1 = bearer

    response = await db_client.post(
        CONSENTS, json=body("location", action, "2026-09-01"), headers=headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "policy_version_invalid"
    assert await account_rows(session, parent_1, ConsentScope.LOCATION) == []


async def test_지금_보여_주지_않는_버전이면_400_policy_version_invalid(db_client, session, bearer):
    headers, parent_1 = bearer

    response = await db_client.post(
        CONSENTS, json=body("location", "granted", "2026-09-01"), headers=headers
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "policy_version_invalid"
    assert error["detail"] == {"scope": "location", "policy_version": "2026-09-01"}
    assert await account_rows(session, parent_1, ConsentScope.LOCATION) == []


@pytest.mark.parametrize(
    "make_body",
    [
        # 계정 동의에 child_id 를 붙였다 — 대상이 둘이 된다
        lambda v, cid: body("location", "granted", v["location"], child_id=str(cid)),
        # 아이 동의인데 child_id 가 없다
        lambda v, cid: body("child_basic", "granted", v["child_basic"], guardian_attested=True),
        # 쓰지 않는 동의 종류 (consent/models.py — 목적 · 동의문 · 약관 · 화면이 없다)
        lambda v, cid: body("quality_improve", "granted", "draft-0"),
        lambda v, cid: body("unknown_scope", "granted", "draft-0"),
        lambda v, cid: body("location", "toggle", v["location"]),
    ],
)
async def test_대상이나_종류가_맞지_않으면_400(db_client, session, bearer, cid, current, make_body):
    headers, parent_1 = bearer

    response = await db_client.post(CONSENTS, json=make_body(current, cid), headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    # 표 전체를 세지 않는다 — 로컬 DB 에 다른 기록이 있어도 깨지지 않게 이 보호자 · 이 아이만 본다
    rows = await session.scalars(
        select(Consent.id).where(
            (Consent.subject_parent_id == parent_1) | (Consent.child_id == cid)
        )
    )
    assert rows.all() == []


async def test_보호자1이_남의_아이_동의를_보내면_403(db_client, session, bearer, current):
    headers, _ = bearer
    _, parent_2 = await issue_bearer(session, token="test-token-other-home")
    child_b = await link_child(session, parent_id=parent_2)

    response = await db_client.post(
        CONSENTS,
        json=body(
            "child_basic",
            "granted",
            current["child_basic"],
            child_id=str(child_b),
            guardian_attested=True,
        ),
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"
    rows = await session.scalars(select(Consent).where(Consent.child_id == child_b))
    assert rows.all() == []


async def test_아이_동의를_받을_때는_법정대리인_확인이_필요하다(
    db_client, session, bearer, cid, current
):
    # 아이 등록과 같은 규칙 — 동의와 법정대리인 확인은 별개 의무다 (개인정보보호법 제22조의2 ①)
    headers, _ = bearer
    without = body("child_basic", "granted", current["child_basic"], child_id=str(cid))

    refused = await db_client.post(CONSENTS, json=without, headers=headers)
    accepted = await db_client.post(
        CONSENTS, json={**without, "guardian_attested": True}, headers=headers
    )

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "consent_required"
    assert accepted.status_code == 201
    (row,) = (await session.scalars(select(Consent).where(Consent.child_id == cid))).all()
    assert (row.scope, row.guardian_attested) == (ConsentScope.CHILD_BASIC, True)


# ── GET /consents ──────────────────────────────────────────────────


async def test_GET_은_내_계정_동의와_그_아이_동의를_최신순으로_돌려준다(
    db_client, session, bearer, cid, current
):
    headers, _ = bearer
    for request in (
        body("location", "granted", current["location"]),
        body(
            "child_basic",
            "granted",
            current["child_basic"],
            child_id=str(cid),
            guardian_attested=True,
        ),
        body("location", "withdrawn", current["location"]),
    ):
        assert (await db_client.post(CONSENTS, json=request, headers=headers)).status_code == 201

    response = await db_client.get(CONSENTS, params={"child_id": str(cid)}, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["effective"] == {
        "service_terms": False,
        "privacy_account": False,
        "location": False,
        "child_basic": True,
        "child_health": False,
    }
    assert [(h["scope"], h["action"]) for h in data["history"]] == [
        ("location", "withdrawn"),
        ("child_basic", "granted"),
        ("location", "granted"),
    ]
    assert data["history"][0]["policy_version"] == current["location"]


async def test_GET_은_다른_보호자의_계정_동의를_섞지_않는다(db_client, session, bearer, current):
    headers_1, _ = bearer
    headers_2, _ = await issue_bearer(session, token="test-token-other-home")
    await db_client.post(
        CONSENTS, json=body("location", "granted", current["location"]), headers=headers_2
    )

    response = await db_client.get(CONSENTS, headers=headers_1)

    assert response.status_code == 200
    assert response.json()["effective"]["location"] is False
    assert response.json()["history"] == []


async def test_GET_child_id_가_없으면_계정_동의만(db_client, bearer):
    headers, _ = bearer

    response = await db_client.get(CONSENTS, headers=headers)

    assert set(response.json()["effective"]) == {"service_terms", "privacy_account", "location"}


async def test_GET_남의_아이면_403(db_client, session, bearer):
    headers, _ = bearer
    _, parent_2 = await issue_bearer(session, token="test-token-other-home")
    child_b = await link_child(session, parent_id=parent_2)

    response = await db_client.get(CONSENTS, params={"child_id": str(child_b)}, headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"
