"""급식표 OCR 출력 스키마 — docs/meal-plan/ocr-pipeline.md §3.

LLM 은 이 모양까지만 낸다. `allergen_codes` 는 여기 **없다** (결정 ②: 번호는 규칙이 뽑는다).
모르는 키가 섞여 오면 검증에서 거절된다 (`extra="forbid"`).
"""

import re

from pydantic import BaseModel, ConfigDict, field_validator

_YEAR_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MenuItem(_Strict):
    name: str
    raw: str
    """OCR 원문 그대로. 검수와 알레르기 번호 파싱의 유일한 근거 — 번호·기호를 지우지 않는다."""


class Meal(_Strict):
    meal_type: str
    """lunch / snack_am / snack_pm / breakfast — 실제 급식표 확인 후 enum 으로 잠근다 (§7-2)."""
    items: list[MenuItem]


class MealDay(_Strict):
    day: int
    """일자만. `year_month` 와 합쳐 날짜로 만드는 것은 규칙(코드)이 한다."""
    meals: list[Meal]
    note: str | None = None
    """휴일 등 메뉴가 없는 이유."""

    @field_validator("day")
    @classmethod
    def _day_in_month(cls, v: int) -> int:
        if not 1 <= v <= 31:
            raise ValueError("day 는 1~31 이어야 한다")
        return v


class UnparsedCell(_Strict):
    """못 읽은 칸. 비어 있지 않으면 사람 검수 플래그 (§1 ③) — 지어내지 않는다."""

    day: int | None = None
    meal_type: str | None = None
    raw: str | None = None
    why: str


class MealPlanJSON(_Strict):
    institution_name: str | None
    year_month: str
    days: list[MealDay]
    unparsed: list[UnparsedCell] = []
    notes: str | None = None

    @field_validator("year_month")
    @classmethod
    def _year_month_format(cls, v: str) -> str:
        if not _YEAR_MONTH.match(v):
            raise ValueError("year_month 는 'YYYY-MM' 형식이어야 한다")
        return v
