"""이 폴더의 API 테스트가 같이 쓰는 픽스처."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.entrypoint import PendingMemoryContext
from app.agents.memory.schemas.task import WorkType
from app.api.deps.auth import hash_token
from app.api.runs import pending_reply, suggestion_retry
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.identity.models import Parent
from app.domains.identity.repository import create_access, create_session

Bearer = tuple[dict[str, str], uuid.UUID]
"""(Authorization 헤더, parent_id)."""


def cookie_header(response, name: str) -> str | None:
    """응답의 Set-Cookie 중 그 이름의 줄 하나. 없으면 None.

    httpx 쿠키 저장소를 거치지 않고 헤더를 그대로 본다 — Secure 쿠키는 http 테스트 주소로
    다시 실리지 않아, 저장소로는 속성이 맞는지 볼 수 없다.
    """
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    return None


async def seed_login(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    token: str,
    access_expires_at: datetime | None = None,
) -> uuid.UUID:
    """로그인 1건(refresh)과 access 1건을 직접 심고 세션 id 를 돌려준다.

    가입 플로우의 `_issue_session` 과 같은 두 행이다. access 원문은 `token` 이라 Bearer 로
    그대로 쓸 수 있고, refresh 는 이 테스트들이 싣지 않으므로 token 에서 파생한 값을 둔다.
    """
    now = datetime.now(UTC)
    session_id = await create_session(
        session,
        parent_id=parent_id,
        refresh_hash=hash_token(f"refresh:{token}"),
        refresh_expires_at=now + timedelta(days=30),
        absolute_expires_at=now + timedelta(days=90),
    )
    await create_access(
        session,
        session_id=session_id,
        token_hash=hash_token(token),
        expires_at=access_expires_at or now + timedelta(hours=1),
    )
    return session_id


async def issue_bearer(session: AsyncSession, *, token: str = "test-token-api") -> Bearer:
    """세션 행을 직접 심어 토큰 하나를 만들고, 그 보호자 id 를 함께 돌려준다.

    가입 플로우(`test_auth_exchange_db.issue_session`)를 태우지 않는 이유는 여기 테스트들이
    `parent_id` 를 알아야 아이를 연결할 수 있어서다. 루트 `session` 픽스처와 같은 트랜잭션이라
    테스트가 끝나면 함께 롤백된다. 보호자가 둘 필요하면 다른 token 으로 한 번 더 부른다.
    """
    parent = Parent()
    session.add(parent)
    await session.flush()

    await seed_login(session, parent_id=parent.id, token=token)
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


def stash_in_memory(*, parent_id: uuid.UUID, child_id: uuid.UUID) -> str:
    """메모리 보관함 둘(되묻기 · 05 다시 시도)에 그 보호자 · 아이의 항목을 하나씩 넣는다.

    돌려준 run_id 로 `in_memory` 가 남아 있는지 본다 (#561).
    """
    run_id = uuid.uuid4().hex
    owner = {"parent_id": parent_id, "child_id": child_id}
    context = PendingMemoryContext("synthetic", "synthetic question", WorkType.OBSERVE)
    pending_reply.put(run_id=run_id, context=context, **owner)
    suggestion_retry.put_empty(run_id=run_id, agent="food", message="synthetic", **owner)
    return run_id


def in_memory(run_id: str, *, parent_id: uuid.UUID, child_id: uuid.UUID) -> tuple[bool, bool]:
    """(되묻기 보관함에 있나, 05 다시 시도 보관함에 있나)."""
    owner = {"run_id": run_id, "parent_id": parent_id, "child_id": child_id}
    return (
        pending_reply.get(**owner) is not None,
        suggestion_retry.empty_message(agent="food", **owner) is not None,
    )
