"""기록 기반 tool 인자. 식단 기억 검색/섭취 기록 분석/반복 메뉴 확인을 담당한다.
날짜는 MealPeriod 라벨로만 받는다. 모델이 날짜를 계산하지 않는다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.food.schemas.common import MealPeriod, MealSlot, Period, ToolArgs


class SearchFoodMemoryArgs(ToolArgs):
    """observation_food + profile_affinity(domain=food) 검색."""

    keywords: Annotated[
        list[str],
        Field(
            default_factory=list,
            max_length=5,
            description="찾을 음식·재료 이름. 예: 딸기, 브로콜리. 비우면 최근 기록과 선호 전체",
        ),
    ]
    period: Annotated[
        MealPeriod | None,
        Field(
            default=None,
            description="기간 라벨. 선호·기피를 볼 때는 비워 기간 제한 없이 찾는다",
        ),
    ]


class AnalyzeMealRecordsArgs(ToolArgs):
    """기간 섭취 기록 요약. 기관 급식과 가정 식사를 한 목록으로 돌려준다."""

    period: Period
    meal_slots: Annotated[
        list[MealSlot],
        Field(default_factory=list, description="특정 끼니만 볼 때. 비우면 전부"),
    ]


class CheckRepeatedMenusArgs(ToolArgs):
    """반복 메뉴·식단 편중 확인. 몇 번부터 반복인지는 코드가 정한다."""

    period: Period
