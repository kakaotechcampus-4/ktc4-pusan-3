"""Food 조회 · 후보 · 급식 tool 테스트가 같이 쓰는 값. 이름 · 메뉴 · 기록은 전부 가짜다."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.agents.common.evidence import AffinityRow, AffinityState
from app.agents.food.context import FoodContext
from app.agents.food.store import (
    DaycareMealRow,
    FoodDocRow,
    FoodObservation,
    InMemoryDaycareMeals,
    InMemoryFoodDocs,
    InMemoryFoodMemory,
    InMemoryMenuCatalog,
    InMemoryProfile,
    InMemoryRecipeCatalog,
    InMemorySuggestionHistory,
    MenuCatalogRow,
    SafetyEntry,
    in_memory_ports,
)
from app.agents.food.store.ports import ConfidenceSource, DaycareSlot

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=KST)
TODAY = NOW.date()
BIRTH_DATE = date(2024, 6, 14)  # NOW 기준 28개월 — toddler


def day(days_ago: int) -> date:
    return TODAY - timedelta(days=days_ago)


def context(
    *,
    memory: InMemoryFoodMemory | None = None,
    daycare: InMemoryDaycareMeals | None = None,
    catalog: InMemoryMenuCatalog | None = None,
    recipes: InMemoryRecipeCatalog | None = None,
    docs: InMemoryFoodDocs | None = None,
    suggestions: InMemorySuggestionHistory | None = None,
    safety: Sequence[SafetyEntry] | None = (),
    birth: date = BIRTH_DATE,
    run_id: str = "run-1",
    utterance: str = "",
) -> FoodContext:
    """`build_gate` 가 한 뒤의 context. `safety=None` 이면 조회 실패(`알레르기 확인 못 함`)다."""
    overrides: dict[str, object] = {"profile": InMemoryProfile({CHILD: birth})}
    for name, value in (
        ("memory", memory),
        ("daycare", daycare),
        ("catalog", catalog),
        ("recipes", recipes),
        ("docs", docs),
        ("suggestions", suggestions),
    ):
        if value is not None:
            overrides[name] = value
    made = FoodContext(
        child_id=CHILD,
        run_id=run_id,
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(**overrides),
    )
    made.state.safety = None if safety is None else tuple(safety)
    made.state.utterance = utterance
    return made


def observation(
    subject: str,
    *,
    days_ago: int = 0,
    polarity: int = 0,
    amount_text: str | None = None,
    confidence_source: ConfidenceSource | None = None,
    source_notice_id: UUID | None = None,
) -> FoodObservation:
    return FoodObservation(
        id=uuid4(),
        child_id=CHILD,
        observed_on=day(days_ago),
        subject=subject,
        amount_text=amount_text,
        polarity=polarity,
        updated_at=NOW,
        confidence_source=confidence_source,
        source_notice_id=source_notice_id,
    )


def affinity(
    label: str,
    *,
    polarity: int | None = 1,
    state: AffinityState = "confirmed",
    strength: float = 0.8,
    days_ago: int = 1,
) -> AffinityRow:
    return AffinityRow(
        id=uuid4(),
        merge_key=label,
        state=state,
        polarity=polarity,
        strength=strength,
        last_observed_on=day(days_ago),
    )


def daycare(
    *menu_keys: str,
    days_ago: int = 1,
    slot: DaycareSlot = "lunch",
    amount_factor: float = 1.0,
    amount_known: bool = False,
) -> DaycareMealRow:
    return DaycareMealRow(
        id=uuid4(),
        child_id=CHILD,
        serve_date=day(days_ago),
        meal_slot=slot,
        menu_keys=tuple(menu_keys),
        amount_factor=amount_factor,
        amount_known=amount_known,
    )


def menu_row(
    menu_key: str,
    *,
    source: str = "manual",
    ingredients: tuple[str, ...] = ("가짜재료",),
    **overrides: object,
) -> MenuCatalogRow:
    """`menu_catalog` / `recipe_catalog` 행 하나. 레시피 행은 `source="mafra_recipe"` 로 넘긴다."""
    fields: dict[str, object] = {
        "menu_key": menu_key,
        "display_name": menu_key,
        "source": source,
        "resolved": True,
        "synced_at": NOW,
        "ingredients": ingredients,
    }
    if source == "mafra_recipe":
        fields["source_id"] = menu_key
    fields.update(overrides)
    return MenuCatalogRow(**fields)  # type: ignore[arg-type]


def doc_row(
    title: str,
    *,
    row_type: str = "weaning_stage",
    months: tuple[int, int] = (4, 12),
    body: str = "가짜 문장이다.",
    codes: frozenset[int] = frozenset(),
    org: str = "가짜 기관",
) -> FoodDocRow:
    return FoodDocRow(
        id=uuid4(),
        row_type=row_type,
        title=title,
        body=body,
        min_month=months[0],
        max_month=months[1],
        texture=None,
        food_groups=frozenset(),
        allergen_codes=codes,
        source_org=org,
        written_at=NOW,
    )
