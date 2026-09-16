"""대상 삭제 전 동의 증빙 보관 — 노션 정책 정본 §5 · §9

운영 consent 의 대상 FK(subject_parent_id · child_id)는 RESTRICT 다. 증빙을 옮기지 않고
대상을 지우려 하면 DB 가 막는다 — 이 모듈을 거치는 것이 유일한 삭제 경로라는 뜻이다.

순서가 정책이다: 증빙 복사 → 운영 consent 삭제 → 대상 hard delete. 하나라도 실패하면
전부 되돌아가야 하므로 호출자의 트랜잭션 안에서 돌고, 여기서 commit 하지 않는다.

🚨 여기는 consent 만 본다. 아이 삭제의 전체 종속 트리(Observation · Event · 사진 원본과
   썸네일 · 캐시 등)를 정리하는 것은 이 파일의 범위가 아니다. child·parent 행 자체를
   지우면 나머지는 기존 FK CASCADE 가 따라온다.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import Child
from app.domains.consent.models import Consent, ConsentRetention
from app.domains.identity.models import Parent

RETENTION_PERIOD = timedelta(days=365)
"""증빙 보관 기간.

🚨 법에서 정한 기간이 아니라 현재 서비스가 정한 정책이다 (정본 §5). 출시 전에 법적
   근거와 보관 항목을 최종 확인해야 하고, 다른 법령이 더 긴 기간을 요구하면 그쪽이
   우선한다.
"""


async def purge_child(session: AsyncSession, *, child_id: uuid.UUID, now: datetime) -> int:
    """아이의 동의 이력을 보관하고 child 를 hard delete 한다. 보관한 행 수를 돌려준다."""
    retained = await _retain(session, Consent.child_id == child_id, now=now)
    await session.execute(delete(Consent).where(Consent.child_id == child_id))
    await session.execute(delete(Child).where(Child.id == child_id))
    await session.flush()
    return retained


async def purge_parent(session: AsyncSession, *, parent_id: uuid.UUID, now: datetime) -> int:
    """계정의 동의 이력을 보관하고 parent 를 hard delete 한다.

    계정 단위 동의(subject_parent_id)만 옮긴다. 이 보호자가 다른 아이에 대해 누른
    아동 동의는 대상이 그 아이지 이 계정이 아니므로 운영 DB 에 남고, actor_parent_id
    만 SET NULL 로 끊긴다 (정본 §5).
    """
    retained = await _retain(session, Consent.subject_parent_id == parent_id, now=now)
    await session.execute(delete(Consent).where(Consent.subject_parent_id == parent_id))
    await session.execute(delete(Parent).where(Parent.id == parent_id))
    await session.flush()
    return retained


async def _retain(session: AsyncSession, where, *, now: datetime) -> int:
    """조건에 걸리는 consent 를 consent_retention 으로 복사한다.

    retained_at 은 호출자가 넘긴 삭제 시각이다 — 모듈이 직접 시계를 읽으면 같은
    트랜잭션 안에서 행마다 다른 값이 박힐 수 있고 테스트도 시각에 흔들린다.
    """
    rows = (await session.scalars(select(Consent).where(where))).all()
    for row in rows:
        session.add(
            ConsentRetention(
                source_consent_id=row.id,
                actor_ref=row.actor_ref,
                subject_parent_ref=row.subject_parent_id,
                child_ref=row.child_id,
                scope=row.scope,
                action=row.action,
                policy_version_id=row.policy_version_id,
                guardian_attested=row.guardian_attested,
                acted_at=row.acted_at,
                retained_at=now,
                purge_at=now + RETENTION_PERIOD,
            )
        )
    await session.flush()
    return len(rows)
