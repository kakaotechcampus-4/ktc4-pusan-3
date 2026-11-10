"""35일이 지난 삭제 이력을 지운다 (#758) — 정리 배치(`cleanup.py`)가 하루 한 번 부른다.

삭제 이력은 백업을 복원할 때 "백업 시각 뒤에 지운 것" 을 다시 지우려고 둔다. 가장 오래된 백업이
28일이므로 그보다 오래된 이력은 쓸 데가 없다. 35일은 28일에 백업 수명 주기의 지연(S3 만료는
하루 넘게 늦을 수 있다)과 정리가 하루쯤 밀리는 여유를 더한 값이다 — 처리방침 1장 ③ 과 같은 값.

🚨 28일보다 짧게 줄이지 않는다. 남은 백업을 복원할 때 필요한 이력이 먼저 사라진다.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.deletion_log.models import DeletionLog

RETENTION = timedelta(days=35)


async def purge_old_deletion_logs(session: AsyncSession, *, now: datetime) -> int:
    """`now` 기준 35일보다 오래된 이력을 지우고 지운 행 수를 돌려준다. 커밋은 호출자가 한다."""
    result = await session.execute(
        delete(DeletionLog).where(DeletionLog.deleted_at < now - RETENTION)
    )
    await session.flush()
    return result.rowcount
