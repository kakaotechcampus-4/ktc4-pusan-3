"""Food 라이브 eval 의 가짜 세계 — 아이 · 기록 · 레시피 · 급식 · 문서. 전부 가짜다.

`test_expected_food.md` §0 의 픽스처 이름(C48 · C48-milk …)을 그대로 쓴다. 기준 시각이 날마다
바뀌므로(`now` 를 받는다) 날짜는 전부 오늘에서 센다. 실제 영양DB · 레시피 API 는 쓰지 않는다 —
레시피는 아래 표가 정본이고, 알레르기 코드는 메뉴 재료에 맞춰 적었다(번호는 `app.rules.allergen`).

한 가지 주의 — 스펙의 C48-dc 는 "오늘 lunch · 오늘 snack_pm · 다음 주 화요일 lunch" 인데
T43("내일모레는 어린이집 빠져")과 T46("어제 밥 남겼대")이 어제 · 모레 행을 기대한다. 그래서
어제 lunch · 모레 lunch · 모레 snack_pm 행을 더했다.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.agents.common.evidence import AffinityRow
from app.agents.food.context import FoodContext
from app.agents.food.meal_dates import resolve_meal_date
from app.agents.food.store import (
    DaycareMealRow,
    FoodObservation,
    FoodPorts,
    InMemoryConsent,
    InMemoryDaycareMeals,
    InMemoryFoodDocs,
    InMemoryFoodMemory,
    InMemoryProfile,
    InMemoryRecipeCatalog,
    InMemorySafety,
    InMemorySuggestionHistory,
    MenuCatalogRow,
    SafetyEntry,
    in_memory_ports,
)
from app.rules.menu_key import normalize_menu_key

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID("00000000-0000-7000-8000-000000000001")

# (이름, 주재료, 재료, 식품군, 알레르기 코드). 코드: 1 난류 · 2 우유 · 4 땅콩 · 5 대두 · 6 밀
#   10 돼지고기 · 15 닭고기 · 16 쇠고기
_TODDLER = (
    ("두부조림", ("두부",), ("두부", "간장", "대파"), ("meat_fish_egg_legumes",), (5,)),
    ("시금치나물", ("시금치",), ("시금치", "참기름", "마늘"), ("vegetables",), ()),
    ("미역국", ("미역",), ("미역", "참기름", "국간장"), ("vegetables",), ()),
    (
        "소불고기",
        ("쇠고기",),
        ("쇠고기", "양파", "배", "간장"),
        ("meat_fish_egg_legumes",),
        (5, 6, 16),
    ),
    ("계란말이", ("계란",), ("계란", "당근", "대파"), ("meat_fish_egg_legumes",), (1,)),
    ("계란찜", ("계란",), ("계란", "새우젓"), ("meat_fish_egg_legumes",), (1, 9)),
    ("브로콜리볶음", ("브로콜리",), ("브로콜리", "마늘", "올리브유"), ("vegetables",), ()),
    ("브로콜리치즈구이", ("브로콜리",), ("브로콜리", "치즈"), ("vegetables", "dairy"), (2,)),
    ("애호박전", ("애호박",), ("애호박", "부침가루", "계란"), ("vegetables",), (1, 6)),
    ("당근계란볶음밥", ("밥",), ("밥", "당근", "계란"), ("grains",), (1,)),
    ("닭죽", ("닭고기",), ("닭고기", "쌀", "당근"), ("grains",), (15,)),
    ("가자미구이", ("가자미",), ("가자미", "올리브유"), ("meat_fish_egg_legumes",), ()),
    ("새우볶음밥", ("새우",), ("새우", "밥", "당근"), ("grains",), (9,)),
    ("감자채볶음", ("감자",), ("감자", "양파", "식용유"), ("grains", "vegetables"), ()),
    ("고구마범벅", ("고구마",), ("고구마", "설탕"), ("grains",), ()),
    ("딸기요거트", ("딸기",), ("딸기", "요거트"), ("fruits", "dairy"), (2,)),
    ("딸기바나나볼", ("딸기",), ("딸기", "바나나", "오트밀"), ("fruits",), ()),
    (
        "바나나팬케이크",
        ("바나나",),
        ("바나나", "밀가루", "계란", "우유"),
        ("fruits", "grains"),
        (1, 2, 6),
    ),
    ("땅콩버터토스트", ("식빵",), ("식빵", "땅콩버터"), ("grains",), (4, 6)),
    ("우유푸딩", ("우유",), ("우유", "설탕"), ("dairy",), (2,)),
    ("치즈주먹밥", ("밥",), ("밥", "치즈", "김"), ("grains", "dairy"), (2,)),
    ("콩나물무침", ("콩나물",), ("콩나물", "참기름", "마늘"), ("vegetables",), ()),
    ("오이무침", ("오이",), ("오이", "식초", "참기름"), ("vegetables",), ()),
    ("사과조림", ("사과",), ("사과", "설탕"), ("fruits",), ()),
    ("두부스테이크", ("두부",), ("두부", "당근", "양파"), ("meat_fish_egg_legumes",), (5,)),
    ("팝콘", ("옥수수",), ("옥수수", "소금"), ("grains",), ()),
    (
        "돼지고기야채볶음",
        ("돼지고기",),
        ("돼지고기", "양배추", "당근"),
        ("meat_fish_egg_legumes",),
        (10,),
    ),
    ("무나물", ("무",), ("무", "참기름"), ("vegetables",), ()),
)
# 이유기(4–11개월) — 단계 · 질감은 `stage_min` 으로만 건다
_WEANING = (
    ("쌀미음", ("쌀",), ("쌀", "물"), ("grains",), ()),
    ("감자죽", ("감자",), ("감자", "쌀"), ("grains",), ()),
    ("애호박죽", ("애호박",), ("애호박", "쌀"), ("vegetables",), ()),
    ("두부죽", ("두부",), ("두부", "쌀"), ("meat_fish_egg_legumes",), (5,)),
    ("닭고기야채죽", ("닭고기",), ("닭고기", "쌀", "당근"), ("meat_fish_egg_legumes",), (15,)),
    ("바나나으깨기", ("바나나",), ("바나나",), ("fruits",), ()),
    (
        "꿀바나나죽",
        ("바나나",),
        ("바나나", "쌀", "꿀"),
        ("fruits",),
        (),
    ),  # 12개월 전 꿀 — 풀에서 빠져야 한다
)
# C48-pool0 · narrow 용 — 계란이 든 레시피
_EGG_ONLY = tuple(row for row in _TODDLER if 1 in row[4])


@dataclass(frozen=True)
class WorldSpec:
    months: int = 48
    allergy: tuple[str, ...] = ()
    consent: bool = True
    safety_fails: bool = False
    records: bool = True  # False 면 관찰 · affinity 가 0건이다(C48-empty)
    recipes: str = "toddler"  # toddler · weaning · pool2 · pool0
    history: tuple[str, ...] = ()  # 최근 3일 안에 추천한 menu_key
    daycare: bool = True


WORLDS: dict[str, WorldSpec] = {
    "C2": WorldSpec(months=2, recipes="toddler"),
    "C8": WorldSpec(months=8, recipes="weaning"),
    "C30": WorldSpec(months=30),
    "C48": WorldSpec(),
    "C48-milk": WorldSpec(allergy=("우유",)),
    "C48-peanut": WorldSpec(allergy=("땅콩",)),
    "C48-egg": WorldSpec(allergy=("달걀",)),  # 재호출 시험 — test 가 풀에 계란 레시피를 끼운다
    "C48-shrimp": WorldSpec(allergy=("새우",)),
    "C48-noconsent": WorldSpec(consent=False),
    "C48-safetyfail": WorldSpec(safety_fails=True),
    "C48-empty": WorldSpec(records=False),
    "C48-pool2": WorldSpec(recipes="pool2"),
    "C48-pool0": WorldSpec(allergy=("달걀",), recipes="pool0"),
    "C48-nodc": WorldSpec(daycare=False),
    "C48-prev": WorldSpec(history=("소불고기", "두부조림", "시금치나물")),
}


@dataclass(frozen=True)
class World:
    name: str
    spec: WorldSpec
    context: FoodContext
    recipes: tuple[MenuCatalogRow, ...]
    today: date
    birth: date
    extra: dict[str, object] = field(default_factory=dict)

    def recipe(self, content: str) -> MenuCatalogRow | None:
        """추천 문장(= 메뉴 이름)으로 레시피 행을 찾는다. 풀 밖이면 None."""
        key = normalize_menu_key(content)
        return next((row for row in self.recipes if row.menu_key == key), None)


def birth_date_for(months: int, today: date) -> date:
    """오늘 기준 `months` 개월 된 날. 말일이 모자라면 그 달 말일로 맞춘다(월령은 달력 계산)."""
    year, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(year, month + 1, min(today.day, calendar.monthrange(year, month + 1)[1]))


def build_world(
    name: str,
    *,
    now: datetime,
    run_id: str = "live",
    birth_date: date | None = None,
    memory: InMemoryFoodMemory | None = None,
    safety: Sequence[SafetyEntry] | None = None,
) -> World:
    """이름으로 세계를 만든다. `birth_date` · `memory` · `safety` 를 주면 그것으로 바꿔 끼운다
    (Supervisor 를 거치는 eval 은 Memory 가 같은 run 에 저장한 관찰을 Food 가 읽어야 한다)."""
    spec = WORLDS[name]
    today = now.astimezone(KST).date()
    birth = birth_date or birth_date_for(spec.months, today)
    recipes = _recipes(spec, now)
    entries = [SafetyEntry(kind="allergy", label=label, status="active") for label in spec.allergy]
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: birth}),
        consent=InMemoryConsent({CHILD: spec.consent}),
        safety=InMemorySafety(entries if safety is None else safety, fail=spec.safety_fails),
        memory=memory if memory is not None else _memory(spec, today),
        recipes=InMemoryRecipeCatalog(recipes),
        daycare=InMemoryDaycareMeals(_daycare(today) if spec.daycare else []),
        docs=InMemoryFoodDocs.from_seed(include_draft=True),
        suggestions=InMemorySuggestionHistory(
            {CHILD: frozenset(normalize_menu_key(key) for key in spec.history)}
        ),
    )
    context = FoodContext(child_id=CHILD, run_id=run_id, now=now, timezone=KST, ports=ports)
    return World(
        name=name, spec=spec, context=context, recipes=tuple(recipes), today=today, birth=birth
    )


def _row(spec: tuple, now: datetime, *, stage_min: str | None = None) -> MenuCatalogRow:
    name, main, ingredients, groups, codes = spec
    return MenuCatalogRow(
        menu_key=normalize_menu_key(name),
        display_name=name,
        source="mafra_recipe",
        resolved=True,
        synced_at=now,
        ingredients=ingredients,
        main_ingredients=main,
        allergen_codes=frozenset(codes),
        food_groups=frozenset(groups),
        stage_min=stage_min,  # type: ignore[arg-type]
        source_id=name,
    )


def _recipes(spec: WorldSpec, now: datetime) -> list[MenuCatalogRow]:
    if spec.recipes == "weaning":
        return [_row(row, now, stage_min="infant_weaning") for row in _WEANING]
    if spec.recipes == "pool2":
        return [_row(row, now) for row in _TODDLER[:2]]
    if spec.recipes == "pool0":
        return [_row(row, now) for row in _EGG_ONLY[:3]]
    return [_row(row, now) for row in _TODDLER]


def _memory(spec: WorldSpec, today: date) -> InMemoryFoodMemory:
    if not spec.records:
        return InMemoryFoodMemory()
    # 최근 7일 기록이 충분하다(10행 이상). 딸기는 좋아하고 브로콜리는 안 먹는다
    meals = (
        (1, "현미밥", "한 그릇"),
        (1, "미역국", "한 그릇"),
        (1, "소불고기", "반 그릇"),
        (2, "시금치나물", "조금"),
        (2, "닭죽", "한 그릇"),
        (3, "감자채볶음", "한 그릇"),
        (3, "두부조림", "한 그릇"),
        (4, "딸기", "다 먹음"),
        (4, "계란말이", "한 그릇"),
        (5, "무나물", "조금"),
        (5, "가자미구이", "한 그릇"),
        (6, "고구마범벅", "한 그릇"),
    )
    observed = [
        FoodObservation(
            id=uuid4(),
            child_id=CHILD,
            observed_on=today - timedelta(days=days_ago),
            subject=subject,
            amount_text=amount,
            polarity=0,
            updated_at=datetime.combine(today, datetime.min.time(), tzinfo=KST),
            confidence_source="parent_direct",
        )
        for days_ago, subject, amount in meals
    ]
    affinities = [
        _affinity("딸기", polarity=1, state="confirmed", strength=0.9, today=today),
        _affinity("브로콜리", polarity=-1, state="confirmed", strength=0.8, today=today),
        _affinity("당근", polarity=None, state="candidate", strength=0.6, today=today),
    ]
    return InMemoryFoodMemory({CHILD: affinities}, {CHILD: observed})


def _affinity(
    label: str, *, polarity: int | None, state: str, strength: float, today: date
) -> AffinityRow:
    return AffinityRow(
        id=uuid4(),
        merge_key=label,
        state=state,  # type: ignore[arg-type]
        polarity=polarity,
        strength=strength,
        last_observed_on=today - timedelta(days=2),
    )


def _daycare(today: date) -> list[DaycareMealRow]:
    """C48-dc — 오늘 점심 · 오후 간식, 어제 점심, 모레 점심 · 오후 간식, 다음 주 화요일 점심."""

    def row(day: date, slot: str, *menus: str) -> DaycareMealRow:
        return DaycareMealRow(
            id=uuid4(),
            child_id=CHILD,
            serve_date=day,
            meal_slot=slot,  # type: ignore[arg-type]
            menu_keys=tuple(normalize_menu_key(menu) for menu in menus),
        )

    next_tuesday = resolve_meal_date("다음 주 화요일", today=today)
    rows = [
        row(today, "lunch", "현미밥", "미역국", "우유"),
        row(today, "snack_pm", "고구마", "과일"),
        row(today - timedelta(days=1), "lunch", "잡곡밥", "된장국", "계란말이"),
        row(today + timedelta(days=2), "lunch", "잡곡밥", "닭볶음탕", "우유"),
        row(today + timedelta(days=2), "snack_pm", "찐감자"),
    ]
    # 일요일에는 "다음 주 화요일" 이 모레다 — 같은 날 점심이 둘이면 판정이 엉뚱한 행을 본다.
    # 모레 점심에도 우유가 있어서 그 행이 다음 주 화요일 점심 노릇을 한다
    if next_tuesday != today + timedelta(days=2):
        rows.append(row(next_tuesday, "lunch", "잡곡밥", "불고기", "시금치나물", "우유"))
    return rows


def ports_of(world: World) -> FoodPorts:
    return world.context.ports
