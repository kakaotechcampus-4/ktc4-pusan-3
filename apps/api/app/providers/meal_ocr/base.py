"""급식표 OCR 제공자 인터페이스 — docs/meal-plan/ocr-pipeline.md §5.

모델이나 회사가 바뀌어도 위쪽 코드는 이 시그니처 하나만 안다.
"""

from typing import Protocol

from app.providers.meal_ocr.schema import MealPlanJSON


class MealOcrProvider(Protocol):
    def extract(self, image_bytes: bytes, mime_type: str) -> MealPlanJSON: ...
