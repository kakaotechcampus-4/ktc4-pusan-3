"""아이 표를 읽는 Agent 포트 어댑터 — Growth 의 성장 기록과 Health 의 별명 · 생일.

`GrowthLogReader` 는 `child_growth_log` 로, `ChildBasicReader` 는 `child` 로 구현한다.
Agent 는 DB 에 직접 닿지 않는다. 러너와 05 가 이 어댑터를 만들어 entrypoint 로 넘긴다. 포트 모양
(`GrowthMeasurement` · `ChildBasic`)은 agents 가 정한 계약이라 여기서 import 한다 — `safety/
agent_reader.py` 와 같은 예외 경로다(apps/api/CLAUDE.md "agents 가 정한 저장 계약").

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
from app.agents.health.store.ports import ChildBasic
from app.domains.child.repository import find_child, list_growth_logs

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


class DbChildBasicReader:
    """Health 응급 카드가 읽는 별명 · 생일. 응급 카드를 그릴 때만 읽는다.

    행이 없으면 `LookupError` 다 — 빈 값을 대신 돌려주지 않는다. 별명은 보호자가 적은 원문이라
    예외 메시지에 싣지 않는다.
    """

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def basic(self, *, child_id: UUID) -> ChildBasic:
        async with self._open() as session:
            child = await find_child(session, child_id=child_id)
        if child is None:
            raise LookupError("아이를 찾을 수 없다")
        return ChildBasic(nickname=child.nickname, birth_date=child.birth_date)
