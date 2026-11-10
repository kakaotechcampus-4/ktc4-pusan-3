"""35일 지난 삭제 이력 정리 (#758).

이력은 가장 오래된 백업(28일)을 복원할 때까지 남아 있어야 하고, 그 뒤로는 남기지 않는다
(처리방침 1장 ③). 경계는 35일 — 35일째까지는 남고, 그보다 오래되면 지운다.
NOW 를 과거로 두는 이유는 test_auth_cleanup 과 같다 — 다른 테스트가 남긴 이력(지금 시각)이
섞이지 않게.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domains.deletion_log.models import DeletionLog
from app.workers.deletion_log_purge import purge_old_deletion_logs

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def test_35일보다_오래된_이력만_지운다(session):
    rows = {
        age: DeletionLog(table_name="parent", row_key={"id": f"age-{age}"}, deleted_at=NOW - age)
        for age in (timedelta(days=35, seconds=1), timedelta(days=35), timedelta(days=34))
    }
    session.add_all(rows.values())
    await session.flush()
    ids = {age: row.id for age, row in rows.items()}

    await purge_old_deletion_logs(session, now=NOW)

    left = set(
        await session.scalars(select(DeletionLog.id).where(DeletionLog.id.in_(ids.values())))
    )
    assert left == {ids[timedelta(days=35)], ids[timedelta(days=34)]}
