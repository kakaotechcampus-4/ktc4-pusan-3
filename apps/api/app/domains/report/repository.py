"""앱 안 신고의 최소 DB 접근 (#487). 커밋은 호출하는 라우터가 한다."""

import uuid

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
