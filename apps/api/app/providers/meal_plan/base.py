"""급식표 읽기 인터페이스 — docs/meal-plan/meal-plan-pipeline-v1.md §4.

입력원(사진 · 엑셀 · 한글)마다 구현체가 하나씩 있고, 위쪽 코드는 이 시그니처 하나만 안다.
모델이나 라이브러리가 바뀌어도 `MealPlanJSON` 을 내는 책임은 같다.
"""

from typing import Protocol

from app.providers.meal_plan.schema import MealPlanJSON, MealPlanSource


class MealPlanReader(Protocol):
    source: MealPlanSource
    """이 구현체가 다루는 입력원. 결과의 `MealPlanJSON.source` 와 같아야 한다."""

    def read(self, data: bytes, *, mime_type: str) -> MealPlanJSON: ...
