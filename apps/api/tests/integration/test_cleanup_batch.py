"""만료된 값 정리 배치의 진입점 (#166)

두 정리 함수 각각은 test_auth_cleanup · test_consent_retention 이 본다. 여기서는 진입점이
둘을 다 부르고 **커밋까지 하는지**만 본다 — 두 함수는 flush 만 해서, 커밋이 빠져도 같은 연결
안에서는 지워진 것처럼 보인다.

세션은 바깥 트랜잭션 안의 savepoint 로 열린다 (tests/conftest.py). 그래서 run_cleanup 뒤에
rollback 을 불러도 행이 돌아오지 않으면 커밋된 것이고, 돌아오면 커밋이 빠진 것이다.
NOW 를 과거로 두는 이유는 test_auth_cleanup 과 같다.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domains.consent.models import ConsentAction, ConsentRetention, ConsentScope
from app.domains.identity.models import AuthSession, Parent
from app.domains.policy.models import PolicyVersion
from app.workers.cleanup import run_cleanup

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def test_both_purges_run_and_are_committed(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    expired = AuthSession(
        parent_id=parent.id, token_hash=b"c1".ljust(32, b"-"), expires_at=NOW - timedelta(hours=1)
    )
    alive = AuthSession(
        parent_id=parent.id, token_hash=b"c2".ljust(32, b"-"), expires_at=NOW + timedelta(hours=1)
    )
    policy_version_id = await session.scalar(
        select(PolicyVersion.id).where(PolicyVersion.scope == ConsentScope.SERVICE_TERMS).limit(1)
    )
    retention = ConsentRetention(
        source_consent_id=uuid.uuid4(),
        actor_ref=uuid.uuid4(),
        subject_parent_ref=uuid.uuid4(),
        scope=ConsentScope.SERVICE_TERMS,
        action=ConsentAction.GRANTED,
        policy_version_id=policy_version_id,
        acted_at=NOW - timedelta(days=400),
        retained_at=NOW - timedelta(days=366),
        purge_at=NOW - timedelta(days=1),
    )
    session.add_all([expired, alive, retention])
    await session.flush()
    # 커밋 · 롤백 뒤에는 객체가 만료돼 속성을 읽으면 DB 를 다시 부른다 — id 를 미리 꺼내 둔다
    expired_id, alive_id, retention_id = expired.id, alive.id, retention.id

    result = await run_cleanup(session, now=NOW)
    await session.rollback()

    assert (result.auth.sessions, result.retentions) == (1, 1)
    left_sessions = await session.scalars(
        select(AuthSession.id).where(AuthSession.id.in_([expired_id, alive_id]))
    )
    left_retention = await session.scalar(
        select(ConsentRetention.id).where(ConsentRetention.id == retention_id)
    )
    assert set(left_sessions) == {alive_id}
    assert left_retention is None
