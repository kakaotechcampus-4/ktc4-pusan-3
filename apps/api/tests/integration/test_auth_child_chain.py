"""세션 키 → 부모 식별 → 아이 접근 체인의 DB 계약.

API 라우터는 Bearer 토큰에서 parent_id를 꺼내고, parent_id로 아이 접근 권한을 확인한다.
이 테스트는 그 체인을 구성하는 repository 함수들이 올바르게 연결되는지 검증한다.
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


async def test_expired_session_still_returns_row(session, two_parents):
    """만료된 세션도 행 자체는 반환한다 — 만료 판정은 호출자(deps/auth.py)가 한다."""
    parent_a, _ = two_parents
    token_hash = _hash("expired-token")
    expired_at = datetime.now(timezone.utc) - timedelta(hours=1)

    await create_session(
        session, parent_id=parent_a.id, token_hash=token_hash, expires_at=expired_at
    )

    row = await find_session_by_token_hash(session, token_hash=token_hash)

    assert row is not None
    assert row.expires_at == expired_at


async def test_unknown_token_returns_none(session, two_parents):
    """등록되지 않은 토큰 해시는 None."""
    row = await find_session_by_token_hash(session, token_hash=_hash("no-such-token"))
    assert row is None


async def test_deleted_parent_session_carries_deleted_at(session, two_parents):
    """탈퇴한 부모의 세션은 parent_deleted_at이 찬다."""
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


async def test_full_chain_session_to_parent_to_child(session, two_parents):
    """세션 토큰 → parent_id → 접근 가능한 아이까지 한 번에 검증한다."""
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
