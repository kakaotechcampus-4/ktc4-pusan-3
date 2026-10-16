"""앱 안 신고의 최소 DB 접근 (#487). 커밋은 호출하는 라우터가 한다."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import Parent
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


async def lock_reporter(session: AsyncSession, *, reporter_parent_id: uuid.UUID) -> bool:
    """신고를 세고 넣기 전에 보호자 행을 잠근다. 보호자가 이미 없으면 False.

    🚨 같은 보호자의 세기 · 넣기가 커밋까지 한 줄로 선다. 안 잠그면 동시에 온 요청이 모두 같은
       수를 보고 넣어 상한이 크게 넘친다(20건 동시 → 17건 · #487 리뷰).
       NO KEY UPDATE 라 다른 테이블이 이 보호자를 FK 로 가리키며 넣는 것은 막지 않는다.
       탈퇴(`withdraw_parent`)도 이 행을 먼저 잡으니 순서가 같아 교착이 없다.
       탈퇴가 먼저 잡았으면 커밋을 기다렸다가 행이 없는 것을 보고 False — 그대로 넣으면 FK 위반
       500 · 가짜 장애 알림이 된다.
    잠금을 세기와 떼어 둔 이유 — 세기만 필요한 곳(관리 화면 등)이 모르고 탈퇴를 막지 않게.
    """
    locked = await session.scalar(
        select(Parent.id).where(Parent.id == reporter_parent_id).with_for_update(key_share=True)
    )
    return locked is not None


async def count_reports_since(
    session: AsyncSession, *, reporter_parent_id: uuid.UUID, since: datetime
) -> int:
    """그 보호자가 since 이후 보낸 신고 수. 하루 상한을 세는 데 쓴다 — 먼저 lock_reporter."""
    stmt = select(func.count(Report.id)).where(
        Report.reporter_parent_id == reporter_parent_id, Report.created_at >= since
    )
    return (await session.execute(stmt)).scalar_one()
