"""이 폴더의 API 테스트가 같이 쓰는 픽스처."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import hash_token
from app.domains.identity.models import Parent
from app.domains.identity.repository import create_session


@pytest.fixture
async def bearer(session: AsyncSession) -> tuple[dict[str, str], uuid.UUID]:
    """세션 행을 직접 심어 토큰 하나를 만들고, 그 보호자 id 를 함께 돌려준다.

    가입 플로우(`test_auth_exchange_db.issue_session`)를 태우지 않는 이유는 여기 테스트들이
    `parent_id` 를 알아야 아이를 연결할 수 있어서다. 루트 `session` 픽스처와 같은 트랜잭션이라
    테스트가 끝나면 함께 롤백된다.
    """
    parent = Parent()
    session.add(parent)
    await session.flush()

    token = "test-token-api"
    await create_session(
        session,
        parent_id=parent.id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    return {"Authorization": f"Bearer {token}"}, parent.id
