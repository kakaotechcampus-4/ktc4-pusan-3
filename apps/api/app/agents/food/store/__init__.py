"""Food Agent와 DB·외부 데이터 사이 통로.
구현체는 ORM·외부 API 가 연결된 뒤 같은 Protocol로 붙인다.
Food는 쓰기 포트를 갖지 않는다. 기록은 Memory가, 추천 저장은 app/api가 한다.
"""

from app.agents.food.store.ports import (
    ChildProfileReader,
    DaycareMenu,
    DaycareMenuReader,
    FoodMemoryReader,
    FoodRecord,
    NutrientFacts,
    NutritionSource,
    SafetyEntry,
    SafetyLookupError,
    SafetyReader,
)

__all__ = [
    "ChildProfileReader",
    "DaycareMenu",
    "DaycareMenuReader",
    "FoodMemoryReader",
    "FoodRecord",
    "NutrientFacts",
    "NutritionSource",
    "SafetyEntry",
    "SafetyLookupError",
    "SafetyReader",
]
