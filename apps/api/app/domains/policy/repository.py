"""정책 버전 저장 규칙.

policy_version 은 정책 본문의 정본이라 immutable 하다. 문구가 바뀌면 기존 row 를 고치지
않고 scope 는 같고 version 만 다른 새 row 를 추가한다. 그래서 update/delete 함수를 두지
않는다 — consent 의 append-only 컨벤션과 같은 이유다.
"""

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent.models import ConsentScope
from app.domains.policy.models import PolicyVersion


async def register_version(
    session: AsyncSession,
    *,
    scope: ConsentScope,
    version: str,
    content: str,
    content_hash: str,
    effective_at: datetime,
    ended_at: datetime | None = None,
) -> PolicyVersion:
    """정책 버전 1건을 등록한다. `scope`+`version` 조합은 DB unique 제약으로 유일해야 한다."""
    policy_version = PolicyVersion(
        scope=scope,
        version=version,
        content=content,
        content_hash=content_hash,
        effective_at=effective_at,
        ended_at=ended_at,
    )
    session.add(policy_version)
    await session.flush()
    return policy_version


async def find_active_version(
    session: AsyncSession,
    *,
    scope: ConsentScope,
    version: str,
    now: datetime,
) -> PolicyVersion | None:
    """`scope`+`version` 이 등록돼 있고 `now` 시점에 유효기간 안인지 확인한다.

    현재 시각은 호출자가 넘긴다 — repository 가 직접 `datetime.now()` 를 부르지 않아야
    호출 시점과 무관하게 테스트할 수 있다.
    """
    stmt = select(PolicyVersion).where(
        PolicyVersion.scope == scope,
        PolicyVersion.version == version,
        PolicyVersion.effective_at <= now,
        or_(PolicyVersion.ended_at.is_(None), PolicyVersion.ended_at > now),
    )
    return await session.scalar(stmt)
