"""policy_version — immutable 정책 본문 저장소 (Issue #47, PR B)."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.domains.consent.models import ConsentScope
from app.domains.policy.repository import find_active_version, register_version

SEEDED_SCOPES = (
    ConsentScope.SERVICE_TERMS,
    ConsentScope.PRIVACY_ACCOUNT,
    ConsentScope.CHILD_BASIC,
    ConsentScope.CHILD_HEALTH,
)
SEEDED_VERSION = "draft-0"
SEEDED_EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("scope", SEEDED_SCOPES, ids=lambda s: s.value)
async def test_migration_seeded_placeholder_for_every_scope(session, scope):
    """마이그레이션이 4개 scope 모두 draft-0 placeholder 로 시드했는지 실제 DB로 확인."""
    row = await find_active_version(
        session, scope=scope, version=SEEDED_VERSION, now=SEEDED_EFFECTIVE_AT
    )
    assert row is not None
    assert row.content.startswith("TODO:")
    assert row.content_hash == hashlib.sha256(row.content.encode("utf-8")).hexdigest()
    assert row.ended_at is None


async def test_duplicate_scope_version_is_rejected(session):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await register_version(
                session,
                scope=ConsentScope.SERVICE_TERMS,
                version=SEEDED_VERSION,
                content="duplicate",
                content_hash="deadbeef",
                effective_at=SEEDED_EFFECTIVE_AT,
            )


async def test_find_active_version_respects_effective_window(session):
    scope = ConsentScope.QUALITY_IMPROVE
    effective_at = datetime(2026, 6, 1, tzinfo=UTC)
    ended_at = datetime(2026, 9, 1, tzinfo=UTC)
    await register_version(
        session,
        scope=scope,
        version="v1",
        content="synthetic",
        content_hash="synthetic-hash",
        effective_at=effective_at,
        ended_at=ended_at,
    )

    assert (
        await find_active_version(
            session, scope=scope, version="v1", now=effective_at - timedelta(days=1)
        )
        is None
    )
    assert (
        await find_active_version(
            session, scope=scope, version="v1", now=effective_at + timedelta(days=1)
        )
        is not None
    )
    assert await find_active_version(session, scope=scope, version="v1", now=ended_at) is None
    assert (
        await find_active_version(session, scope=scope, version="nonexistent", now=effective_at)
        is None
    )


async def test_policy_version_repository_has_no_mutation_functions():
    """policy_version 은 immutable — repository 에 update/delete 함수를 두지 않는다."""
    import app.domains.policy.repository as repo

    assert not hasattr(repo, "update_version")
    assert not hasattr(repo, "delete_version")
