"""기관 급식 조회 인자. 날짜는 라벨로만 받는다."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from app.agents.food.schemas.common import ToolArgs


class MenuDay(StrEnum):
    YESTERDAY = "yesterday"
    TODAY = "today"
    TOMORROW = "tomorrow"
    # TODO: 날짜를 직접 받는 기능은 나중에 common.py의 MealPeriod와
    # 함께 검토하며 적용을 고려


class DaycareMenuArgs(ToolArgs):
    day: Annotated[
        MenuDay,
        Field(default=MenuDay.TODAY, description="yesterday / today / tomorrow 중 하나"),
    ]
