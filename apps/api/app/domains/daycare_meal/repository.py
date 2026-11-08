"""어린이집 급식 저장 경로 — 급식표 읽기가 넣고, 화면 · Agent 어댑터가 읽는다.

🚨 이 모듈에는 넣기와 읽기만 있다. 고치기 · 지우기는 Food Agent 의 몫이다
   (docs/agents/food/food_agent_own_table.md §5 — DaycareMealStore.update · delete).
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.daycare_meal.models import DaycareMeal, DaycareOrigin, DaycareSlot


@dataclass(frozen=True)
class NewDaycareMeal:
    """급식표에서 읽어 보호자가 확인한 한 끼. 알레르기 번호는 싣지 않는다."""

    serve_date: date
    meal_slot: DaycareSlot | str
    menu_keys: tuple[str, ...]
    origin: DaycareOrigin = DaycareOrigin.OCR
    ocr_confidence: Decimal | None = None


async def insert_daycare_meals(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    meals: Sequence[NewDaycareMeal],
    source_notice_id: uuid.UUID | None = None,
) -> int:
    """급식표의 끼니들을 넣는다. 넣은 행 수를 돌려준다.

    🚨 이미 있는 날 · 끼니는 건드리지 않는다. Food 가 보호자 말로 고친 내용(대체식 · 먹은 양 ·
       확인 표시)을 다시 올린 급식표가 말없이 덮으면 안 된다. 다시 올린 급식표로 바꾸는 길은
       사진 저장(K 묶음 2/2)에서 정한다.
    """
    if not meals:
        return 0
    stmt = (
        insert(DaycareMeal)
        .values(
            [
                {
                    "child_id": child_id,
                    "serve_date": meal.serve_date,
                    "meal_slot": DaycareSlot(meal.meal_slot),
                    "menu_keys": list(meal.menu_keys),
                    "origin": DaycareOrigin(meal.origin),
                    "ocr_confidence": meal.ocr_confidence,
                    "source_notice_id": source_notice_id,
                }
                for meal in meals
            ]
        )
        .on_conflict_do_nothing(constraint="uq_daycare_meal_child_date_slot")
        .returning(DaycareMeal.id)
    )
    return len((await session.execute(stmt)).all())


async def list_daycare_meals(
    session: AsyncSession, *, child_id: uuid.UUID, date_from: date, date_to: date
) -> list[DaycareMeal]:
    """아이의 급식 — 날짜 범위의 양 끝을 포함하고 (날짜, 끼니) 순.

    Food 포트(DaycareMealStore.rows)의 메모리 구현과 같은 규칙이다.
    """
    stmt = (
        select(DaycareMeal)
        .where(
            DaycareMeal.child_id == child_id,
            DaycareMeal.serve_date >= date_from,
            DaycareMeal.serve_date <= date_to,
        )
        .order_by(DaycareMeal.serve_date, DaycareMeal.meal_slot)
    )
    return list((await session.scalars(stmt)).all())
