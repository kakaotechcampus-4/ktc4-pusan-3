"""동의 행위자·대상 분리와 증빙 보관 (Issue #47, PR C) — 노션 정책 정본 §5 · §9

여기서 지키는 것 넷.
    ① 행위자가 탈퇴해도 아동 동의는 살아 있다 (actor 만 NULL)
    ② 현재 상태는 대상+scope 의 마지막 action 이다
    ③ 증빙을 옮기지 않은 대상 삭제는 DB 가 막는다
    ④ 증빙 복사·삭제는 한 트랜잭션이라 중간 실패가 남지 않는다
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.domains.child.models import Child
from app.domains.consent.models import Consent, ConsentAction, ConsentRetention, ConsentScope
from app.domains.consent.repository import (
    grant_account_scope,
    latest_action,
    missing_account_scopes,
    record_consent,
)
from app.domains.consent.retention import RETENTION_PERIOD, purge_child, purge_parent
from app.domains.identity.models import Parent
from app.domains.policy.models import PolicyVersion
from app.workers.consent_purge import purge_expired_retentions

NOW = datetime(2026, 9, 16, tzinfo=UTC)


@pytest.fixture
async def seeded(session):
    """보호자 둘(A·B)과 아이 하나. A 가 Owner 로 시작한다."""
    a, b = Parent(), Parent()
    session.add_all([a, b])
    await session.flush()
    child = Child(owner_parent_id=a.id, nickname="test child", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return a, b, child


async def policy_id(session, scope: ConsentScope) -> object:
    """PR B 마이그레이션이 시드한 placeholder 버전의 id."""
    return await session.scalar(select(PolicyVersion.id).where(PolicyVersion.scope == scope))


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


# ── ① 행위자 탈퇴 · ② 최신 action ────────────────────────────────────────────


async def test_owner_transfer_and_actor_withdrawal_keep_child_consent(session, seeded):
    """🚨 완료 기준의 상태 전이 시나리오 전체.

    A 동의 → Owner B 이관 → A 탈퇴 → granted 유지 → B withdrawn → B granted.
    행위자가 사라진다고 아이의 동의가 무효화되면 안 된다 (정본 §5).
    """
    a, b, child = seeded
    version = await policy_id(session, ConsentScope.CHILD_HEALTH)
    await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.GRANTED,
        policy_version_id=version,
    )

    child.owner_parent_id = b.id
    await session.flush()
    # A 는 Owner 가 아니게 됐으므로 hard delete 가 가능하다 (PR A 의 writer FK + 여기의
    # actor SET NULL).
    await session.execute(delete(Parent).where(Parent.id == a.id))

    state = await latest_action(session, scope=ConsentScope.CHILD_HEALTH, child_id=child.id)
    assert state is ConsentAction.GRANTED
    row = await session.scalar(select(Consent).where(Consent.child_id == child.id))
    assert row.actor_parent_id is None, "탈퇴한 행위자 참조는 끊긴다"
    assert row.actor_ref == a.id, "🚨 누가 눌렀는지는 증빙으로 남는다"

    await record_consent(
        session,
        actor_parent_id=b.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.WITHDRAWN,
        policy_version_id=version,
    )
    assert (
        await latest_action(session, scope=ConsentScope.CHILD_HEALTH, child_id=child.id)
        is ConsentAction.WITHDRAWN
    )

    await record_consent(
        session,
        actor_parent_id=b.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.GRANTED,
        policy_version_id=version,
    )
    assert (
        await latest_action(session, scope=ConsentScope.CHILD_HEALTH, child_id=child.id)
        is ConsentAction.GRANTED
    )
    assert await count(session, Consent) == 3, "철회·재동의는 기존 행을 고치지 않고 쌓인다"


async def test_repeating_the_same_action_is_idempotent(session, seeded):
    """같은 action 을 다시 보내면 행이 늘지 않는다 — 재시도가 이력을 부풀리지 않는다."""
    a, _, child = seeded
    version = await policy_id(session, ConsentScope.CHILD_BASIC)
    first = await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_BASIC,
        action=ConsentAction.GRANTED,
        policy_version_id=version,
    )
    again = await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_BASIC,
        action=ConsentAction.GRANTED,
        policy_version_id=version,
    )

    assert first is not None
    assert again is None
    assert await count(session, Consent) == 1


async def test_account_consent_counts_for_the_subject_not_the_actor(session, seeded):
    """계정 동의의 주인은 subject 다. actor 가 남이어도 그 계정의 동의로 센다."""
    a, b, _ = seeded
    for scope in (ConsentScope.SERVICE_TERMS, ConsentScope.PRIVACY_ACCOUNT):
        await record_consent(
            session,
            actor_parent_id=b.id,
            subject_parent_id=a.id,
            scope=scope,
            action=ConsentAction.GRANTED,
            policy_version_id=await policy_id(session, scope),
        )

    assert await missing_account_scopes(session, parent_id=a.id) == []
    assert len(await missing_account_scopes(session, parent_id=b.id)) == 2


async def test_grant_account_scope_fills_actor_from_the_subject(session, seeded):
    """가입 경로는 본인이 본인에게 동의한다 — actor_ref 를 호출자가 지정하지 않는다."""
    a, _, _ = seeded
    consent = await grant_account_scope(
        session,
        parent_id=a.id,
        scope=ConsentScope.SERVICE_TERMS,
        policy_version_id=await policy_id(session, ConsentScope.SERVICE_TERMS),
    )

    assert consent.actor_parent_id == a.id
    assert consent.actor_ref == a.id
    assert consent.subject_parent_id == a.id
    assert consent.child_id is None


@pytest.mark.parametrize(
    "scope,kwargs",
    [
        (ConsentScope.SERVICE_TERMS, {"child_id": True}),
        (ConsentScope.CHILD_HEALTH, {"subject_parent_id": True}),
    ],
    ids=["account-scope-with-child", "child-scope-with-parent"],
)
async def test_target_kind_must_match_scope(session, seeded, scope, kwargs):
    """🚨 scope 와 대상 종류가 어긋나면 DB CHECK 가 막는다."""
    a, _, child = seeded
    target = {"child_id": child.id} if "child_id" in kwargs else {"subject_parent_id": a.id}

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(
                Consent(
                    actor_parent_id=a.id,
                    actor_ref=a.id,
                    scope=scope,
                    action=ConsentAction.GRANTED,
                    policy_version_id=await policy_id(session, scope),
                    **target,
                )
            )
            await session.flush()


async def test_consent_target_must_be_exactly_one(session, seeded):
    """대상을 둘 다 채우거나 둘 다 비우면 저장되지 않는다."""
    a, _, child = seeded
    with pytest.raises(ValueError, match="정확히 하나"):
        await latest_action(
            session,
            scope=ConsentScope.CHILD_BASIC,
            subject_parent_id=a.id,
            child_id=child.id,
        )


# ── ③ 증빙 없는 삭제 차단 ─────────────────────────────────────────────────────


async def test_deleting_a_consent_subject_without_retention_is_blocked(session, seeded):
    """🚨 증빙을 옮기지 않은 채 대상을 지우려 하면 DB 가 막는다.

    이 RESTRICT 가 없으면 삭제 서비스를 거치지 않는 경로 하나가 증빙을 조용히 날린다.
    """
    a, _, child = seeded
    await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_BASIC,
        action=ConsentAction.GRANTED,
        policy_version_id=await policy_id(session, ConsentScope.CHILD_BASIC),
    )

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(Child).where(Child.id == child.id))


async def test_policy_version_cannot_be_deleted_while_consent_refers_to_it(session, seeded):
    """증빙이 가리키는 정책 본문은 지울 수 없다 — 재현할 수 있어야 한다 (정본 §5)."""
    a, _, child = seeded
    version = await policy_id(session, ConsentScope.CHILD_BASIC)
    await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_BASIC,
        action=ConsentAction.GRANTED,
        policy_version_id=version,
    )

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(PolicyVersion).where(PolicyVersion.id == version))


# ── ④ 증빙 보관 트랜잭션 ──────────────────────────────────────────────────────


async def test_purge_child_retains_evidence_then_deletes(session, seeded):
    """아이 삭제 — 동의 이력이 보관 테이블로 옮겨지고 운영 consent 에서는 사라진다."""
    a, _, child = seeded
    version = await policy_id(session, ConsentScope.CHILD_HEALTH)
    await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.GRANTED,
        policy_version_id=version,
    )
    await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.WITHDRAWN,
        policy_version_id=version,
    )

    retained = await purge_child(session, child_id=child.id, now=NOW)

    assert retained == 2, "granted·withdrawn 이력 전체를 남긴다"
    assert await count(session, Consent) == 0
    assert await count(session, ConsentRetention) == 2
    assert await session.scalar(select(Child.id).where(Child.id == child.id)) is None
    row = await session.scalar(select(ConsentRetention).limit(1))
    assert row.child_ref == child.id, "FK 는 없지만 어느 아이였는지는 남는다"
    assert row.actor_ref == a.id
    assert row.retained_at == NOW
    assert row.purge_at == NOW + RETENTION_PERIOD


async def test_policy_version_survives_while_only_evidence_refers_to_it(session, seeded):
    """🚨 운영 consent 가 사라진 뒤에도 증빙이 가리키는 본문은 지울 수 없다.

    "증빙이 남아 있는 동안 참조 정책 본문을 재현할 수 있다" 는 consent 가 아니라
    consent_retention 쪽 이야기다 — 대상이 지워지면 consent 행은 없어지므로, 그때부터는
    보관 테이블의 FK 만이 본문을 붙잡는다.
    """
    a, _, child = seeded
    version = await policy_id(session, ConsentScope.CHILD_HEALTH)
    await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.GRANTED,
        policy_version_id=version,
    )
    await purge_child(session, child_id=child.id, now=NOW)
    assert await count(session, Consent) == 0, "막는 주체가 consent 가 아님을 분명히 한다"
    assert await count(session, ConsentRetention) == 1

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(delete(PolicyVersion).where(PolicyVersion.id == version))

    content = await session.scalar(select(PolicyVersion.content).where(PolicyVersion.id == version))
    assert content is not None, "보관 기간 동안 동의 당시 본문을 그대로 읽을 수 있다"


async def test_purge_parent_keeps_other_subjects_consent(session, seeded):
    """계정 삭제는 그 계정 대상 동의만 옮긴다. 아이 동의는 운영 DB 에 남는다."""
    a, b, child = seeded
    child.owner_parent_id = b.id
    await session.flush()
    await grant_account_scope(
        session,
        parent_id=a.id,
        scope=ConsentScope.SERVICE_TERMS,
        policy_version_id=await policy_id(session, ConsentScope.SERVICE_TERMS),
    )
    await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_BASIC,
        action=ConsentAction.GRANTED,
        policy_version_id=await policy_id(session, ConsentScope.CHILD_BASIC),
    )

    retained = await purge_parent(session, parent_id=a.id, now=NOW)

    assert retained == 1, "계정 단위 동의 1건만 보관 대상이다"
    remaining = await session.scalar(select(Consent))
    assert remaining.child_id == child.id, "아이 동의는 남는다"
    assert remaining.actor_parent_id is None, "행위자 참조만 끊긴다"
    assert remaining.actor_ref == a.id


async def test_failed_target_delete_rolls_back_the_retention(session, seeded):
    """🚨 증빙만 남고 대상이 살아남는 상태가 생기지 않는다.

    Owner 로 남아 있는 보호자는 child.owner_parent_id 의 RESTRICT 때문에 지워지지
    않는다. 그때 앞서 복사한 증빙도 함께 되돌아가야 한다.
    """
    a, _, _ = seeded  # a 는 아이의 Owner 라 삭제가 막힌다
    await grant_account_scope(
        session,
        parent_id=a.id,
        scope=ConsentScope.SERVICE_TERMS,
        policy_version_id=await policy_id(session, ConsentScope.SERVICE_TERMS),
    )

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await purge_parent(session, parent_id=a.id, now=NOW)

    assert await count(session, ConsentRetention) == 0, "증빙 복사가 되돌아갔다"
    assert await count(session, Consent) == 1, "운영 동의도 그대로다"
    assert await session.scalar(select(Parent.id).where(Parent.id == a.id)) == a.id


# ── 파기 배치 ─────────────────────────────────────────────────────────────────


async def test_purge_batch_removes_only_expired_evidence(session, seeded):
    """purge_at 이 지난 것만 지운다. 아직 안 지난 증빙은 남는다."""
    a, _, child = seeded
    await record_consent(
        session,
        actor_parent_id=a.id,
        child_id=child.id,
        scope=ConsentScope.CHILD_BASIC,
        action=ConsentAction.GRANTED,
        policy_version_id=await policy_id(session, ConsentScope.CHILD_BASIC),
    )
    await purge_child(session, child_id=child.id, now=NOW)

    not_yet = NOW + RETENTION_PERIOD - timedelta(days=1)
    assert await purge_expired_retentions(session, now=not_yet) == 0
    assert await count(session, ConsentRetention) == 1

    expired = await purge_expired_retentions(session, now=NOW + RETENTION_PERIOD)
    assert expired == 1
    assert await count(session, ConsentRetention) == 0
