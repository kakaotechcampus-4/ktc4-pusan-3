"""Food · Activity · Growth · Health 의 안전 포트를 `health_safety` 로 구현한다 (#405).

Agent 는 DB 에 직접 닿지 않는다. api 가 이 어댑터를 만들어(`agent_ports.safety_readers()`) 러너와
05 가 entrypoint 로 넘긴다(#460).
포트 모양(`SafetyEntry`)은 agents 가 정한 계약이라 여기서 import 한다 — `DbMemoryStore` 와 같은
예외 경로다(apps/api/CLAUDE.md "agents 가 정한 저장 계약").

- 🚨 Food · Activity · Growth · Health 는 읽는 행이 다르다. 한 쿼리로 합치지 않는다.
  - Food: `active` 만, kind 는 전부. 질환 칸(`chronic_disease` "유당불내증")도
    `chronic_restriction.yaml` 로 메뉴를 막는다 — 빠지면 막히던 메뉴가 통과한다.
  - Activity · Growth: `allergy` · `environmental` 만, status 는 전부. 거르는 것은 호출부다.
    Growth 는 교육 활동만 읽는다. 쿼리는 같아도 포트 계약이 따로라 kind 표를 따로 둔다.
  - Health: `active` 만, kind 는 전부. 응급 카드가 만성질환까지 싣고, 증상 반복 안내가 등록된
    질환과 맞춰 본다. 위험도 · 반응 · 관리 칸까지 옮긴다.
- 🚨 DB 를 못 읽으면 `SafetyLookupError` 다. 빈 목록을 대신 돌려주지 않는다 — 빈 목록이면 Agent 가
  "알레르기 없는 아이" 로 열린다. 그 밖의 예외(버그 · 취소)는 그대로 올린다 (`read_safety_basis`).
- 호출마다 짧은 세션을 연다. run 세션을 도메인 task 들이 같이 타지 않게 (#257).
- `HealthSafety` 에서 옮기는 칸은 포트마다 다르다. Food · Activity 는 kind · label · status 세 칸만,
  Health 는 severity · 반응 · 관리 칸까지다. `notes`(보호자 자유 기술)는 어느 쪽도 싣지 않는다.
"""

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.activity.store.ports import SafetyEntry as ActivitySafetyEntry
from app.agents.common.ports import SafetyLookupError
from app.agents.food.store.ports import SafetyEntry as FoodSafetyEntry
from app.agents.growth.store.ports import SafetyEntry as GrowthSafetyEntry
from app.agents.health.store.ports import HealthSafetyEntry
from app.domains.safety.models import HealthSafety, SafetyKind
from app.domains.safety.repository import list_active_safety, list_safety_of_kinds

log = logging.getLogger(__name__)

# 호출마다 새 세션을 여는 함수. 러너가 `background_session` 으로 만든다
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# Activity 포트가 받는 kind (activity/store/ports.py `SafetyKind`)
ACTIVITY_KINDS = (SafetyKind.ALLERGY, SafetyKind.ENVIRONMENTAL)
# Growth 포트가 받는 kind (growth/store/ports.py `SafetyKind`). 지금은 Activity 와 같다
GROWTH_KINDS = (SafetyKind.ALLERGY, SafetyKind.ENVIRONMENTAL)


class DbSafetyReader:
    """Food `food_safety` · Activity `activity_safety` · Growth `growth_safety` 를 같이 구현한다.

    메서드마다 읽는 행이 다르다 — 각 포트의 계약대로다.
    """

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def food_safety(self, *, child_id: UUID) -> list[FoodSafetyEntry]:
        rows = await self._read(lambda session: list_active_safety(session, child_id=child_id))
        return [
            FoodSafetyEntry(kind=row.kind.value, label=row.label, status=row.status.value)
            for row in rows
        ]

    async def activity_safety(self, *, child_id: UUID) -> list[ActivitySafetyEntry]:
        rows = await self._read(
            lambda session: list_safety_of_kinds(session, child_id=child_id, kinds=ACTIVITY_KINDS)
        )
        return [
            ActivitySafetyEntry(kind=row.kind.value, label=row.label, status=row.status.value)
            for row in rows
        ]

    async def growth_safety(self, *, child_id: UUID) -> list[GrowthSafetyEntry]:
        rows = await self._read(
            lambda session: list_safety_of_kinds(session, child_id=child_id, kinds=GROWTH_KINDS)
        )
        return [
            GrowthSafetyEntry(kind=row.kind.value, label=row.label, status=row.status.value)
            for row in rows
        ]

    async def health_safety(self, *, child_id: UUID) -> list[HealthSafetyEntry]:
        rows = await self._read(lambda session: list_active_safety(session, child_id=child_id))
        return [
            HealthSafetyEntry(
                kind=row.kind.value,
                label=row.label,
                status=row.status.value,
                severity=row.severity.value if row.severity is not None else None,
                reactions=tuple(row.reactions),
                management=row.management,
            )
            for row in rows
        ]

    async def _read(
        self, query: Callable[[AsyncSession], Awaitable[list[HealthSafety]]]
    ) -> list[HealthSafety]:
        try:
            async with self._open() as session:
                return await query(session)
        except (SQLAlchemyError, OSError) as error:
            # 원문 문구에는 접속 정보가 들 수 있어 종류만 남긴다
            log.warning("health_safety 읽기 실패 error=%s", type(error).__name__)
            raise SafetyLookupError("health_safety 를 읽지 못했다") from error
