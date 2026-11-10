"""아이 삭제 — #751.

등록 보호자만 지우는지, 지우면 아이에 묶인 것(함께 보는 보호자 연결 · 공동 기록)이 남지 않고 동의
증빙만 보관 표로 가는지, 커밋 뒤 메모리에 든 그 아이 것까지 비우는지를 본다. 지우는 함수는 탈퇴와
같은 `purge_child` 라 무엇이 CASCADE 로 따라가는지는 탈퇴 테스트(test_withdraw.py)와 같은 기준이다.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.runs import photo_run
from app.domains.child.delete import ChildDeletion, delete_owned_child
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.consent.models import Consent, ConsentAction, ConsentRetention, ConsentScope
from app.domains.consent.repository import grant_account_scope, record_consent
from app.domains.identity.models import Parent
from app.domains.identity.withdraw import withdraw_parent
from app.domains.policy.models import PolicyVersion
from app.domains.schedule.models import SharedPhoto
from app.infra.db.session import engine
from tests.integration.api.conftest import (
    Bearer,
    in_memory,
    issue_bearer,
    link_child,
    stash_in_memory,
)


def _url(cid: uuid.UUID) -> str:
    return f"/api/v1/children/{cid}"


def _key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


async def _policy(session: AsyncSession, scope: ConsentScope) -> uuid.UUID:
    return await session.scalar(select(PolicyVersion.id).where(PolicyVersion.scope == scope))


async def _child_consent(session: AsyncSession, *, actor: uuid.UUID, cid: uuid.UUID) -> None:
    await record_consent(
        session,
        actor_parent_id=actor,
        child_id=cid,
        scope=ConsentScope.CHILD_BASIC,
        action=ConsentAction.GRANTED,
        policy_version_id=await _policy(session, ConsentScope.CHILD_BASIC),
        guardian_attested=True,
    )


async def _retained(session: AsyncSession, **where) -> int:
    stmt = select(func.count()).select_from(ConsentRetention).filter_by(**where)
    return await session.scalar(stmt)


async def test_owner_deletes_child_with_member_links_and_shared_records(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """등록 보호자: 아이 · 함께 보던 보호자 연결 · 공동 기록이 지워지고, 아이 동의만 보관 표로 간다.

    보호자 계정과 계정 동의는 그대로다 — 지우는 것은 아이 하나다.
    """
    headers, owner = bearer
    member_headers, member = await issue_bearer(session, token="member")
    await link_child(session, parent_id=member, child_id=cid)
    await grant_account_scope(
        session,
        parent_id=owner,
        scope=ConsentScope.SERVICE_TERMS,
        policy_version_id=await _policy(session, ConsentScope.SERVICE_TERMS),
    )
    await _child_consent(session, actor=owner, cid=cid)
    photo = SharedPhoto(
        child_id=cid,
        uploader_parent_id=member,
        date=date(2026, 3, 10),
        image_url="https://x.test/p",
    )
    session.add(photo)
    await session.flush()
    photo_id = photo.id

    resp = await db_client.delete(_url(cid), headers={**headers, **_key()})
    # 테스트 세션은 savepoint 위에서 돈다. 라우터가 commit 하지 않았으면 여기서 되돌아간다.
    await session.rollback()

    assert resp.status_code == 204
    assert resp.content == b""
    assert await session.get(Child, cid) is None
    assert await session.scalar(select(ParentChild).where(ParentChild.child_id == cid)) is None
    # session.get 은 세션에 남은 객체를 돌려준다 — CASCADE 는 DB 에서 일어나서 세어 본다
    photos = select(func.count()).select_from(SharedPhoto).where(SharedPhoto.id == photo_id)
    assert await session.scalar(photos) == 0
    assert await session.get(Parent, owner) is not None
    assert await _retained(session, child_ref=cid) == 1
    assert await _retained(session, subject_parent_ref=owner) == 0
    owner_me = await db_client.get("/api/v1/me", headers=headers)
    assert (owner_me.status_code, owner_me.json()["children"]) == (200, [])
    member_me = await db_client.get("/api/v1/me", headers=member_headers)
    assert (member_me.status_code, member_me.json()["children"]) == (200, [])


async def test_member_cannot_delete_child(
    db_client: AsyncClient, session: AsyncSession, cid: uuid.UUID
):
    """함께 보는 보호자는 403 owner_only — 키를 보기 전에 막는다. 아이는 그대로다."""
    member_headers, member = await issue_bearer(session, token="member")
    await link_child(session, parent_id=member, child_id=cid)

    resp = await db_client.delete(_url(cid), headers=member_headers)

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "owner_only"
    assert await session.get(Child, cid) is not None


async def test_unlinked_parent_gets_child_access_denied(
    db_client: AsyncClient, session: AsyncSession, cid: uuid.UUID
):
    """연결되지 않은 보호자는 다른 아이 주소와 같은 403 이다.

    404 를 주면 그 id 의 아이가 있는지 드러난다.
    """
    stranger_headers, _ = await issue_bearer(session, token="stranger")

    resp = await db_client.delete(_url(cid), headers={**stranger_headers, **_key()})

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "child_access_denied"
    assert await session.get(Child, cid) is not None


async def test_idempotency_key_is_required(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """되돌릴 수 없어서 키가 없으면 400 이고 아무것도 지우지 않는다."""
    headers, _ = bearer

    resp = await db_client.delete(_url(cid), headers=headers)

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "idempotency_key_required"
    assert await session.get(Child, cid) is not None


async def test_same_key_after_success_is_blocked_by_access_check(
    db_client: AsyncClient, session: AsyncSession, bearer: Bearer, cid: uuid.UUID
):
    """끝난 뒤 같은 키로 다시 오면 재생보다 아이 접근 확인이 먼저라 403 이다 (idempotency-v1 §3-1).

    다시 지울 아이가 없으니 두 번 지워지는 일은 없다. 화면은 이 403 을 받으면 `/me` 로 확인한다.
    """
    headers, _ = bearer
    key = _key()

    first = await db_client.delete(_url(cid), headers={**headers, **key})
    again = await db_client.delete(_url(cid), headers={**headers, **key})

    assert first.status_code == 204
    assert again.status_code == 403
    assert again.json()["error"]["code"] == "child_access_denied"


async def test_delete_forgets_in_memory_entries_of_the_child(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    clean_in_memory_stores,
):
    """커밋 뒤 그 아이의 메모리 보관함(되묻기 · 05 다시 시도 · 사진 읽은 내용)을 비운다.

    함께 보던 보호자 것도 지운다 — 아이가 없어졌다. 다른 아이 것은 남는다.
    """
    headers, owner = bearer
    _, member = await issue_bearer(session, token="member")
    await link_child(session, parent_id=member, child_id=cid)
    owners = await stash_in_memory(parent_id=owner, child_id=cid)
    members = await stash_in_memory(parent_id=member, child_id=cid)
    # 다른 아이는 실제 행이어야 한다 — 되묻기 맥락은 표(run_hold)라 아이 FK 를 본다 (#877)
    _, someone = await issue_bearer(session, token="someone")
    other_child = await link_child(session, parent_id=someone)
    others = await stash_in_memory(parent_id=someone, child_id=other_child)
    for run_id, parent_id, child_id in (
        ("photo-of-child", member, cid),
        ("photo-of-other", someone, other_child),
    ):
        await photo_run.keep(
            photo_run.open_photo_run(run_id=run_id, parent_id=parent_id, child_id=child_id)
        )

    resp = await db_client.delete(_url(cid), headers={**headers, **_key()})

    assert resp.status_code == 204
    assert await in_memory(owners, parent_id=owner, child_id=cid) == (False, False)
    assert await in_memory(members, parent_id=member, child_id=cid) == (False, False)
    assert await in_memory(others, parent_id=someone, child_id=other_child) == (True, True)
    assert await photo_run.get("photo-of-child", parent_id=member) is None
    assert await photo_run.get("photo-of-other", parent_id=someone) is not None


async def test_delete_keeps_in_memory_entries_when_member_is_refused(
    db_client: AsyncClient,
    session: AsyncSession,
    bearer: Bearer,
    cid: uuid.UUID,
    clean_in_memory_stores,
):
    """막힌 요청은 메모리도 건드리지 않는다 — 아이가 그대로 있다."""
    _, owner = bearer
    member_headers, member = await issue_bearer(session, token="member")
    await link_child(session, parent_id=member, child_id=cid)
    owners = await stash_in_memory(parent_id=owner, child_id=cid)

    resp = await db_client.delete(_url(cid), headers={**member_headers, **_key()})

    assert resp.status_code == 403
    assert await in_memory(owners, parent_id=owner, child_id=cid) == (True, True)


# ── 동시 요청 ─────────────────────────────────────────────────────────────────
# 경합은 한 트랜잭션 안의 테스트 세션으로 못 만든다. 그래서 여기만 실제로 커밋하는 세션을 따로
# 열고, 끝나면 만든 행을 직접 지운다 (test_withdraw.py 와 같은 방식).


@pytest.fixture
async def committed():
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


async def _owner_with_child(factory, made) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as s:
        parent = Parent()
        s.add(parent)
        await s.flush()
        child = Child(owner_parent_id=parent.id, nickname="테스트아이", birth_date=date(2023, 1, 1))
        s.add(child)
        await s.flush()
        link = ParentChild(
            parent_id=parent.id, child_id=child.id, relation=ParentChildRelation.OTHER
        )
        s.add(link)
        await _child_consent(s, actor=parent.id, cid=child.id)
        await s.commit()
    made["parents"].append(parent.id)
    made["children"].append(child.id)
    return parent.id, child.id


async def _delete_committed(factory, *, pid: uuid.UUID, cid: uuid.UUID) -> ChildDeletion:
    async with factory() as s:
        result = await delete_owned_child(s, child_id=cid, parent_id=pid, now=datetime.now(UTC))
        await s.commit()
        return result


async def test_concurrent_delete_retains_evidence_once(committed):
    """같은 아이 삭제가 동시에 둘 돌아도 한 번만 지운다 — 증빙 한 벌, 나중 요청은 GONE."""
    factory, made = committed
    pid, cid = await _owner_with_child(factory, made)

    results = await asyncio.gather(
        _delete_committed(factory, pid=pid, cid=cid), _delete_committed(factory, pid=pid, cid=cid)
    )

    async with factory() as check:
        retained = await _retained(check, child_ref=cid)
    assert sorted(results) == sorted([ChildDeletion.DELETED, ChildDeletion.GONE])
    assert retained == 1


async def test_domain_refuses_parent_who_is_not_the_owner(committed):
    """잠근 뒤에도 등록 보호자인지 다시 본다 — 라우터 확인과 잠금 사이를 믿지 않는다."""
    factory, made = committed
    _, cid = await _owner_with_child(factory, made)
    async with factory() as s:
        other = Parent()
        s.add(other)
        await s.commit()
    made["parents"].append(other.id)

    result = await _delete_committed(factory, pid=other.id, cid=cid)

    async with factory() as check:
        assert await check.get(Child, cid) is not None
    assert result is ChildDeletion.NOT_OWNER


async def test_child_delete_and_owner_withdraw_do_not_deadlock(committed):
    """아이 삭제가 아이를 잠근 채 커밋 전일 때 등록 보호자의 탈퇴가 와도 교착 없이 둘 다 끝난다.

    탈퇴는 보호자 → 아이 순서로 잠근다. 아이 삭제는 아이만 잠그고 보호자 행은 건드리지 않아서,
    탈퇴는 아이 잠금에서 기다렸다가 아이가 이미 없는 것을 보고 보호자만 지운다.
    """
    factory, made = committed
    pid, cid = await _owner_with_child(factory, made)

    async with factory() as deleting:
        result = await delete_owned_child(
            deleting, child_id=cid, parent_id=pid, now=datetime.now(UTC)
        )
        withdrawing = asyncio.create_task(_withdraw_committed(factory, pid))
        await asyncio.sleep(0.3)
        await deleting.commit()

    withdrawn = await asyncio.wait_for(withdrawing, timeout=5)

    async with factory() as check:
        retained = await _retained(check, child_ref=cid)
        assert await check.get(Parent, pid) is None
    assert result is ChildDeletion.DELETED
    assert withdrawn is not None
    assert withdrawn.child_ids == ()
    assert retained == 1


async def _withdraw_committed(factory, pid: uuid.UUID):
    async with factory() as s:
        result = await withdraw_parent(s, parent_id=pid, now=datetime.now(UTC))
        await s.commit()
        return result
