"""탈퇴 — #379.

등록 보호자는 아이까지, 함께 보는 보호자는 자기만 지워지는지와 그 경계(동의 증빙 · 세션 ·
공동 기록), 그리고 커밋 앞뒤의 실패 처리를 본다. 카카오 서버는 부르지 않는다.
"""

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps.auth import hash_token
from app.api.v1.routers import auth as auth_router
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.consent import retention
from app.domains.consent.models import Consent, ConsentAction, ConsentRetention, ConsentScope
from app.domains.consent.repository import grant_account_scope, record_consent
from app.domains.identity import withdraw as withdraw_module
from app.domains.identity.models import AuthProvider, AuthSession, Parent
from app.domains.identity.repository import (
    consume_handoff,
    create_handoff,
    create_identity,
)
from app.domains.identity.withdraw import withdraw_parent
from app.domains.policy.models import PolicyVersion
from app.domains.schedule.models import SharedPhoto
from app.infra.db.session import engine
from app.integrations.kakao.client import KakaoApiError
from tests.integration.api.conftest import Bearer, issue_bearer, link_child, seed_login

URL = "/api/v1/auth/withdraw"
ACK = {"acknowledged_immediate_deletion": True}
KAKAO_ID = "1234567890"


class FakeAdmin:
    """카카오 연결 끊기를 기록만 하는 대역. 실패를 시키려면 fail 에 예외를 넣는다."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.unlinked: list[str] = []

    async def unlink(self, user_id: str) -> None:
        self.unlinked.append(user_id)
        if self.fail is not None:
            raise self.fail


@pytest.fixture
def kakao(monkeypatch) -> FakeAdmin:
    fake = FakeAdmin()

    @asynccontextmanager
    async def fake_admin():
        yield fake

    monkeypatch.setattr(auth_router, "kakao_admin", fake_admin)
    return fake


async def _policy(session: AsyncSession, scope: ConsentScope) -> uuid.UUID:
    return await session.scalar(select(PolicyVersion.id).where(PolicyVersion.scope == scope))


async def _signed_up(session: AsyncSession, pid: uuid.UUID) -> None:
    """가입한 보호자처럼 — 카카오 회원번호와 계정 동의 1건."""
    await create_identity(
        session, parent_id=pid, provider=AuthProvider.KAKAO, provider_user_id=f"{KAKAO_ID}-{pid}"
    )
    await grant_account_scope(
        session,
        parent_id=pid,
        scope=ConsentScope.SERVICE_TERMS,
        policy_version_id=await _policy(session, ConsentScope.SERVICE_TERMS),
    )


async def _retained(session: AsyncSession, **where) -> int:
    stmt = select(func.count()).select_from(ConsentRetention).filter_by(**where)
    return await session.scalar(stmt)


async def test_owner_withdraw_deletes_child_and_cuts_members(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, kakao
):
    """등록 보호자: 아이 · 함께 보던 보호자의 연결까지 지우고, 다른 기기의 세션도 끊는다.

    다른 기기는 세션째 지워져 401 이다 — 로그인 문서 A-19.
    """
    headers, owner = bearer
    member_headers, member = await issue_bearer(session, token="member")
    await link_child(session, parent_id=member, child_id=cid)
    await seed_login(session, parent_id=owner, token="owner-other-device")
    await _signed_up(session, owner)
    await _signed_up(session, member)
    await record_consent(
        session,
        actor_parent_id=owner,
        child_id=cid,
        scope=ConsentScope.CHILD_BASIC,
        action=ConsentAction.GRANTED,
        policy_version_id=await _policy(session, ConsentScope.CHILD_BASIC),
        guardian_attested=True,
    )

    resp = await db_client.post(URL, json=ACK, headers=headers)
    # 테스트 세션은 savepoint 위에서 돈다. 라우터가 commit 하지 않았으면 여기서 되돌아간다.
    await session.rollback()

    assert resp.status_code == 200
    assert datetime.fromisoformat(resp.json()["deleted_at"])
    assert await session.get(Parent, owner) is None
    assert await session.get(Child, cid) is None
    assert await session.scalar(select(ParentChild).where(ParentChild.parent_id == member)) is None
    other_device = {"Authorization": "Bearer owner-other-device"}
    assert (await db_client.get("/api/v1/me", headers=other_device)).status_code == 401
    member_me = await db_client.get("/api/v1/me", headers=member_headers)
    assert (member_me.status_code, member_me.json()["children"]) == (200, [])
    assert await _retained(session, subject_parent_ref=owner) == 1
    assert await _retained(session, child_ref=cid) == 1
    assert await _retained(session, subject_parent_ref=member) == 0
    assert kakao.unlinked == [f"{KAKAO_ID}-{owner}"]


async def test_member_withdraw_keeps_child_and_shared_records(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID, kakao
):
    """함께 보는 보호자: 자기만 지워진다.

    아이 · 등록 보호자는 그대로이고, 공동 기록의 적은 사람만 비워진다.
    """
    owner_headers, owner = bearer
    member_headers, member = await issue_bearer(session, token="member")
    await link_child(session, parent_id=member, child_id=cid)
    await _signed_up(session, member)
    photo = SharedPhoto(
        child_id=cid,
        uploader_parent_id=member,
        date=date(2026, 3, 10),
        image_url="https://x.test/p",
    )
    session.add(photo)
    await session.flush()

    resp = await db_client.post(URL, json=ACK, headers=member_headers)
    await session.rollback()

    assert resp.status_code == 200
    assert await session.get(Parent, member) is None
    assert await session.get(Child, cid) is not None
    await session.refresh(photo)
    assert photo.uploader_parent_id is None
    owner_me = await db_client.get("/api/v1/me", headers=owner_headers)
    assert [c["child_id"] for c in owner_me.json()["children"]] == [str(cid)]
    assert await _retained(session, subject_parent_ref=member) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"acknowledged_immediate_deletion": False},
        {"acknowledged_immediate_deletion": "true"},
        {"acknowledged_immediate_deletion": 1},
    ],
)
async def test_ack_must_be_json_true(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, kakao, body: dict
):
    """화면 버그로 잘못 나간 요청을 한 번 더 막는다 — JSON true 가 아니면 아무것도 지우지 않는다."""
    headers, pid = bearer

    resp = await db_client.post(URL, json=body, headers=headers)

    assert (resp.status_code, resp.json()["error"]["code"]) == (400, "validation_failed")
    assert await session.get(Parent, pid) is not None


async def test_kakao_failure_after_commit_still_withdraws(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    kakao,
    caplog: pytest.LogCaptureFixture,
):
    """커밋 뒤 카카오가 실패해도 탈퇴는 200 이고 DB 는 지워진 채다.

    회원번호는 로그에 남기지 않는다.
    """
    headers, pid = bearer
    await _signed_up(session, pid)
    kakao.fail = KakaoApiError("unlink: 카카오가 500 로 답했다")

    with caplog.at_level(logging.WARNING, logger=auth_router.__name__):
        resp = await db_client.post(URL, json=ACK, headers=headers)
    await session.rollback()

    assert resp.status_code == 200
    assert await session.get(Parent, pid) is None
    [failed] = [r for r in caplog.records if "카카오 연결 끊기 실패" in r.getMessage()]
    assert failed.levelno == logging.ERROR  # 알림이 가야 잘못 넣은 키를 안다
    assert KAKAO_ID not in caplog.text


async def test_owner_withdraw_rolls_back_child_when_parent_purge_fails(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    kakao,
    monkeypatch,
):
    """아이를 지운 뒤 보호자 삭제가 실패하면 아이 삭제까지 되돌아간다 — 계정만 남는 일이 없다."""
    headers, pid = bearer

    async def broken(*_, **__):
        raise RuntimeError("보호자 삭제 실패")

    monkeypatch.setattr(withdraw_module, "purge_parent", broken)
    # 준비한 행을 savepoint 밖으로 내보낸다. 아니면 아래 rollback 이 준비한 아이까지 되돌려
    # "라우터가 중간에 커밋했는가" 를 가를 수 없다.
    await session.commit()

    with contextlib.suppress(RuntimeError):
        await db_client.post(URL, json=ACK, headers=headers)
    await session.rollback()

    assert await session.get(Child, cid) is not None
    assert await session.get(Parent, pid) is not None
    assert kakao.unlinked == []


# ── 동시 요청 ─────────────────────────────────────────────────────────────────
# 경합은 한 트랜잭션 안의 테스트 세션으로 못 만든다. 그래서 여기만 실제로 커밋하는 세션을 따로
# 열고, 끝나면 만든 행을 직접 지운다.


@pytest.fixture
async def committed():
    """실제로 커밋하는 세션 공장. 테스트가 만든 보호자 · 아이 id 를 넣어 두면 끝나고 지운다."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    made: dict[str, list[uuid.UUID]] = {"parents": [], "children": []}
    yield factory, made
    pids, cids = made["parents"], made["children"]
    async with factory() as cleanup:
        await cleanup.execute(
            delete(ConsentRetention).where(
                ConsentRetention.subject_parent_ref.in_(pids) | ConsentRetention.child_ref.in_(cids)
            )
        )
        await cleanup.execute(
            delete(Consent).where(Consent.subject_parent_id.in_(pids) | Consent.child_id.in_(cids))
        )
        await cleanup.execute(delete(Child).where(Child.id.in_(cids)))
        await cleanup.execute(delete(Parent).where(Parent.id.in_(pids)))
        await cleanup.commit()


async def _new_parent(factory, made) -> uuid.UUID:
    async with factory() as s:
        parent = Parent()
        s.add(parent)
        await s.flush()
        await _signed_up(s, parent.id)
        await s.commit()
    made["parents"].append(parent.id)
    return parent.id


async def _withdraw_committed(factory, pid: uuid.UUID):
    async with factory() as s:
        result = await withdraw_parent(s, parent_id=pid, now=datetime.now(UTC))
        await s.commit()
        return result


async def test_concurrent_withdraw_retains_evidence_once(committed):
    """같은 보호자의 탈퇴가 동시에 돌아도 한 번만 처리된다 — 증빙 한 벌, 나중 요청은 None."""
    factory, made = committed
    pid = await _new_parent(factory, made)

    results = await asyncio.gather(
        _withdraw_committed(factory, pid), _withdraw_committed(factory, pid)
    )

    async with factory() as check:
        retained = await _retained(check, subject_parent_ref=pid)
    assert sorted(r is None for r in results) == [False, True]
    assert retained == 1


async def test_child_consent_saved_mid_withdraw_is_not_lost(committed, monkeypatch):
    """보관 목록을 읽은 직후 함께 보는 보호자가 아이 동의를 남겨도, 보관 없이 지워지지 않는다.

    보관을 읽은 그 순간에 다른 세션이 동의를 남기게 끼워 넣는다. 아이 행이 잠겨 있으면 그 저장은
    기다리다 시간 초과로 실패하고, 잠겨 있지 않으면 커밋된 뒤 보관 없이 함께 지워진다.
    """
    factory, made = committed
    owner = await _new_parent(factory, made)
    member = await _new_parent(factory, made)
    async with factory() as s:
        child = Child(owner_parent_id=owner, nickname="테스트아이", birth_date=date(2023, 1, 1))
        s.add(child)
        await s.flush()
        s.add_all(
            [
                ParentChild(parent_id=owner, child_id=child.id, relation=ParentChildRelation.OTHER),
                ParentChild(
                    parent_id=member, child_id=child.id, relation=ParentChildRelation.OTHER
                ),
            ]
        )
        await s.commit()
    made["children"].append(child.id)

    member_saved = False
    original = retention._retain

    async def retain_then_member_consents(session, where, *, now):
        nonlocal member_saved
        kept = await original(session, where, now=now)
        if not member_saved:
            async with factory() as other:
                await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
                try:
                    await record_consent(
                        other,
                        actor_parent_id=member,
                        child_id=child.id,
                        scope=ConsentScope.CHILD_HEALTH,
                        action=ConsentAction.GRANTED,
                        policy_version_id=await _policy(other, ConsentScope.CHILD_HEALTH),
                        guardian_attested=True,
                    )
                    await other.commit()
                    member_saved = True
                except DBAPIError:
                    await other.rollback()
        return kept

    monkeypatch.setattr(retention, "_retain", retain_then_member_consents)

    await _withdraw_committed(factory, owner)

    async with factory() as check:
        retained = await _retained(check, child_ref=child.id)
    assert retained == (1 if member_saved else 0)


async def test_login_exchange_and_withdraw_do_not_deadlock(committed):
    """기존 회원의 로그인 교환(대기표 → 보호자)과 탈퇴가 겹쳐도 교착 없이 둘 다 끝난다.

    교환이 대기표를 지운 채 커밋 전일 때 탈퇴가 시작된다. 탈퇴가 보호자부터 잡으면, 교환은
    세션을 만들며 보호자를 기다리고 탈퇴는 대기표를 기다려 DB 가 한쪽을 끊는다.
    """
    factory, made = committed
    pid = await _new_parent(factory, made)
    code_hash = hash_token("handoff-code")
    async with factory() as s:
        await create_handoff(
            s,
            provider=AuthProvider.KAKAO,
            code_hash=code_hash,
            bind_hash=hash_token("bind"),
            parent_id=pid,
            provider_user_id=None,
            expires_at=datetime.now(UTC) + timedelta(minutes=2),
        )
        await s.commit()

    async with factory() as login:
        consumed = await consume_handoff(login, code_hash=code_hash, provider=AuthProvider.KAKAO)
        withdrawing = asyncio.create_task(_withdraw_committed(factory, pid))
        await asyncio.sleep(0.3)
        await seed_login(login, parent_id=consumed.parent_id, token="new-session")
        await login.commit()

    withdrawn = await asyncio.wait_for(withdrawing, timeout=5)

    async with factory() as check:
        sessions = await check.scalar(
            select(func.count()).select_from(AuthSession).where(AuthSession.parent_id == pid)
        )
    assert withdrawn is not None
    assert sessions == 0
