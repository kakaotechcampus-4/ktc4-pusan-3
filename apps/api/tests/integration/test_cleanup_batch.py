"""만료된 값 정리 배치의 진입점 (#166)

두 정리 함수 각각은 test_auth_cleanup · test_consent_retention 이 본다. 여기서는 진입점이
둘을 다 부르고 **커밋까지 하는지**만 본다 — 두 함수는 flush 만 해서, 커밋이 빠져도 같은 연결
안에서는 지워진 것처럼 보인다.

세션은 바깥 트랜잭션 안의 savepoint 로 열린다 (tests/conftest.py). 그래서 run_cleanup 뒤에
rollback 을 불러도 행이 돌아오지 않으면 커밋된 것이고, 돌아오면 커밋이 빠진 것이다.
NOW 를 과거로 두는 이유는 test_auth_cleanup 과 같다.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.llm_usage import UsageRecord
from app.domains.consent.models import (
    ConsentAction,
    ConsentRetention,
    ConsentScope,
    LocationPurpose,
    LocationUseLog,
)
from app.domains.identity.models import AuthSession, Parent
from app.domains.policy.models import PolicyVersion
from app.domains.usage.models import LlmUsage
from app.domains.usage.repository import insert_usage
from app.workers import cleanup as cleanup_module
from app.workers.cleanup import run_cleanup

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def test_both_purges_run_and_are_committed(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    expired = AuthSession(
        parent_id=parent.id,
        refresh_hash=b"c1".ljust(32, b"-"),
        refresh_expires_at=NOW - timedelta(hours=1),
        absolute_expires_at=NOW + timedelta(days=60),
    )
    alive = AuthSession(
        parent_id=parent.id,
        refresh_hash=b"c2".ljust(32, b"-"),
        refresh_expires_at=NOW + timedelta(hours=1),
        absolute_expires_at=NOW + timedelta(days=60),
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


async def test_expired_location_logs_are_purged_and_committed(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    expired = LocationUseLog(
        parent_id=parent.id,
        purpose=LocationPurpose.SUGGESTION,
        used_at=NOW - timedelta(days=200),
    )
    session.add(expired)
    await session.flush()
    expired_id = expired.id

    result = await run_cleanup(session, now=NOW)
    await session.rollback()

    assert result.location_logs == 1
    left = await session.scalar(select(LocationUseLog.id).where(LocationUseLog.id == expired_id))
    assert left is None


async def test_expired_llm_usage_is_purged_and_committed(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    parent_id = parent.id

    def usage(run_id: str, age: timedelta) -> UsageRecord:
        return UsageRecord(
            parent_id=parent_id,
            run_id=run_id,
            source="input",
            role="food",
            provider="gateway.example",
            model="model-a",
            prompt_tokens=10,
            cached_prompt_tokens=0,
            completion_tokens=1,
            reasoning_tokens=0,
            latency_ms=1,
            occurred_at=NOW - age,
        )

    await insert_usage(
        session, [usage("expired", timedelta(days=91)), usage("alive", timedelta(days=89))]
    )

    result = await run_cleanup(session, now=NOW)
    await session.rollback()

    assert result.llm_usage == 1
    # 커밋이 빠졌으면 rollback 이 살아 있는 행까지 같이 되돌린다
    left = await session.scalars(select(LlmUsage.run_id).where(LlmUsage.parent_id == parent_id))
    assert set(left) == {"alive"}


async def test_사용량_정리가_실패해도_처방침이_약속한_정리는_커밋된다(session, monkeypatch, caplog):
    """배포 때 migrate 전이라 표가 없는 경우처럼 사용량 정리만 터질 수 있다 (#710 리뷰).

    그 하나 때문에 만료된 세션 · 동의 증빙 · 위치 기록 정리가 같이 되감기면 처방침의 "하루 한 번" 이
    거짓이 된다. 사용량 정리는 다른 트랜잭션이다.
    """
    parent = Parent()
    session.add(parent)
    await session.flush()
    expired = LocationUseLog(
        parent_id=parent.id,
        purpose=LocationPurpose.SUGGESTION,
        used_at=NOW - timedelta(days=200),
    )
    alive = LocationUseLog(
        parent_id=parent.id,
        purpose=LocationPurpose.SUGGESTION,
        used_at=NOW - timedelta(days=10),
    )
    session.add_all([expired, alive])
    await session.flush()
    expired_id, alive_id = expired.id, alive.id

    async def boom(session, *, now):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(cleanup_module, "purge_expired_llm_usage", boom)

    with caplog.at_level(logging.ERROR, logger="app.workers.cleanup"):
        result = await run_cleanup(session, now=NOW)
    await session.rollback()

    assert (result.location_logs, result.llm_usage) == (1, 0)
    # 살아 있는 행이 남았다 = 위치 정리가 커밋됐다. 커밋 전이었다면 rollback 이 둘 다 되돌린다
    left = await session.scalars(
        select(LocationUseLog.id).where(LocationUseLog.id.in_([expired_id, alive_id]))
    )
    assert set(left) == {alive_id}
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1  # 실패는 알린다
