"""policy_version — immutable 정책 본문 저장소 (Issue #47, PR B)."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.domains.consent.models import ConsentScope
from app.domains.policy.repository import (
    find_active_version,
    find_active_versions,
    register_version,
)

SEEDED_SCOPES = (
    ConsentScope.SERVICE_TERMS,
    ConsentScope.PRIVACY_ACCOUNT,
    ConsentScope.CHILD_BASIC,
    ConsentScope.CHILD_HEALTH,
)
SEEDED_VERSION = "draft-0"
SEEDED_LABELS = {
    ConsentScope.SERVICE_TERMS: ("서비스 이용약관", None),
    ConsentScope.PRIVACY_ACCOUNT: ("개인정보 수집·이용 (보호자 본인)", None),
    ConsentScope.CHILD_BASIC: (
        "개인정보 수집·이용 (아이 기본정보)",
        "개인정보보호법 제22조의2 (만 14세 미만 아동의 법정대리인 동의)",
    ),
    ConsentScope.CHILD_HEALTH: (
        "민감정보 처리 (아이 건강·알레르기)",
        "개인정보보호법 제23조 (민감정보의 처리, 별도 동의)",
    ),
}
"""scope → (제목, 법적 근거). draft-0 이 화면에 보이던 동안의 값 (apps/web/src/lib/consent.ts)."""
SEEDED_EFFECTIVE_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("scope", SEEDED_SCOPES, ids=lambda s: s.value)
async def test_migration_seeded_placeholder_for_every_scope(session, scope):
    """마이그레이션이 4개 scope 모두 draft-0 placeholder 로 시드했는지 실제 DB로 확인.

    draft-1 이 등록된 scope 의 draft-0 은 끝난 것으로 기록되지만 행은 그대로 남는다
    (immutable). 끝났는지는 test_policy_draft_1.py 가 본다.
    """
    row = await find_active_version(
        session, scope=scope, version=SEEDED_VERSION, now=SEEDED_EFFECTIVE_AT
    )
    assert row is not None
    assert row.content.startswith("TODO:")
    assert (row.label, row.legal_basis) == SEEDED_LABELS[scope], "그때 화면의 제목 · 근거가 남는다"
    assert row.content_hash == hashlib.sha256(row.content.encode("utf-8")).hexdigest()


async def test_duplicate_scope_version_is_rejected(session):
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await register_version(
                session,
                scope=ConsentScope.SERVICE_TERMS,
                version=SEEDED_VERSION,
                label="test",
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
        label="test",
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


async def test_find_active_versions_returns_one_current_row_per_scope(session):
    """GET /policies 의 재료 (#91). scope 마다 지금 유효한 행 하나 — 여럿이면 가장 늦게 시작한 것.

    이미 끝났거나 아직 시작하지 않은 버전은 후보에서 빠진다. 마이그레이션이 등록한 약관과
    섞이지 않게, 아무 약관도 등록되지 않는 quality_improve 한 scope 안에서 본다.
    """
    scope = ConsentScope.QUALITY_IMPROVE
    now = datetime(2026, 9, 29, tzinfo=UTC)
    windows = {
        "older": (datetime(2026, 6, 1, tzinfo=UTC), None),
        "newer": (datetime(2026, 7, 1, tzinfo=UTC), None),
        "ended": (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)),
        "future": (now + timedelta(days=1), None),
    }
    for version, (effective_at, ended_at) in windows.items():
        await register_version(
            session,
            scope=scope,
            version=version,
            label="test",
            content=version,
            content_hash=f"{version}-hash",
            effective_at=effective_at,
            ended_at=ended_at,
        )

    rows = await find_active_versions(session, now=now)
    by_scope = {row.scope: row.version for row in rows}

    assert len(rows) == len(by_scope), "scope 마다 한 행만"
    assert by_scope[scope] == "newer"


async def test_find_active_versions_skips_scope_without_current_row(session):
    """등록된 행이 없는 scope 는 결과에 없다 (#91)."""
    rows = await find_active_versions(session, now=datetime(2026, 9, 29, tzinfo=UTC))

    assert ConsentScope.QUALITY_IMPROVE not in {row.scope for row in rows}


async def test_policy_version_repository_has_no_mutation_functions():
    """policy_version 은 immutable — repository 에 update/delete 함수를 두지 않는다."""
    import app.domains.policy.repository as repo

    assert not hasattr(repo, "update_version")
    assert not hasattr(repo, "delete_version")
