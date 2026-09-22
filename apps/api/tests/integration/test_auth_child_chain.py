"""세션 키 → 부모 식별 → 아이 접근 — 인증·인가 체인의 DB 계약.

API 라우터의 인증 흐름:
  Bearer token → hash → find_session_by_token_hash → parent_id
                                                      ↓
                                        find_accessible_child(child_id, parent_id)

이 테스트는 체인의 각 단계가 올바르게 동작하고, 단계 간 연결이 끊기지 않는지 검증한다.
인덱스 의존: ix_session_token_hash (unique), parent_child_parent_id_child_id_key (unique composite).
"""

import hashlib
from datetime import date, datetime, timedelta, timezone

import pytest

from app.domains.child.models import ParentChildRelation
from app.domains.child.repository import (
    create_child,
    find_accessible_child,
    list_children_for_parent,
)
from app.domains.identity.models import Parent
from app.domains.identity.repository import (
    SessionRow,
    create_session,
    find_session_by_token_hash,
)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@pytest.fixture
async def two_parents(session):
    """보호자 2명. 각자 아이를 등록하기 전 상태."""
    parent_a, parent_b = Parent(), Parent()
    session.add_all([parent_a, parent_b])
    await session.flush()
    return parent_a, parent_b


# -- 1. 세션 → 부모 식별 --


async def test_valid_session_resolves_to_parent(session, two_parents):
    """유효한 세션 토큰 해시로 parent_id를 얻는다."""
    parent_a, _ = two_parents
    token_hash = _hash("test-token-a")
    expires = datetime.now(timezone.utc) + timedelta(hours=12)

    await create_session(session, parent_id=parent_a.id, token_hash=token_hash, expires_at=expires)

    row = await find_session_by_token_hash(session, token_hash=token_hash)

    assert row is not None
    assert isinstance(row, SessionRow)
    assert row.parent_id == parent_a.id
    assert row.parent_deleted_at is None


async def test_expired_session_returns_row_with_past_expires_at(session, two_parents):
    """만료된 세션도 행은 반환한다. 만료 판정은 repository가 아니라 deps/auth.py가 한다."""
    parent_a, _ = two_parents
    token_hash = _hash("expired-token")
    expired_at = datetime.now(timezone.utc) - timedelta(hours=1)

    await create_session(
        session, parent_id=parent_a.id, token_hash=token_hash, expires_at=expired_at
    )

    row = await find_session_by_token_hash(session, token_hash=token_hash)

    assert row is not None
    assert row.expires_at == expired_at


async def test_unknown_token_hash_returns_none(session, two_parents):
    """DB에 없는 토큰 해시 → None. deps/auth.py는 이를 401로 변환한다."""
    row = await find_session_by_token_hash(session, token_hash=_hash("no-such-token"))
    assert row is None


async def test_deleted_parent_session_carries_deleted_at(session, two_parents):
    """탈퇴한 부모의 세션은 parent_deleted_at이 채워진다. deps/auth.py는 이를 404로 변환한다."""
    parent_a, _ = two_parents
    deleted_time = datetime.now(timezone.utc)
    parent_a.deleted_at = deleted_time
    await session.flush()

    token_hash = _hash("deleted-parent-token")
    await create_session(
        session,
        parent_id=parent_a.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
    )

    row = await find_session_by_token_hash(session, token_hash=token_hash)

    assert row is not None
    assert row.parent_id == parent_a.id
    assert row.parent_deleted_at is not None


# -- 2. 부모 → 아이 접근 --


async def test_parent_sees_own_children_only(session, two_parents):
    """각 부모는 자기 아이만 조회할 수 있다."""
    parent_a, parent_b = two_parents
    child_a = await create_child(
        session,
        owner_parent_id=parent_a.id,
        nickname="아이A",
        birth_date=date(2023, 3, 1),
        relation=ParentChildRelation.MOTHER,
    )
    child_b = await create_child(
        session,
        owner_parent_id=parent_b.id,
        nickname="아이B",
        birth_date=date(2024, 1, 1),
        relation=ParentChildRelation.FATHER,
    )

    a_children = await list_children_for_parent(session, parent_id=parent_a.id)
    b_children = await list_children_for_parent(session, parent_id=parent_b.id)

    assert [c.id for c in a_children] == [child_a.id]
    assert [c.id for c in b_children] == [child_b.id]


async def test_find_accessible_child_blocks_cross_parent_access(session, two_parents):
    """다른 부모의 아이에 접근하면 None이다."""
    parent_a, parent_b = two_parents
    child_a = await create_child(
        session,
        owner_parent_id=parent_a.id,
        nickname="아이A",
        birth_date=date(2023, 3, 1),
        relation=ParentChildRelation.MOTHER,
    )

    found = await find_accessible_child(session, child_id=child_a.id, parent_id=parent_a.id)
    assert found is not None
    assert await find_accessible_child(session, child_id=child_a.id, parent_id=parent_b.id) is None


# -- 3. 전체 체인: 세션 → 부모 → 아이 --


async def test_full_chain_token_hash_to_parent_to_accessible_child(session, two_parents):
    """전체 체인: token_hash → SessionRow.parent_id → find_accessible_child 성공/차단."""
    parent_a, parent_b = two_parents

    # 세션 생성
    token_hash = _hash("chain-test-token")
    await create_session(
        session,
        parent_id=parent_a.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
    )

    # 아이 등록
    child = await create_child(
        session,
        owner_parent_id=parent_a.id,
        nickname="체인테스트",
        birth_date=date(2023, 6, 15),
        relation=ParentChildRelation.MOTHER,
    )

    # 체인 실행: 토큰 해시 → parent_id → 아이 접근
    session_row = await find_session_by_token_hash(session, token_hash=token_hash)
    assert session_row is not None

    resolved_child = await find_accessible_child(
        session, child_id=child.id, parent_id=session_row.parent_id
    )
    assert resolved_child is not None
    assert resolved_child.id == child.id

    # 다른 부모의 세션으로는 같은 아이에 접근 불가
    other_token_hash = _hash("other-parent-token")
    await create_session(
        session,
        parent_id=parent_b.id,
        token_hash=other_token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
    )
    other_session_row = await find_session_by_token_hash(session, token_hash=other_token_hash)
    assert other_session_row is not None

    blocked = await find_accessible_child(
        session, child_id=child.id, parent_id=other_session_row.parent_id
    )
    assert blocked is None
