"""Food 의 급식 포트(`DaycareMealStore`)를 `daycare_meal` 표로 구현한다.

Agent 는 DB 에 직접 닿지 않는다. 러너 · 05 가 이 어댑터를 만들어(`agent_ports.food_stores()`)
entrypoint 로 넘긴다. 행 모양(`DaycareMealRow`)은 agents 가 정한 계약이라 여기서 import 한다 —
`domains/food/agent_reader.py` 와 같은 예외 경로다(apps/api CLAUDE.md "저장 계약").

- 🚨 INSERT 가 없다. Food 는 급식을 새로 만들 권한이 없다 — 행을 만드는 것은 급식표 읽기
  (`repository.insert_daycare_meals`)뿐이고, 여기는 있는 행을 읽고 고치고 지울 뿐이다.
- 🚨 쓰기는 `child_id` 를 WHERE 에 건다. id 하나만으로 쓰면 잘못된 id 가 남의 아이 행을
  고치거나 지울 수 있다. `update` 는 없는 행 · 다른 아이 행이면 `KeyError`, `delete` 는 다른 아이
  id 가 하나라도 섞이면 `KeyError` 이고 아무것도 지우지 않는다(메모리 구현과 같다).
- `update` 가 바꾸는 칸은 `menu_keys` · `amount_factor` · `amount_known` ·
  `caregiver_checked` 넷뿐이다.
  날짜 · 끼니를 바꾸면 UNIQUE 에 걸리고, 출처를 바꾸면 `center_standard` 표시가 틀어진다.
- 🚨 읽기에 실패하면 `DaycareLookupError` 다. 빈 목록 · False 를 대신 돌려주지 않는다 — 급식이 있는
  아이에게 "등록된 급식이 없어요" 가 나간다. Food 는 이 예외만 잡아 급식을 "못 읽음" 으로 다룬다.
  쓰기 실패는 그대로 올린다(못 썼는데 "바꿨어요" 를 낼 수 없다).
- 호출마다 짧은 세션을 연다. run 세션을 도메인 task 들이 같이 타지 않게 (#257). 쓰기는 호출 하나가
  짧은 트랜잭션 하나다(포트 계약).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import date
from decimal import Decimal
from typing import TypeVar

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.food.store.ports import DaycareLookupError, DaycareMealRow
from app.domains.daycare_meal import repository
from app.domains.daycare_meal.models import DaycareMeal

log = logging.getLogger(__name__)

# 호출마다 새 세션을 여는 함수. 러너가 `background_session` 으로 만든다
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_T = TypeVar("_T")


def daycare_row(model: DaycareMeal) -> DaycareMealRow:
    return DaycareMealRow(
        id=model.id,
        child_id=model.child_id,
        serve_date=model.serve_date,
        meal_slot=model.meal_slot.value,  # type: ignore[arg-type] — CHECK 가 값 범위를 막는다
        menu_keys=tuple(model.menu_keys),
        amount_factor=float(model.amount_factor),
        amount_known=model.amount_known,
        caregiver_checked=model.caregiver_checked,
        origin=model.origin.value,  # type: ignore[arg-type] — CHECK 가 값 범위를 막는다
    )


class DbDaycareMeals:
    """`DaycareMealStore`. 넣기(INSERT)는 없다 — 급식표 읽기의 몫이다."""

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def _read(self, query: Callable[[AsyncSession], Awaitable[_T]]) -> _T:
        try:
            async with self._open() as session:
                return await query(session)
        except (SQLAlchemyError, OSError) as error:
            # 원문 문구에는 접속 정보가 들 수 있어 종류만 남긴다
            log.warning("daycare_meal 읽기 실패 error=%s", type(error).__name__)
            raise DaycareLookupError("daycare_meal 을 읽지 못했다") from error

    async def rows(
        self, *, child_id: uuid.UUID, date_from: date, date_to: date
    ) -> list[DaycareMealRow]:
        found = await self._read(
            lambda session: repository.list_daycare_meals(
                session, child_id=child_id, date_from=date_from, date_to=date_to
            )
        )
        return [daycare_row(model) for model in found]

    async def has_rows(self, *, child_id: uuid.UUID) -> bool:
        return await self._read(
            lambda session: repository.has_daycare_meals(session, child_id=child_id)
        )

    async def update(self, row: DaycareMealRow) -> None:
        stmt = (
            update(DaycareMeal)
            .where(DaycareMeal.id == row.id, DaycareMeal.child_id == row.child_id)
            .values(
                menu_keys=list(row.menu_keys),
                amount_factor=Decimal(str(round(row.amount_factor, 2))),
                amount_known=row.amount_known,
                caregiver_checked=row.caregiver_checked,
            )
            .returning(DaycareMeal.id)
        )
        async with self._open() as session:
            if (await session.execute(stmt)).first() is None:
                # commit 전이라 아무것도 남지 않는다. id 는 로그에 싣지 않는다 — 메시지에도
                raise KeyError("고칠 수 있는 daycare_meal 행이 없다")
            await session.commit()

    async def delete(self, *, child_id: uuid.UUID, row_ids: tuple[uuid.UUID, ...]) -> None:
        if not row_ids:
            return
        async with self._open() as session:
            foreign = await session.scalar(
                select(DaycareMeal.id)
                .where(DaycareMeal.id.in_(row_ids), DaycareMeal.child_id != child_id)
                .limit(1)
            )
            if foreign is not None:
                raise KeyError("다른 아이의 daycare_meal 행은 지울 수 없다")
            await session.execute(
                delete(DaycareMeal).where(
                    DaycareMeal.id.in_(row_ids), DaycareMeal.child_id == child_id
                )
            )
            await session.commit()
