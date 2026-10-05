"""Food 포트의 인메모리 구현. 테스트·eval이 DB 없이 FoodPorts를 주입할 때 쓴다.

`daycare`(`DaycareMealRow`) 는 행 자체가 `child_id` 를 가지므로 `rows`·`has_rows`·
`update`·`delete` 모두 그 값으로 아이 경계를 지킨다. `child_id` 가 없는 공용 데이터
포트(catalog·docs·menu_source)는 한 인스턴스가 한 시나리오 몫의 데이터만 쥔다고 보고
`child_id` 인자를 받지 않는다. `child_id` 없이는 행을 구분할 수 없는 포트
(profile·consent·memory·suggestions)는 딕셔너리로 나눈다.
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from app.agents.common.evidence import AffinityRow
from app.agents.food.store.ports import (
    DaycareMealRow,
    FoodDocRow,
    FoodObservation,
    FoodPorts,
    Measurement,
    MenuCatalogRow,
    MenuSourceError,
    NutritionFacts,
    SafetyEntry,
    SafetyLookupError,
)
from app.rules.age import Stage


class InMemoryProfile:
    def __init__(self, birth_dates: dict[UUID, date] | None = None) -> None:
        self._birth_dates = dict(birth_dates or {})

    async def birth_date(self, *, child_id: UUID) -> date:
        return self._birth_dates[child_id]


class InMemoryConsent:
    def __init__(self, granted: dict[UUID, bool] | None = None, *, default: bool = True) -> None:
        self._granted = dict(granted or {})
        self._default = default

    async def child_health_granted(self, *, child_id: UUID) -> bool:
        return self._granted.get(child_id, self._default)


class InMemorySafety:
    """`fail=True` 면 조회 자체가 실패한 것으로 본다 — 빈 목록과 구분해야 한다 (루트 §2)."""

    def __init__(self, entries: Sequence[SafetyEntry] = (), *, fail: bool = False) -> None:
        self._entries = list(entries)
        self._fail = fail

    async def food_safety(self, *, child_id: UUID) -> list[SafetyEntry]:
        if self._fail:
            raise SafetyLookupError("health_safety 조회 실패 (테스트 주입)")
        return list(self._entries)


class InMemoryGrowth:
    def __init__(self, measurements: dict[UUID, Measurement] | None = None) -> None:
        self._measurements = dict(measurements or {})

    async def latest(self, *, child_id: UUID) -> Measurement | None:
        return self._measurements.get(child_id)


class InMemoryFoodMemory:
    def __init__(
        self,
        affinities: dict[UUID, Sequence[AffinityRow]] | None = None,
        observations: dict[UUID, Sequence[FoodObservation]] | None = None,
    ) -> None:
        self._affinities = {key: list(value) for key, value in (affinities or {}).items()}
        self._observations = {key: list(value) for key, value in (observations or {}).items()}

    async def affinities(self, *, child_id: UUID) -> list[AffinityRow]:
        return list(self._affinities.get(child_id, ()))

    async def observations(
        self, *, child_id: UUID, date_from: date, date_to: date
    ) -> list[FoodObservation]:
        rows = self._observations.get(child_id, ())
        return [row for row in rows if date_from <= row.observed_on <= date_to]


class InMemoryMenuCatalog:
    """메뉴 카탈로그 — 메뉴명 → 영양성분. 아이 무관, 전역 하나다."""

    def __init__(self, rows: Sequence[MenuCatalogRow] = ()) -> None:
        self._rows: dict[str, MenuCatalogRow] = {row.menu_key: row for row in rows}

    async def get(self, menu_key: str) -> MenuCatalogRow | None:
        return self._rows.get(menu_key)

    async def get_many(self, menu_keys: tuple[str, ...]) -> dict[str, MenuCatalogRow]:
        """있는 행만 돌려준다. `resolved=False` 행도 넣는다 — `all_resolved` 와 다르다."""
        return {key: self._rows[key] for key in menu_keys if key in self._rows}

    async def put(self, row: MenuCatalogRow) -> None:
        self._rows.setdefault(row.menu_key, row)

    async def all_resolved(self) -> list[MenuCatalogRow]:
        """`resolved=True` 행만 돌려준다. 재료(ingredients) 유무는 여기서 거르지 않는다."""
        return [row for row in self._rows.values() if row.resolved]


class InMemoryMenuSource:
    def __init__(
        self,
        nutrition_by_name: dict[str, NutritionFacts] | None = None,
        ingredients_by_name: dict[str, tuple[str, ...]] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self._nutrition = dict(nutrition_by_name or {})
        self._ingredients = dict(ingredients_by_name or {})
        self._fail = fail

    async def nutrition(self, *, name: str) -> NutritionFacts | None:
        if self._fail:
            raise MenuSourceError(f"영양성분 조회 실패 (테스트 주입): {name}")
        return self._nutrition.get(name)

    async def ingredients(self, *, name: str) -> tuple[str, ...] | None:
        if self._fail:
            raise MenuSourceError(f"레시피 조회 실패 (테스트 주입): {name}")
        return self._ingredients.get(name)


class InMemoryDaycareMeals:
    """Food 는 급식을 새로 만들 권한이 없다 — 그래서 insert 류 메서드를 두지 않는다.

    `child_id` 는 아이 경계를 지키는 축이다(row 에 있는 값과 인자로 받은 값을 맞춰 본다).
    실제 DDL 은 `daycare_meal.child_id NOT NULL` 이라, 어댑터가 `id` 하나만으로 쓰면
    잘못된 id 가 남의 아이 급식 행을 고치거나 지울 수 있다 — 그 상황을 InMemory 에서도
    같은 예외(KeyError)로 드러낸다.

    - `update`: 없는 id → KeyError. 있는데 저장된 행의 `child_id` 가 인자 행과 다르면 →
      KeyError (남의 행을 내 아이 것으로 옮기는 길을 막는다).
    - `delete`: 없는 id 는 조용히 넘긴다 — 이미 지워진 것과 같은 정상 흐름이다.
      있는데 다른 아이 것이면 KeyError — 조용히 넘기면 남의 데이터를 건드리려 한 사실이
      사라진다. 검사를 먼저 다 끝내고 나서 지우므로, `row_ids` 여럿 중 하나라도 다른
      아이 것이면 아무것도 지워지지 않는다(부분 삭제 없음).
    """

    def __init__(self, rows: Sequence[DaycareMealRow] = ()) -> None:
        self._rows: dict[UUID, DaycareMealRow] = {row.id: row for row in rows}

    async def rows(self, *, child_id: UUID, date_from: date, date_to: date) -> list[DaycareMealRow]:
        matched = [
            row
            for row in self._rows.values()
            if row.child_id == child_id and date_from <= row.serve_date <= date_to
        ]
        return sorted(matched, key=lambda row: (row.serve_date, row.meal_slot))

    async def has_rows(self, *, child_id: UUID) -> bool:
        return any(row.child_id == child_id for row in self._rows.values())

    async def update(self, row: DaycareMealRow) -> None:
        existing = self._rows.get(row.id)
        if existing is None:
            raise KeyError(f"없는 daycare_meal 행은 수정할 수 없다: {row.id}")
        if existing.child_id != row.child_id:
            raise KeyError(f"다른 아이의 daycare_meal 행은 수정할 수 없다: {row.id}")
        self._rows[row.id] = row

    async def delete(self, *, child_id: UUID, row_ids: tuple[UUID, ...]) -> None:
        """검사를 먼저 끝내고 지운다 — 부분 삭제 상태로 예외를 올리지 않는다.

        실제 어댑터는 `DELETE ... WHERE id = ANY(?) AND child_id = ?` 한 문장이라
        본디 all-or-nothing 이다. 여기서 먼저 훑어 다른 아이 소유를 걸러내는 것은
        그 원자성을 InMemory 로 흉내 내는 것이다.
        """
        for row_id in row_ids:
            existing = self._rows.get(row_id)
            if existing is not None and existing.child_id != child_id:
                raise KeyError(f"다른 아이의 daycare_meal 행은 지울 수 없다: {row_id}")

        for row_id in row_ids:
            self._rows.pop(row_id, None)  # 없는 id 는 이미 지워진 것과 같다 — 조용히 넘긴다


class InMemoryFoodDocs:
    def __init__(self, rows: Sequence[FoodDocRow] = ()) -> None:
        self._rows = list(rows)

    async def search(
        self, *, stage: Stage, row_types: tuple[str, ...], keys: tuple[str, ...]
    ) -> list[FoodDocRow]:
        # stage·keys 로 좁히는 것은 실제 검색(임베딩·인덱스)의 몫이다.
        # InMemory 는 테스트가 필요한 행만 미리 넣어 두므로 row_type 만 거른다.
        if not row_types:
            return list(self._rows)
        return [row for row in self._rows if row.row_type in row_types]


class InMemorySuggestionHistory:
    def __init__(self, recent: dict[UUID, frozenset[str]] | None = None) -> None:
        self._recent = dict(recent or {})

    async def recent_menu_keys(self, *, child_id: UUID, since: date) -> frozenset[str]:
        return self._recent.get(child_id, frozenset())


def in_memory_ports(**overrides: object) -> FoodPorts:
    """`FoodPorts` 를 기본 InMemory 구현으로 채운다. 필요한 포트만 override 로 바꿔 끼운다."""
    defaults: dict[str, object] = {
        "profile": InMemoryProfile(),
        "consent": InMemoryConsent(),
        "safety": InMemorySafety(),
        "growth": InMemoryGrowth(),
        "memory": InMemoryFoodMemory(),
        "catalog": InMemoryMenuCatalog(),
        "daycare": InMemoryDaycareMeals(),
        "docs": InMemoryFoodDocs(),
        "suggestions": InMemorySuggestionHistory(),
        "menu_source": InMemoryMenuSource(),
    }
    defaults.update(overrides)
    return FoodPorts(**defaults)
