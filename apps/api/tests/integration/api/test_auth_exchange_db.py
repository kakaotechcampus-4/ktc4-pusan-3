"""교환·가입·세션 — 명세 docs/api/auth-kakao-v1.md §9 의 A-01~A-05 · A-12~A-15 · A-19

실제 Postgres 를 본다. 각 테스트는 바깥 트랜잭션을 마지막에 롤백하므로 서로 데이터를
공유하지 않는다 (tests/conftest.py 의 session 픽스처).

여기서 지키는 것 넷.
    ① 동의 전에는 parent 가 생기지 않는다 (A-01 · A-13 · §6-1)
    ② 1회용 코드는 정확히 한 번만 쓰인다 (A-04 · A-12)
    ③ 🚨 bind 가 틀리면 코드를 되돌려주지 않는다 (A-05 · §7-2)
    ④ 세션 무효화는 즉시다 (A-15) — 탈퇴는 401 이 아니라 404 (A-19)
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.api.deps.auth import hash_token
from app.domains.consent.models import Consent, ConsentScope
from app.domains.identity.models import AuthHandoff, AuthIdentity, AuthProvider, AuthSession, Parent
from app.domains.policy.models import PolicyVersion

BIND = "A" * 43
OTHER_BIND = "B" * 43
KAKAO_USER_ID = "1234567890"
POLICY = "draft-0"
"""🚨 계약서 §3-5 예시는 "2026-09-01" 이지만 그건 등록된 버전이 아니다.

가입은 이제 policy_version 테이블에 등록되고 적용 기간 안인 버전만 받는다 (PR C).
지금 등록된 것은 PR B 마이그레이션이 시드한 placeholder "draft-0" 하나뿐이라 여기서도
그 값을 쓴다. 실제 약관이 확정되면 새 버전을 등록하고 이 상수와 계약서 예시를 함께
바꾼다 — 한쪽만 바꾸면 이 테스트가 그 사실을 잡는다.
"""

UNREGISTERED_POLICY = "2026-09-01"
"""등록된 적 없는 버전. 낡은 화면이 보내는 값을 흉내낸다."""

REQUIRED_CONSENTS = [
    {"scope": "service_terms", "policy_version": POLICY},
    {"scope": "privacy_account", "policy_version": POLICY},
]


async def seed_handoff(
    session,
    *,
    code: str,
    bind: str = BIND,
    parent_id=None,
    provider_user_id: str | None = KAKAO_USER_ID,
    ttl_seconds: int = 120,
) -> None:
    """콜백이 남겼을 1회용 코드 1건. parent_id 와 provider_user_id 중 하나만 찬다."""
    session.add(
        AuthHandoff(
            provider=AuthProvider.KAKAO,
            code_hash=hash_token(code),
            bind_hash=hash_token(bind),
            parent_id=parent_id,
            provider_user_id=None if parent_id else provider_user_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
    )
    await session.flush()


async def seed_member(session, *, deleted: bool = False) -> Parent:
    """이미 가입한 보호자 + 카카오 연결."""
    parent = Parent(deleted_at=datetime.now(UTC) if deleted else None)
    session.add(parent)
    await session.flush()
    session.add(
        AuthIdentity(
            parent_id=parent.id,
            provider=AuthProvider.KAKAO,
            provider_user_id=KAKAO_USER_ID,
        )
    )
    await session.flush()
    return parent


TRACKED = (Parent, AuthIdentity, AuthSession, Consent, AuthHandoff)
"""행 수 변화를 지켜볼 테이블."""


async def row_count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def snapshot(session) -> dict:
    """지금 행 수를 찍어 둔다. 이 뒤로는 기준점 대비 증감만 본다.

    🚨 절대 개수를 세지 않는다. 테스트끼리는 바깥 트랜잭션 롤백으로 격리되지만, 로컬에서
       앱을 한 번 써 본 개발자의 DB 에는 커밋된 행이 남아 있고 그건 롤백 대상이 아니다.
       빈 DB 에서만 통과하는 테스트는 "내 로컬에서만 빨간불" 을 만든다 (#45).
    """
    return {model: await row_count(session, model) for model in TRACKED}


async def changed(session, base: dict) -> dict:
    """기준점 이후 달라진 것만 {테이블 이름: 증감} 으로 돌려준다.

    변화가 없으면 빈 dict 다. 그래서 "아무것도 만들지 않았다" 를 `== {}` 한 줄로 쓸 수
    있고, 기대한 것 외에 **다른 테이블이 건드려졌는지까지** 함께 잡힌다.
    """
    now = {model: await row_count(session, model) for model in TRACKED}
    return {
        model.__name__: now[model] - base[model]
        for model in TRACKED
        if now[model] != base[model]
    }


# ── A-01 · A-02 신규 가입 ─────────────────────────────────────────────────────


async def test_first_time_member_gets_consent_code_and_no_parent(db_client, session):
    """A-01. 처음 보는 회원번호는 대기표만 받는다. 🚨 parent 가 생기면 §6-1 위반이다."""
    await seed_handoff(session, code="handoff-code")
    base = await snapshot(session)

    response = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "consent_required"
    assert body["consent_code"]
    assert "token" not in body
    # 교환용 코드가 대기표로 바뀌었을 뿐 (소비 -1, 발급 +1) 계정은 하나도 안 생겼다.
    assert await changed(session, base) == {}


async def test_signup_creates_account_and_session_in_one_go(db_client, session):
    """A-02. 가입은 parent · auth_identity · consent 를 함께 만들고 세션을 낸다."""
    await seed_handoff(session, code="handoff-code")
    exchanged = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND}
    )
    consent_code = exchanged.json()["consent_code"]
    base = await snapshot(session)

    response = await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={"consent_code": consent_code, "bind": BIND, "consents": REQUIRED_CONSENTS},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_new"] is True
    assert body["token"]
    assert body["expires_in"] == 43200
    assert body["consent_required"] == []
    # 한 트랜잭션에서 만들어진 것 전부. 대기표는 소비돼 -1 이다.
    assert await changed(session, base) == {
        "Parent": 1,
        "AuthIdentity": 1,
        "Consent": 2,
        "AuthSession": 1,
        "AuthHandoff": -1,
    }


async def test_signup_rejects_a_code_issued_for_another_provider(db_client, session):
    """🚨 #83. 카카오로 발급된 코드로는 다른 provider 에 가입할 수 없다.

    막지 않으면 카카오 회원번호가 Google 식별자로 등록된다 — 소비 조건이 코드와 만료뿐이고
    identity 의 provider 는 URL 경로에서 오기 때문이다. 나중에 구글 로그인을 붙였을 때
    같은 값을 가진 실제 사용자의 자리를 미리 차지할 수 있다.

    실패는 없는 코드와 구분되지 않는다 (§8-1).
    """
    await seed_handoff(session, code="handoff-code")
    consent_code = (
        await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    ).json()["consent_code"]
    base = await snapshot(session)

    response = await db_client.post(
        "/api/v1/auth/google/signup",
        json={"consent_code": consent_code, "bind": BIND, "consents": REQUIRED_CONSENTS},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_handoff"
    assert await changed(session, base) == {}


async def test_rejected_provider_mismatch_leaves_the_ticket_usable(db_client, session):
    """🚨 #83. 다른 provider 로 내밀어도 대기표는 타지 않는다.

    provider 를 WHERE 절에서 거르므로 지울 행을 못 찾는다. bind 불일치(§7-2)와 다르게
    다루는 이유 — provider 는 비밀이 아니라 맞혀볼 것이 없어서, 태워도 얻는 게 없고
    엉뚱한 경로로 한 번 보내진 사용자만 로그인부터 다시 하게 된다.
    """
    await seed_handoff(session, code="handoff-code")
    consent_code = (
        await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    ).json()["consent_code"]
    body = {"consent_code": consent_code, "bind": BIND, "consents": REQUIRED_CONSENTS}

    await db_client.post("/api/v1/auth/google/signup", json=body)
    response = await db_client.post("/api/v1/auth/kakao/signup", json=body)

    assert response.status_code == 200
    assert response.json()["is_new"] is True


async def test_exchange_rejects_a_code_issued_for_another_provider(db_client, session):
    """🚨 #83. 교환도 같은 규칙이다 — 두 엔드포인트가 같은 소비 함수를 쓴다."""
    member = await seed_member(session)
    await seed_handoff(session, code="handoff-code", parent_id=member.id)
    base = await snapshot(session)

    response = await db_client.post(
        "/api/v1/auth/google", json={"code": "handoff-code", "bind": BIND}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_handoff"
    assert await changed(session, base) == {}


async def test_signup_without_required_scope_creates_nothing(db_client, session):
    """🚨 A-13. 필수 스코프가 빠지면 403 이고 parent 는 만들어지지 않는다."""
    await seed_handoff(session, code="handoff-code")
    exchanged = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND}
    )
    consent_code = exchanged.json()["consent_code"]
    base = await snapshot(session)

    response = await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={
            "consent_code": consent_code,
            "bind": BIND,
            "consents": [{"scope": "service_terms", "policy_version": POLICY}],
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "consent_required"
    # 🚨 아무것도 만들어지지 않았다. 대기표조차 태우지 않는다.
    assert await changed(session, base) == {}


async def test_signup_with_unregistered_policy_version_is_rejected(db_client, session):
    """🚨 등록되지 않은 정책 버전으로는 가입할 수 없다 (노션 정책 정본 §5).

    재현할 수 없는 문구에 "동의했다" 고 남기면 증빙이 증빙이 아니다. parent 도 만들지
    않는다 — 동의 없는 계정이 남는 것이 §6-1 이 막으려는 상태다.
    """
    await seed_handoff(session, code="handoff-code")
    consent_code = (
        await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    ).json()["consent_code"]
    base = await snapshot(session)

    response = await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={
            "consent_code": consent_code,
            "bind": BIND,
            "consents": [
                {"scope": "service_terms", "policy_version": UNREGISTERED_POLICY},
                {"scope": "privacy_account", "policy_version": UNREGISTERED_POLICY},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "policy_version_invalid"
    # 🚨 parent 도 동의도 만들어지지 않았다. 대기표조차 태우지 않는다.
    assert await changed(session, base) == {}


async def test_unregistered_policy_version_leaves_the_ticket_usable(db_client, session):
    """낡은 화면은 공격이 아니다 — 대기표를 태우지 않는다.

    새로고침 한 번이면 될 일에 로그인부터 다시 시키지 않는다. 필수 동의 누락(A-13)과
    같은 판단이다.
    """
    await seed_handoff(session, code="handoff-code")
    consent_code = (
        await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    ).json()["consent_code"]
    await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={
            "consent_code": consent_code,
            "bind": BIND,
            "consents": [
                {"scope": "service_terms", "policy_version": UNREGISTERED_POLICY},
                {"scope": "privacy_account", "policy_version": UNREGISTERED_POLICY},
            ],
        },
    )

    retry = await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={"consent_code": consent_code, "bind": BIND, "consents": REQUIRED_CONSENTS},
    )

    assert retry.status_code == 200
    assert retry.json()["is_new"] is True


async def test_signup_links_consent_to_the_registered_policy_version(db_client, session):
    """저장된 것은 문자열이 아니라 policy_version 행의 id 다 — 본문을 재현할 수 있다."""
    await seed_handoff(session, code="handoff-code")
    consent_code = (
        await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    ).json()["consent_code"]
    await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={"consent_code": consent_code, "bind": BIND, "consents": REQUIRED_CONSENTS},
    )

    rows = (await session.execute(select(Consent))).scalars().all()
    assert len(rows) == 2
    for row in rows:
        version = await session.get(PolicyVersion, row.policy_version_id)
        assert version.scope == row.scope
        assert version.version == POLICY
        assert row.subject_parent_id == row.actor_ref, "본인이 본인 계정에 동의한 가입 경로"


async def test_missing_consent_leaves_the_ticket_usable(db_client, session):
    """동의를 빼먹은 것은 공격이 아니라 선택이다. 대기표를 태우지 않는다.

    체크박스 하나 놓쳤다고 로그인부터 다시 하게 만들지 않는다 — bind 불일치와 다르다.
    """
    await seed_handoff(session, code="handoff-code")
    consent_code = (
        await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    ).json()["consent_code"]
    await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={"consent_code": consent_code, "bind": BIND, "consents": []},
    )

    retry = await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={"consent_code": consent_code, "bind": BIND, "consents": REQUIRED_CONSENTS},
    )

    assert retry.status_code == 200
    assert retry.json()["is_new"] is True


async def test_token_responses_are_never_cached(db_client, session):
    """🚨 토큰이 실리는 응답은 캐시에 남기지 않는다 — 명세 §3-4 · §3-5.

    뒤로 가기나 공용 PC 의 브라우저 캐시에서 세션 토큰·가입 대기표를 그대로 꺼낼 수 있다.
    302 둘은 _redirect() 가 붙여주지만 이 둘은 pydantic 모델을 그대로 돌려주는 경로라
    따로 붙여야 한다.
    """
    await seed_handoff(session, code="handoff-code")

    exchanged = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND}
    )
    signed = await db_client.post(
        "/api/v1/auth/kakao/signup",
        json={
            "consent_code": exchanged.json()["consent_code"],
            "bind": BIND,
            "consents": REQUIRED_CONSENTS,
        },
    )

    assert exchanged.headers["cache-control"] == "no-store"
    assert signed.headers["cache-control"] == "no-store"


# ── A-03 기존 회원 ────────────────────────────────────────────────────────────


async def test_returning_member_gets_session_without_new_identity(db_client, session):
    """A-03. 기존 회원은 바로 세션이다. auth_identity 행이 늘지 않는다."""
    parent = await seed_member(session)
    await seed_handoff(session, code="handoff-code", parent_id=parent.id)
    base = await snapshot(session)

    response = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_new"] is False
    assert body["parent"]["id"] == str(parent.id)
    assert body["parent"]["nickname"] is None
    # 세션만 생기고 코드는 소비된다. AuthIdentity 가 목록에 없다 = 연결이 늘지 않았다.
    assert await changed(session, base) == {"AuthSession": 1, "AuthHandoff": -1}


async def test_returning_member_sees_outstanding_consents(db_client, session):
    """동의한 적 없는 기존 회원은 consent_required 가 채워진다 (§6-2 ②)."""
    parent = await seed_member(session)
    await seed_handoff(session, code="handoff-code", parent_id=parent.id)

    body = (
        await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    ).json()

    assert set(body["consent_required"]) == {
        ConsentScope.SERVICE_TERMS.value,
        ConsentScope.PRIVACY_ACCOUNT.value,
    }


# ── A-04 · A-05 · A-12 1회용 코드 ─────────────────────────────────────────────


async def test_handoff_code_works_exactly_once(db_client, session):
    """🚨 A-04. 두 번째 교환은 401 이다. DELETE … RETURNING 이 그것을 보장한다."""
    parent = await seed_member(session)
    await seed_handoff(session, code="handoff-code", parent_id=parent.id)

    first = await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    second = await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})

    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "invalid_handoff"


async def test_wrong_bind_burns_the_code(db_client, session):
    """🚨 A-05. bind 가 틀리면 세션을 내주지 않고, 코드도 되돌려주지 않는다.

    롤백해서 재시도 기회를 주면 1회용 코드를 가로챈 쪽이 bind 를 맞출 때까지 반복할 수
    있다 (§7-2). 그래서 두 번째 시도는 올바른 bind 로도 실패해야 한다.
    """
    parent = await seed_member(session)
    await seed_handoff(session, code="handoff-code", parent_id=parent.id)
    base = await snapshot(session)

    wrong = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": OTHER_BIND}
    )
    retry = await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})

    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_handoff"
    assert retry.status_code == 401
    # 코드는 태워졌고(-1) 세션은 안 나갔다. AuthSession 이 목록에 없는 것이 그 증거다.
    assert await changed(session, base) == {"AuthHandoff": -1}


async def test_expired_handoff_is_rejected(db_client, session):
    """A-12. 만료된 코드는 401. 만료 판정은 DB 시각으로 한다."""
    parent = await seed_member(session)
    # 60초 과거로 둔다. DB 시각과 애플리케이션 시각이 조금 어긋나도 흔들리지 않게.
    await seed_handoff(session, code="handoff-code", parent_id=parent.id, ttl_seconds=-60)

    response = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_handoff"


@pytest.mark.parametrize("bad_bind", ["", "1", "A" * 42])
async def test_malformed_bind_is_rejected_before_lookup(db_client, session, bad_bind):
    """bind 형식 불량은 400 validation_failed (§8-1). 코드 조회까지 가지 않는다."""
    parent = await seed_member(session)
    await seed_handoff(session, code="handoff-code", parent_id=parent.id)

    response = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": bad_bind}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"


# ── A-14 · A-15 · A-19 세션 ───────────────────────────────────────────────────


async def issue_session(db_client, session) -> str:
    """가입까지 태워 토큰 하나를 얻는다."""
    await seed_handoff(session, code="handoff-code")
    consent_code = (
        await db_client.post("/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND})
    ).json()["consent_code"]
    return (
        await db_client.post(
            "/api/v1/auth/kakao/signup",
            json={"consent_code": consent_code, "bind": BIND, "consents": REQUIRED_CONSENTS},
        )
    ).json()["token"]


async def test_logout_invalidates_the_token_immediately(db_client, session):
    """🚨 A-15. 로그아웃 직후 같은 토큰은 401. 지연이 있으면 불투명 토큰을 고른 이유가 사라진다."""
    token = await issue_session(db_client, session)
    headers = {"Authorization": f"Bearer {token}"}
    base = await snapshot(session)

    logout = await db_client.post("/api/v1/auth/logout", headers=headers)
    after = await db_client.post("/api/v1/auth/logout", headers=headers)

    assert logout.status_code == 204
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "unauthenticated"
    assert await changed(session, base) == {"AuthSession": -1}


async def test_logout_leaves_other_sessions_alone(db_client, session):
    """A-15. 그 행 1건만 지운다. 같은 계정의 다른 기기 세션을 끊지 않는다 (§5-3)."""
    token = await issue_session(db_client, session)
    parent_id = await session.scalar(select(Parent.id))
    session.add(
        AuthSession(
            parent_id=parent_id,
            token_hash=hash_token("other-device-token"),
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
    )
    await session.flush()
    base = await snapshot(session)

    await db_client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})

    # 🚨 정확히 1행만 줄었다. 2 였다면 같은 계정의 다른 기기까지 끊은 것이다.
    assert await changed(session, base) == {"AuthSession": -1}


async def test_expired_session_token_is_401(db_client, session):
    """A-14. 만료된 토큰은 401 unauthenticated — 사유를 구분해 알려주지 않는다."""
    parent = await seed_member(session)
    session.add(
        AuthSession(
            parent_id=parent.id,
            token_hash=hash_token("expired-token"),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await session.flush()

    response = await db_client.post(
        "/api/v1/auth/logout", headers={"Authorization": "Bearer expired-token"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_withdrawn_account_is_404_not_401(db_client, session):
    """🚨 A-19. 탈퇴한 계정의 세션은 404 not_found — 만료를 기다리지 않는다.

    401 과 구분하는 유일한 경우다. 나머지(없음·만료·삭제됨)는 전부 401 이다 (§8-1).
    """
    parent = await seed_member(session, deleted=True)
    session.add(
        AuthSession(
            parent_id=parent.id,
            token_hash=hash_token("withdrawn-token"),
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
    )
    await session.flush()

    response = await db_client.post(
        "/api/v1/auth/logout", headers={"Authorization": "Bearer withdrawn-token"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_withdrawn_member_cannot_exchange(db_client, session):
    """A-19. 탈퇴 계정은 교환에서도 세션을 받지 못한다 (§10-1)."""
    parent = await seed_member(session, deleted=True)
    await seed_handoff(session, code="handoff-code", parent_id=parent.id)
    base = await snapshot(session)

    response = await db_client.post(
        "/api/v1/auth/kakao", json={"code": "handoff-code", "bind": BIND}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    # 세션은 나가지 않는다. 코드는 이미 소비된 뒤라 -1 이다.
    assert await changed(session, base) == {"AuthHandoff": -1}


# ── A-18 저장 확인 ────────────────────────────────────────────────────────────


async def test_no_plaintext_secret_is_ever_stored(db_client, session):
    """🚨 A-18. session · auth_handoff 어디에도 원문이 없다. 해시만 저장한다 (§5).

    토큰 · 1회용 코드 · bind 셋 다 본다. 하나라도 원문으로 남으면 DB 가 새는 순간
    남의 계정으로 그대로 로그인된다.
    """
    token = await issue_session(db_client, session)
    await seed_handoff(session, code="another-code")

    # 🚨 이 테스트가 심은 행만 집는다. 전체 조회로 .one() 을 쓰면 수동으로 로그인해 본
    #    개발자의 DB 에서 MultipleResultsFound 로 깨진다 (#45).
    stored_token = await session.scalar(
        select(AuthSession.token_hash).where(AuthSession.token_hash == hash_token(token))
    )
    handoff = (
        await session.execute(
            select(AuthHandoff.code_hash, AuthHandoff.bind_hash).where(
                AuthHandoff.code_hash == hash_token("another-code")
            )
        )
    ).one()

    # 원문으로 저장됐다면 해시로 찾을 때 안 나온다.
    assert stored_token == hash_token(token)
    assert stored_token != token.encode()
    assert handoff.code_hash != b"another-code"
    assert handoff.bind_hash == hash_token(BIND)
    assert handoff.bind_hash != BIND.encode()
