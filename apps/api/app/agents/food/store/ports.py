"""Food가 읽는 데이터의 계약.

구현체는 DB·외부 API가 연결된 뒤에 붙인다.
구조가 아직 확정되지 않은 것(기관 급식 · 영양성분 · 연령별 기준)은 필요한 필드만 최소로 둔다.
구조가 정해지면 tool 코드는 그대로 두고 어댑터만 추가한다.

| 포트 | 연결 대상 |
| FoodMemoryReader | observation_food · profile_affinity(domain=food) |
| ChildProfileReader | Child_Profile — birth_date 만 (키·몸무게는 수집 범위 결정 전까지 비움) |
| SafetyReader | health_safety — allergy · chronic_disease · dietary_restriction (active) |
| DaycareMenuReader | 기관 급식 데이터 (구조 미확정) |
| NutritionSource | 식품영양성분 DB · 연령별 권장 기준 (출처 미정) |
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol
from uuid import UUID


class SafetyLookupError(Exception):
    """health_safety를 읽지 못함.

    기본값("제한 없음")으로 넘기지 않는다. 식단 추천은 모델을 부르지 않고 끝내고,
    급식 조회는 "알레르기 확인을 못 했어요" 를 명시한다 (루트 §2 · S6).
    """


@dataclass(frozen=True)
class FoodRecord:
    """식단 기억 한 건. 모델에게는 요약만 간다."""

    kind: Literal["observation_food", "profile_affinity"]
    id: str
    subject: str
    observed_on: date | None  # profile_affinity 는 last_observed_on
    # action · amount · reaction · polarity ...
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SafetyEntry:
    """health_safety 한 행에서 필터에 필요한 것만 뽑아 모델에게 전달"""

    kind: Literal["allergy", "chronic_disease", "dietary_restriction"]
    label: str  # 예: 우유
    aliases: tuple[str, ...] = ()  # 매칭 폭을 넓히는 별칭
    restricted_foods: tuple[str, ...] = ()  # 보호자가 management 에 적은 제한 식품 (열린 결정 4)


@dataclass(frozen=True)
class DaycareMenu:
    """하루치 기관 급식. 테이블 구조가 확정되면 변경."""

    day: date
    dishes: tuple[str, ...]
    # 메뉴 → 알레르기 유발 식품. 기관이 준 표시일 뿐 아이 기준 필터는 filter_food_safety 가 한다
    allergens: dict[str, tuple[str, ...]] = field(default_factory=dict)
    total_kcal: float | None = None
    protein_g: float | None = None


@dataclass(frozen=True)
class NutrientFacts:
    food_name: str
    per_100g: dict[str, float]  # TODO: 영양소 → 함량. 단위는 출처를 따른다
    source: str  # 예: 식약처 식품영양성분 DB


class FoodMemoryReader(Protocol):
    async def search(
        self,
        *,
        child_id: UUID,
        keywords: list[str],
        date_from: date | None,
        date_to: date | None,
    ) -> list[FoodRecord]:
        """observation_food + profile_affinity(domain=food)."""
        ...

    async def meal_records(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[FoodRecord]: ...


class ChildProfileReader(Protocol):
    async def birth_date(self, *, child_id: UUID) -> date:
        """식이 단계는 이 값에서 코드가 계산한다. 키·몸무게는 아직 없음."""
        ...


class SafetyReader(Protocol):
    async def food_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        """state=active 인 allergy · chronic_disease · dietary_restriction.

        읽기에 실패하면 SafetyLookupError. 빈 목록을 돌려주지 않는다.
        TODO: 빈 목록이 "알레르기 없음" 인지 "아직 모름" 인지 아직 구분할 자리가 없음.
        구분하지 못하면 모름으로 보고 식단 추천을 실행하지 않는다.
        """
        ...


class DaycareMenuReader(Protocol):
    async def menu(self, *, child_id: UUID, day: date) -> DaycareMenu | None: ...


class NutritionSource(Protocol):
    async def facts(self, *, food_names: list[str]) -> list[NutrientFacts]: ...

    async def reference_ratios(self, *, age_months: int) -> dict[str, float]:
        """연령별 권장 영양소 비중. TODO: 출처 미정. LLM 이 기준을 만들지 않는다."""
        ...
