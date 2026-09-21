"""보관 기간이 끝난 동의 증빙을 파기하는 배치 — 노션 정책 정본 §5 · §9

증빙은 영구 보관이 아니다. purge_at 이 지나면 지운다 — 보관 자체가 목적이 있는 예외라
기간이 끝나면 예외도 끝난다 (정본 §1-5 "삭제가 기본이고 보존은 예외").

스케줄러는 아직 붙이지 않았다. 이 함수를 언제 어떻게 부를지(주기·운영 진입점)는 배포
구성이 정해진 뒤에 결정한다.
"""

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent.models import ConsentRetention


async def purge_expired_retentions(session: AsyncSession, *, now: datetime) -> int:
    """purge_at 이 지난 증빙을 지우고 지운 행 수를 돌려준다.

    현재 시각은 호출자가 넘긴다 — 배치가 직접 시계를 읽으면 테스트가 실제 시각에
    묶인다 (policy repository 의 find_active_version 과 같은 이유).
    """
    result = await session.execute(delete(ConsentRetention).where(ConsentRetention.purge_at <= now))
    await session.flush()
    return result.rowcount
