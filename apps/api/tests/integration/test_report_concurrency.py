"""같은 보호자가 신고를 동시에 보내도 하루 상한을 넘기지 않는다 (#487 리뷰).

잠금 없이 세고 넣으면 20건 동시 → 17건 저장(상한 10)까지 넘쳤다. 저장된 건마다 Discord 알림이라
한 사람이 분당 알림 한도를 다 쓴다.

트랜잭션 둘이 실제로 겹쳐야 해서 롤백 픽스처(session)를 쓰지 않는다. 커넥션 둘을 따로 열고,
심은 행은 끝에 직접 지운다 (test_invite_concurrency.py 와 같은 방식).
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.reports import REPORT_DAILY_LIMIT
from app.domains.identity.models import Parent
from app.domains.report.models import ReportCategory
from app.domains.report.repository import count_reports_since, create_report
from app.infra.db.session import engine


async def test_먼저_센_쪽이_커밋할_때까지_다음_세기는_기다린다():
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        parent = Parent()
        setup.add(parent)
        await setup.flush()
        for _ in range(REPORT_DAILY_LIMIT - 1):
            await create_report(
                setup, reporter_parent_id=parent.id, category=ReportCategory.BUG, message="x"
            )
        await setup.commit()

    since = datetime.now(UTC) - timedelta(days=1)
    try:
        async with (
            AsyncSession(engine, expire_on_commit=False) as a,
            AsyncSession(engine, expire_on_commit=False) as b,
        ):
            # a 가 마지막 한 자리를 세고 넣은 뒤 커밋 전. b 는 a 의 잠금에서 기다려야 한다.
            assert await count_reports_since(a, reporter_parent_id=parent.id, since=since) == (
                REPORT_DAILY_LIMIT - 1
            )
            await create_report(
                a, reporter_parent_id=parent.id, category=ReportCategory.BUG, message="x"
            )
            pending_b = asyncio.create_task(
                count_reports_since(b, reporter_parent_id=parent.id, since=since)
            )
            await asyncio.sleep(0.2)
            assert not pending_b.done()

            await a.commit()
            assert await pending_b == REPORT_DAILY_LIMIT  # a 가 넣은 건까지 보고 429 로 간다
            await b.rollback()
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Parent).where(Parent.id == parent.id))  # 신고는 CASCADE
            await cleanup.commit()
