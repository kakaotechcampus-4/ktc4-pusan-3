"""급식표 입력(사진 · 엑셀 · 한글) → `MealPlanJSON`.

구현체는 입력원별 모듈에 둔다 (image_ocr · xlsx · hwp). 이 패키지는 스키마와 인터페이스만 낸다.
"""

from app.providers.meal_plan.base import MealPlanReader
from app.providers.meal_plan.schema import (
    Meal,
    MealDay,
    MealPlanJSON,
    MealPlanSource,
    MealType,
    MenuItem,
    UnparsedCell,
)

__all__ = [
    "Meal",
    "MealDay",
    "MealPlanJSON",
    "MealPlanReader",
    "MealPlanSource",
    "MealType",
    "MenuItem",
    "UnparsedCell",
]
