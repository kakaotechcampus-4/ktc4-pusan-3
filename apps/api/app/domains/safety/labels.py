"""05 사전검사용 health_safety 행 — 모든 kind · 모든 status (#249).

Agent 의 안전 조회(`agent_reader` · #412)는 active 만 읽지만, 사전검사는 알레르기(active) ·
괜찮았던 식품(none) · 내린 기록(retracted)을 갈라야 한다(내린 기록은 "모름" 과 같다). 판정은
agents(`allergen_states`)가 한다 — 여기는 행을 옮겨 담기만 한다.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.safety.models import HealthSafety


async def list_safety_labels(
    session: AsyncSession, *, child_id: uuid.UUID
) -> list[tuple[str, str, str]]:
    stmt = select(HealthSafety.kind, HealthSafety.label, HealthSafety.status).where(
        HealthSafety.child_id == child_id
    )
    return [
        (kind.value, label, status.value) for kind, label, status in await session.execute(stmt)
    ]
