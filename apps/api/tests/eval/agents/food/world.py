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
    InMemoryMenuCatalog,
    InMemoryProfile,
    InMemoryRecipeCatalog,
    InMemorySafety,
    InMemorySuggestionHistory,
    MenuCatalogRow,
    SafetyEntry,
    in_memory_ports,
)
from app.agents.food.weaning_menu import weaning_menu_rows
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
# 견과류 알레르기 세계(C48-nut)에 더하는 견과류 메뉴 — 풀 필터가 이 다섯을 모두 빼야 한다.
# 땅콩 4 · 호두 14 · 잣 19. 아몬드 · 밤은 19종 밖이라 코드 없이 글자로만 걸린다
_NUT_DISHES = (
    ("호두강정", ("호두",), ("호두", "조청"), (), (14,)),
    ("땅콩조림", ("땅콩",), ("땅콩", "간장", "설탕"), (), (4,)),
    ("잣죽", ("잣",), ("잣", "쌀"), (), (19,)),
    ("아몬드멸치볶음", ("멸치",), ("멸치", "아몬드", "간장"), (), ()),
    ("밤밥", ("쌀",), ("쌀", "밤"), (), ()),
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
    # default 는 C48-dc 급식(오늘 · 어제 · 모레 …). week 는 지난 2일 + 오늘 ~ 모레 점심 1행씩인
    # 이번 주 급식표 — 영양소 분석이 이번 주 남은 급식까지 보는지 본다
    daycare_menu: str = "default"
    # default · sparse_veg · sparse_veg_unknown · few. sparse_veg 는 최근 7일에 채소 · 과일이 기록된
    # 날의 절반 미만으로 나온다. sparse_veg_unknown 은 거기에 식품군을 모르는 메뉴가 낀 날을 더한다.
    # few 는 기록이 4행 · 2일뿐인 아이(출현은 말하지만 10행에 못 미친다)
    meals: str = "default"
    # True 면 먹은 메뉴의 menu_catalog 행(식품군)을 채운다. 비어 있으면 단일 식품 표
    # (reference/plain_food_groups.yaml)에 있는 이름만 식품군을 알아서, 기본 식단(meals="default")은
    # 식품군을 아는 메뉴가 절반에 못 미쳐 appearance_ready=False 다 — 부족 식품군 가중 · 오늘 겹침
    # 가중이 돌지 않는다
    catalog: bool = False


WORLDS: dict[str, WorldSpec] = {
    "C2": WorldSpec(months=2, recipes="toddler"),
    "C8": WorldSpec(months=8, recipes="weaning"),
    "C30": WorldSpec(months=30),
    "C48": WorldSpec(),
    "C48-milk": WorldSpec(allergy=("우유",)),
    "C48-peanut": WorldSpec(allergy=("땅콩",)),
    "C48-egg": WorldSpec(allergy=("달걀",)),  # 재호출 시험 — test 가 풀에 계란 레시피를 끼운다
    "C48-shrimp": WorldSpec(allergy=("새우",)),
    "C48-nut": WorldSpec(allergy=("견과류",), recipes="nut"),  # 견과류 요청 · 섭취 확인
    "C48-noconsent": WorldSpec(consent=False),
    "C48-safetyfail": WorldSpec(safety_fails=True),
    "C48-empty": WorldSpec(records=False),
    "C48-pool2": WorldSpec(recipes="pool2"),
    "C48-pool0": WorldSpec(allergy=("달걀",), recipes="pool0"),
    "C48-nodc": WorldSpec(daycare=False),
    # 대두 알레르기 — 생성 메뉴가 간장 · 된장 같은 양념을 재료에 안 적으면 필터가 놓칠 수 있다(#740)
    "C48-soy": WorldSpec(allergy=("대두",)),
    "C48-prev": WorldSpec(history=("소불고기", "두부조림", "시금치나물")),
    # 영양 반영이 실제로 도는 세계 — 출현 창(오늘 - 7일 ~ 이번 주 일요일)에서 채소 3일 · 과일 2일 /
    # 기록된 8일(토 · 일은 7일). 어제 · 오늘 · 모레 급식 메뉴까지 카탈로그에 있어야
    # 모르는 날이 안 낀다
    "C48-veg": WorldSpec(meals="sparse_veg", catalog=True),
    # C48-veg 에 채소가 안 나온 2일 전 · 3일 전마다 카탈로그에 없는 메뉴를 더한다 — 확인한 날만 보면
    # 적게 나왔지만(가중은 걸린다) 모르는 날을 다 나왔다고 치면 절반 이상이라 안내 줄은 없다(#739)
    "C48-veg-unknown": WorldSpec(meals="sparse_veg_unknown", catalog=True),
    # 집 기록 0 · 이번 주 급식표만 있는 아이 — 수요일이면 지난 급식 2일 + 오늘 ~ 금요일 급식 3일.
    # 채소는 목요일 하루뿐이라 출현은 말하지만 적은 기록이다(5행). 판정 창은 2행이라 구간은 막힌다
    "C48-wk": WorldSpec(records=False, catalog=True, daycare_menu="week"),
    # 급식이 없고 집 기록이 4행 · 2일뿐인 아이 — 채소가 한 번도 안 나왔다(출현 4행, 구간은 막힘)
    "C48-few": WorldSpec(daycare=False, catalog=True, meals="few"),
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
        # 이유기 후보는 레시피북이 아니라 이유식 식단 표다(#399)
        weaning = weaning_menu_rows(self.spec.months, synced_at=self.context.now)
        return next((row for row in (*self.recipes, *weaning) if row.menu_key == key), None)


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
        catalog=InMemoryMenuCatalog(_menu_catalog(now) if spec.catalog else ()),
        daycare=InMemoryDaycareMeals(_daycare_rows(spec, today) if spec.daycare else []),
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
    if spec.recipes == "nut":
        return [_row(row, now) for row in (*_TODDLER, *_NUT_DISHES)]
    if spec.recipes == "pool2":
        return [_row(row, now) for row in _TODDLER[:2]]
    if spec.recipes == "pool0":
        return [_row(row, now) for row in _EGG_ONLY[:3]]
    return [_row(row, now) for row in _TODDLER]


# 최근 7일 기록이 충분하다(10행 이상). (며칠 전, 메뉴, 양)
_MEALS = (
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
# C48-veg — 행 수는 같고 채소는 5일 전 시금치나물뿐이다. 어제 급식 된장국까지 채소 2일 · 과일 1일 /
# 기록된 6일이라 둘 다 절반(3일) 미만이다(N-19)
_SPARSE_VEG_MEALS = (
    (1, "현미밥", "한 그릇"),
    (1, "소불고기", "반 그릇"),
    (2, "닭죽", "한 그릇"),
    (2, "계란말이", "한 그릇"),
    (3, "두부조림", "한 그릇"),
    (3, "현미밥", "한 그릇"),
    (4, "딸기", "다 먹음"),
    (4, "가자미구이", "한 그릇"),
    (5, "고구마범벅", "한 그릇"),
    (5, "시금치나물", "조금"),
    (6, "닭죽", "한 그릇"),
    (6, "계란말이", "한 그릇"),
)
# C48-veg-unknown — C48-veg 에서 채소 · 과일이 안 나온 2일 전 · 3일 전에 카탈로그에 없는 메뉴를
# 더한다.
# 채소 2 + 모르는 날 2 = 4, 과일 1 + 2 = 3 이라 둘 다 기록된 6일의 절반(3일) 미만이 아니다
_UNKNOWN_VEG_MEALS = (
    *_SPARSE_VEG_MEALS,
    (2, "처음보는반찬", "한 그릇"),
    (3, "낯선국", "한 그릇"),
)
# C48-few — 1일 전 현미밥 · 소불고기, 2일 전 닭죽 · 딸기. 4행 · 2일, 채소 0일 · 과일 1일
# (기록된 2일의 절반 미만이 아니다)이라 채소만 적게 나온다
_FEW_MEALS = (
    (1, "현미밥", "한 그릇"),
    (1, "소불고기", "반 그릇"),
    (2, "닭죽", "한 그릇"),
    (2, "딸기", "다 먹음"),
)
# C48-wk 급식표 — (오늘 기준 며칠 뒤, 점심 메뉴). 5일 중 채소는 +1일(목요일)뿐이고, 오늘에는 채소가
# 없어 오늘 겹침(×0.7)이 채소 가중에 섞이지 않는다
_WEEK_DAYCARE: tuple[tuple[int, tuple[str, ...]], ...] = (
    (-2, ("잡곡밥", "계란말이", "딸기")),
    (-1, ("현미밥", "두부조림")),
    (0, ("잡곡밥", "소불고기", "딸기")),
    (1, ("현미밥", "된장국", "가자미구이")),
    (2, ("닭죽", "계란말이", "딸기")),
)
# menu_catalog — 먹은 메뉴와 급식(어제 잡곡밥 · 된장국 · 계란말이, 오늘 · 모레 C48-dc 급식,
# 이번 주 급식표)의 식품군. 영양값은 비워 둔다 — 1차 출력처럼 구간은 못 내고(insufficient)
# 식품군 출현만 센다. 급식 메뉴가 빠지면 출현 창에 든 오늘 · 모레 급식이 "식품군을 모르는 날" 이
# 되어 평일에 채소 줄이 사라진다
_CATALOG_GROUPS: dict[str, tuple[str, ...]] = {
    "현미밥": ("grains",),
    "잡곡밥": ("grains",),
    "닭죽": ("grains",),
    "고구마범벅": ("grains",),
    "소불고기": ("meat_fish_egg_legumes",),
    "계란말이": ("meat_fish_egg_legumes",),
    "두부조림": ("meat_fish_egg_legumes",),
    "가자미구이": ("meat_fish_egg_legumes",),
    "시금치나물": ("vegetables",),
    "된장국": ("vegetables",),
    "딸기": ("fruits",),
    "미역국": ("vegetables",),
    "우유": ("dairy",),
    "고구마": ("grains",),
    "과일": ("fruits",),
    "닭볶음탕": ("meat_fish_egg_legumes",),
    "찐감자": ("grains",),
}


def _menu_catalog(now: datetime) -> list[MenuCatalogRow]:
    return [
        MenuCatalogRow(
            menu_key=normalize_menu_key(name),
            display_name=name,
            source="manual",
            resolved=True,
            synced_at=now,
            food_groups=frozenset(groups),
        )
        for name, groups in _CATALOG_GROUPS.items()
    ]


def _memory(spec: WorldSpec, today: date) -> InMemoryFoodMemory:
    if not spec.records:
        return InMemoryFoodMemory()
    # 딸기는 좋아하고 브로콜리는 안 먹는다
    meals = {
        "sparse_veg": _SPARSE_VEG_MEALS,
        "sparse_veg_unknown": _UNKNOWN_VEG_MEALS,
        "few": _FEW_MEALS,
    }.get(spec.meals, _MEALS)
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
        observed_days=3,
    )


def _daycare_rows(spec: WorldSpec, today: date) -> list[DaycareMealRow]:
    return _daycare_week(today) if spec.daycare_menu == "week" else _daycare(today)


def _daycare_row(day: date, slot: str, *menus: str) -> DaycareMealRow:
    return DaycareMealRow(
        id=uuid4(),
        child_id=CHILD,
        serve_date=day,
        meal_slot=slot,  # type: ignore[arg-type]
        menu_keys=tuple(normalize_menu_key(menu) for menu in menus),
    )


def _daycare_week(today: date) -> list[DaycareMealRow]:
    """C48-wk — 지난 2일 + 오늘 ~ 모레 점심 1행씩(`_WEEK_DAYCARE`)."""
    return [
        _daycare_row(today + timedelta(days=offset), "lunch", *menus)
        for offset, menus in _WEEK_DAYCARE
    ]


def _daycare(today: date) -> list[DaycareMealRow]:
    """C48-dc — 오늘 점심 · 오후 간식, 어제 점심, 모레 점심 · 오후 간식, 다음 주 화요일 점심."""
    row = _daycare_row

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
