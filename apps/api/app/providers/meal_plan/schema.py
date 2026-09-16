"""급식표 구조화 결과 — docs/meal-plan/meal-plan-pipeline-v1.md §3.

입력이 사진(OCR) · 엑셀 · 한글 어느 쪽이든 읽기 코드는 이 모양까지만 낸다.
`allergen_codes` 는 여기 **없다** — 번호는 규칙(app/rules/allergen.py)이 `raw` 에서 뽑는다.
모르는 키가 섞여 오면 검증에서 거절된다 (`extra="forbid"`).
"""

import calendar
import re
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_YEAR_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class MealPlanSource(StrEnum):
    """어디서 온 급식표인가. 읽기 코드가 채우고 모델은 채우지 않는다."""

    IMAGE = "image"  # 보호자가 찍은 사진 → OCR. 못 읽은 칸이 생길 수 있어 검수 대상
    XLSX = "xlsx"  # 급식관리지원센터 배포 엑셀. 표를 그대로 읽는다
    HWP = "hwp"  # 센터 배포 한글. 표 안 텍스트를 읽는다


class MealType(StrEnum):
    """급식표의 끼니 칸. 실측한 센터 급식표 기준 — 오전·오후 간식이 갈린다."""

    BREAKFAST = "breakfast"
    SNACK_AM = "snack_am"
    LUNCH = "lunch"
    SNACK_PM = "snack_pm"
    DINNER = "dinner"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MenuItem(_Strict):
    name: str
    raw: str
    """원문 그대로. 검수와 알레르기 번호 파싱의 유일한 근거 — 번호·기호를 지우지 않는다."""


class Meal(_Strict):
    meal_type: MealType
    items: list[MenuItem]


class MealDay(_Strict):
    day: int
    """일자만. `year_month` 와 합쳐 날짜로 만드는 것은 코드가 한다 (`MealPlanJSON.date_of`)."""
    meals: list[Meal]
    note: str | None = None
    """휴일 등 메뉴가 없는 이유."""

    @field_validator("day")
    @classmethod
    def _day_in_range(cls, v: int) -> int:
        if not 1 <= v <= 31:
            raise ValueError("day 는 1~31 이어야 한다")
        return v


class UnparsedCell(_Strict):
    """못 읽은 칸. 비어 있지 않으면 사람 검수 대상 — 지어내지 않는다."""

    day: int | None = None
    meal_type: MealType | None = None
    raw: str | None = None
    why: str


class MealPlanJSON(_Strict):
    """급식표 1장 = 한 달치."""

    source: MealPlanSource
    year_month: str
    institution_name: str | None = None
    """급식표에 찍혀 있으면 그대로. 저장 기준(아이/기관)이 정해지기 전이라 선택값이다."""
    days: list[MealDay]
    unparsed: list[UnparsedCell] = []
    notes: str | None = None

    @field_validator("year_month")
    @classmethod
    def _year_month_format(cls, v: str) -> str:
        if not _YEAR_MONTH.match(v):
            raise ValueError("year_month 는 'YYYY-MM' 형식이어야 한다")
        return v

    @model_validator(mode="after")
    def _days_fit_the_month(self) -> "MealPlanJSON":
        """2월 30일처럼 그 달에 없는 날과 같은 날이 두 번 나오는 것을 거절한다."""
        last = calendar.monthrange(*self._year_and_month())[1]
        seen: set[int] = set()
        for entry in self.days:
            if entry.day > last:
                raise ValueError(f"day {entry.day} 는 {self.year_month} 에 없다 (말일 {last})")
            if entry.day in seen:
                raise ValueError(f"day {entry.day} 가 두 번 나온다")
            seen.add(entry.day)
        return self

    def date_of(self, day: int) -> date:
        """`year_month` + `day` → 날짜. 날짜 계산은 모델이 아니라 여기서 한다."""
        year, month = self._year_and_month()
        return date(year, month, day)

    def _year_and_month(self) -> tuple[int, int]:
        year, month = self.year_month.split("-")
        return int(year), int(month)
