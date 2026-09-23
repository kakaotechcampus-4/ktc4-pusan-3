# Food tool 스키마가 공유하는 enum · 공통 인자.

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolArgs(BaseModel):
    """모든 Food tool argument의 공통 base. 정의되지 않은 필드는 받지 않는다."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class FoodTaskType(StrEnum):
    """Supervisor가 고르는 Food의 일. health_safety 사전 확인과 tool 묶음이 여기서 갈린다."""

    MEAL_RECOMMENDATION = "meal_recommendation"  # 식단 추천: health_safety 사전 확인 대상
    NUTRIENT_ANALYSIS = "nutrient_analysis"  # 영양소 분석: 식단 기반 비중까지


class FeedingStage(StrEnum):
    """식이 단계. 발화가 아니라 아이의 나이에서 코드가 계산한다.

    값이 app/rules/age.py 의 Band 와 같은 문자열이라 FeedingStage(life_stage(...).big) 으로
    바로 만든다. 경계 월령은 그쪽 _STAGE_BOUNDARIES 한 곳에만 둔다.
    """

    INFANT = "infant"  # 영아기: 분유/수유 · 이유식 · 식재료 도입
    TODDLER = "toddler"  # 유아기: 기관 급식 · 섭취 분석 · 영양성분 · 균형 · 반복 메뉴


class MealSlot(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"


class MealPeriod(StrEnum):
    """조회 기간 라벨. 모델은 라벨만 고르고 날짜 범위는 코드가 계산한다."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_3_DAYS = "last_3_days"
    THIS_WEEK = "this_week"
    LAST_7_DAYS = "last_7_days"
    # TODO: 더 긴 기간이 필요할지에 대한 검토 필요


class EvidenceRef(ToolArgs):
    """근거 Ref 모양. 조회 tool이 돌려준 id를 그대로 쓴다."""

    kind: Annotated[
        Literal["observation_food", "profile_affinity"],
        Field(description="근거가 있는 테이블. 조회 결과의 kind를 그대로 쓴다"),
    ]
    id: Annotated[str, Field(description="조회 결과에 있던 id만. 지어내지 않는다")]


Period = Annotated[
    MealPeriod,
    Field(
        default=MealPeriod.LAST_7_DAYS,
        description="today / yesterday / last_3_days / this_week / last_7_days 중 하나",
    ),
]
