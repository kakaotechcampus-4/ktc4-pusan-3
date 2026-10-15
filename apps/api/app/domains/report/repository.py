"""앱 안 신고의 최소 DB 접근 (#487). 커밋은 호출하는 라우터가 한다."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.report.models import Report, ReportCategory


async def create_report(
    session: AsyncSession,
    *,
    reporter_parent_id: uuid.UUID,
    category: ReportCategory,
    message: str,
) -> Report:
    """신고 1건을 넣고 id 가 채워진 행을 돌려준다."""
    row = Report(reporter_parent_id=reporter_parent_id, category=category, message=message)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def count_reports_since(
    session: AsyncSession, *, reporter_parent_id: uuid.UUID, since: datetime
) -> int:
    """그 보호자가 since 이후 보낸 신고 수. 하루 상한을 세는 데 쓴다."""
    stmt = select(func.count(Report.id)).where(
        Report.reporter_parent_id == reporter_parent_id, Report.created_at >= since
    )
    return (await session.execute(stmt)).scalar_one()
