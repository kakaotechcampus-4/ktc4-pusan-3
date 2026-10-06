"""이 폴더의 API 테스트가 같이 쓰는 픽스처."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import hash_token
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.identity.models import Parent
from app.domains.identity.repository import create_session

Bearer = tuple[dict[str, str], uuid.UUID]
"""(Authorization 헤더, parent_id)."""


async def issue_bearer(session: AsyncSession, *, token: str = "test-token-api") -> Bearer:
    """세션 행을 직접 심어 토큰 하나를 만들고, 그 보호자 id 를 함께 돌려준다.

    가입 플로우(`test_auth_exchange_db.issue_session`)를 태우지 않는 이유는 여기 테스트들이
    `parent_id` 를 알아야 아이를 연결할 수 있어서다. 루트 `session` 픽스처와 같은 트랜잭션이라
    테스트가 끝나면 함께 롤백된다. 보호자가 둘 필요하면 다른 token 으로 한 번 더 부른다.
    """
    parent = Parent()
    session.add(parent)
    await session.flush()

    await create_session(
        session,
        parent_id=parent.id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return {"Authorization": f"Bearer {token}"}, parent.id


@pytest.fixture
async def bearer(session: AsyncSession) -> Bearer:
    return await issue_bearer(session)


async def link_child(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    child_id: uuid.UUID | None = None,
    relation: ParentChildRelation = ParentChildRelation.OTHER,
) -> uuid.UUID:
    """보호자를 아이에 잇는다. child_id 를 안 주면 그 보호자를 owner 로 아이를 새로 만든다.

    아이 주소(`/children/{cid}/*`)는 연결된 보호자만 쓴다 (#134 9단계 — 아니면 403).
    별명 · 생일은 가짜 값이다 — 실제 아이 정보를 픽스처에 넣지 않는다 (루트 CLAUDE.md §9).
    """
    if child_id is None:
        child = Child(owner_parent_id=parent_id, nickname="테스트아이", birth_date=date(2023, 1, 1))
        session.add(child)
        await session.flush()
        child_id = child.id
    session.add(ParentChild(parent_id=parent_id, child_id=child_id, relation=relation))
    await session.flush()
    return child_id


@pytest.fixture
async def cid(session: AsyncSession, bearer: Bearer) -> uuid.UUID:
    """bearer 보호자에게 연결된 아이 하나 — 아이 주소를 부르는 테스트가 쓴다."""
    _, parent_id = bearer
    return await link_child(session, parent_id=parent_id)
