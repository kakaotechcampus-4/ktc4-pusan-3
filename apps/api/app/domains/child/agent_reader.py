"""Growth 의 성장 기록 포트(`GrowthLogReader`)를 `child_growth_log` 로 구현한다.

Agent 는 DB 에 직접 닿지 않는다. 러너와 05 가 이 어댑터를 만들어 entrypoint 로 넘긴다. 포트 모양
(`GrowthMeasurement`)은 agents 가 정한 계약이라 여기서 import 한다 — `safety/agent_reader.py` 와
같은 예외 경로다(apps/api/CLAUDE.md "agents 가 정한 저장 계약").

- 호출마다 짧은 세션을 연다. run 세션을 도메인 task 들이 같이 타지 않게 (#257).
- 키 · 몸무게는 `numeric(4,1)` 그대로 `Decimal` 이다. float 로 바꾸면 차분이 어긋난다.
- 읽기에 실패하면 예외를 그대로 올린다. 빈 목록은 "측정한 적 없음" 이라 대신 돌려주지 않는다.
- 메모(`note`)는 옮기지 않는다 — 보호자 원문이고 Growth 가 쓰지 않는다.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.growth.store.ports import GrowthMeasurement
from app.domains.child.repository import list_growth_logs

# 호출마다 새 세션을 여는 함수. 러너가 `background_session` 으로 만든다
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class DbGrowthLogReader:
    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def measurements(self, *, child_id: UUID) -> list[GrowthMeasurement]:
        async with self._open() as session:
            rows = await list_growth_logs(session, child_id=child_id)
        return [
            GrowthMeasurement(
                id=row.id,
                child_id=row.child_id,
                measured_on=row.measured_on,
                height_cm=row.height_cm,
                weight_kg=row.weight_kg,
            )
            for row in rows
        ]
