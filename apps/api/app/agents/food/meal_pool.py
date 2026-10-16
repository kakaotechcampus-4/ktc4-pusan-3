"""식단 추천 후보 풀 · 샘플링 — 풀은 코드가 만든다. 모델은 보여 준 샘플 안에서만 고른다.

```
recipe_catalog (resolved · 재료 있는 행)
  ∩ 이 아이의 단계(stage_min)
  − 이름이 같은 레시피는 하나로 (알레르기 · 재료는 합집합)
  − 알레르기 · 질환 · 연령 금지 식품 (`filter_food_safety` — 막힌 것 · 확인 못 한 것 모두)
  − 최근 추천한 메뉴
  → 가중치를 붙여 최대 `POOL_MAX` 개 (기피 · 최근 3일 먹은 메뉴 ↓, 선호 · 부족 식품군 ↑)
        │ `sample_pool` — 가중 샘플링 + 같은 주재료 2개 이하 (seed = run_id)
        ▼
  모델에게 보여 줄 10~15개
```

- **기피는 풀에서 빼지 않는다.** 가중치만 낮춘다. 풀에서 빠지는 것은 안전 필터가 막았거나 확인하지
  못한 메뉴뿐이다(루트 §2 — 알레르기 필터는 코드가 막는다).
- **최근 3일 먹은 메뉴도 빼지 않는다.** 이름이 같은 후보의 가중치만 낮춘다 — 반복은 위험이 아니라
  다양성 문제라, 빼면 급식 메뉴가 자주 겹치는 아이의 풀만 줄어든다.
- **부족 식품군은 기피보다 세다.** 영양 판정이 `low` 인 항목의 식품군은 기피 가중치를 상쇄한다.
  기록이 모자라(`insufficient`) 판정이 없으면 영양 가중치는 주지 않고 평소 순위로 간다.
- 샘플링은 안전 필터 **뒤에** 온다. 앞에 두면 위험한 후보가 뽑힐 자리가 생긴다.
- 같은 `seed` 면 같은 결과다(재현 · QA). 흔드는 것은 메뉴뿐이고 판정은 흔들지 않는다.
- 풀이 3개 미만이어도 재호출하지 않고 있는 만큼만 낸다(#216). 풀 밖 메뉴를 만들어 채우지 않는다.
"""

import random
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from app.agents.common.age import first_month_of, life_stage
from app.agents.common.evidence import OBSERVATION_WINDOW_DAYS, ObservationRow, rank_evidence
from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.food.context import FoodContext, require_safety
from app.agents.food.matching import related
from app.agents.food.nutrition_eval import (
    MealRecords,
    NutrientVerdict,
    evaluate_nutrient_bands,
    read_meal_records,
)
from app.agents.food.store.ports import MenuCatalogRow
from app.agents.food.tools.safety import filter_food_safety
from app.rules.age import Stage
from app.rules.menu_key import normalize_menu_key

POOL_MAX = 30
SAMPLE_MIN, SAMPLE_MAX = 10, 15
# 같은 주재료가 샘플에 몇 개까지 들어갈 수 있는가
MAIN_INGREDIENT_LIMIT = 2
# 최근 며칠 안에 추천한 메뉴를 풀에서 빼는가
RECENT_DAYS = 3
# 이 기간(오늘 포함) 안에 먹은 메뉴와 이름이 같은 후보는 가중치를 낮춘다. 풀에서 빼지는 않는다 —
# 빼면 급식 메뉴가 자주 겹치는 아이의 풀이 비고, 반복은 위험이 아니라 다양성 문제다
RECENT_EATEN_DAYS = 3

# 가중치. 부족 식품군이 기피를 상쇄해야 해서 `DEFICIT_BOOST * AVOID_FACTOR >= 1` 이어야 한다
BASE_WEIGHT = 1.0
AVOID_FACTOR = 0.3  # 기피 — 0 이 되지는 않는다
PREFER_FACTOR = 1.5  # 선호
DEFICIT_BOOST = 4.0  # 영양 판정이 low 인 항목의 식품군 · 적게 나온 채소 · 과일
TODAY_OVERLAP_FACTOR = 0.7  # 오늘 이미 먹은 식품군과 겹침 — 점심에 고기면 저녁은 채소 위주
RECENT_REPEAT_FACTOR = 0.4  # 최근 3일 안에 먹은 메뉴와 같은 이름 — 0 이 되지는 않는다

# 영양 판정이 `low` 인 항목 → 채워 줄 식품군. 영양소_계산_설계 §6 의 구간 → 식품군 안내와 같은
# 쪽이다
LOW_NUTRIENT_GROUPS: dict[str, frozenset[str]] = {
    "protein": frozenset({"meat_fish_egg_legumes"}),
    "calcium": frozenset({"dairy"}),
    "iron": frozenset({"meat_fish_egg_legumes"}),
}

# 영양 구간에 대응하는 항목이 없는 식품군 — 최근 7일 중 나온 날 수로 부족을 본다(N-19). 기록된 날의
# 이 비율보다 적게 나오면 부족 식품군에 더한다. 영양 구간으로 오는 부족 식품군과 합집합이다
APPEARANCE_DEFICIT_GROUPS: tuple[str, ...] = ("vegetables", "fruits")
MIN_APPEARANCE_SHARE = 0.5

# 영양 판정(KDRI)이 있는 단계. 12개월 미만은 판정하지 않는다
_NUTRIENT_STAGES: frozenset[Stage] = frozenset({"toddler", "preschool"})


@dataclass(frozen=True)
class PoolItem:
    """후보 풀의 한 줄. `weight` 는 샘플링 확률의 비율일 뿐 점수가 아니다."""

    row: MenuCatalogRow
    weight: float


async def build_pool(context: FoodContext) -> tuple[PoolItem, ...]:
    """이 아이에게 나갈 수 있는 레시피 후보를 가중치와 함께 최대 `POOL_MAX` 개 돌려준다.

    🚨 안전 정보를 못 읽었으면(`None`) 풀을 만들지 않는다 — 알레르기를 거를 수 없다.
    모델을 부르기 전에 한 번 부른다. 안전 필터에 막힌 메뉴와 확인하지 못한 메뉴(재료를 모르는
    레시피 포함)는 풀에 없다. 막힌 것 중 제품에 따라 성분이 달라질 수 있어서 막힌 메뉴
    (`needs_check`)도 확인 칸이 생기기 전에는 풀에 넣지 않는다.
    """
    entries = require_safety(context)
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    life = life_stage(birth_date, context.today)

    # 이름이 같은 레시피를 먼저 묶는다 — 단계는 묶은 뒤의 값(더 늦은 쪽)으로 본다
    rows = [row for row in await ports.recipes.all_resolved() if row.ingredients]
    merged = [row for row in _merge_same_name(rows) if _fits_stage(row, life.stage)]
    verdict = filter_food_safety(merged, entries, life.stage, months=life.months)
    safe = {row.menu_key for row in verdict.passed}
    recent = await ports.suggestions.recent_menu_keys(
        child_id=context.child_id, since=context.today - timedelta(days=RECENT_DAYS)
    )
    candidates = [row for row in merged if row.menu_key in safe and row.menu_key not in recent]
    if not candidates:
        return ()

    weights = await _weights(context, candidates, stage=life.stage, months=life.months)
    items = [PoolItem(row=row, weight=weights[row.menu_key]) for row in candidates]
    capped = _weighted_order(items, seed=f"{context.run_id}:pool")[:POOL_MAX]
    return tuple(capped)


def sample_pool(pool: Sequence[PoolItem], *, seed: str) -> tuple[PoolItem, ...]:
    """풀에서 `SAMPLE_MIN`~`SAMPLE_MAX` 개를 가중치대로 뽑는다. 같은 `seed` 면 같은 결과다.

    같은 주재료는 `MAIN_INGREDIENT_LIMIT` 개까지만 넣는다. 주재료 칸이 빈 레시피는 이 제약을
    건너뛴다(풀을 줄이지 않는다). 풀이 샘플 크기보다 작으면 풀 전체다.
    """
    rng = random.Random(seed)
    size = min(len(pool), rng.randint(SAMPLE_MIN, SAMPLE_MAX))
    chosen: list[PoolItem] = []
    counts: Counter[str] = Counter()
    for item in _weighted_order(pool, rng=rng):
        if len(chosen) >= size:
            break
        mains = {key for name in item.row.main_ingredients if (key := normalize_menu_key(name))}
        if any(counts[key] >= MAIN_INGREDIENT_LIMIT for key in mains):
            continue
        chosen.append(item)
        counts.update(mains)
    return tuple(chosen)


def remember_sample(
    context: FoodContext, sample: Sequence[PoolItem], *, recalled: bool = False
) -> None:
    """모델에게 보여 준 후보를 run state 에 적는다. 출력 tool 이 이 표 밖 `menu_key` 를 거절한다.

    더 채울 길이가 없는지(`state.exhausted`)도 여기서 정한다 — 샘플이 `MAX_SUGGESTIONS` 개 미만이면
    모델이 고를 것이 모자라 3개를 채울 수 없고, 안전 필터 뒤 재호출(`recalled`)을 이미 했으면 더
    부를 수 없다. 둘 중 하나면 출력 tool 은 3개 미만(0개 포함)도 받는다.
    """
    context.state.sampled = {item.row.menu_key: item.row for item in sample}
    context.state.exhausted = recalled or len(sample) < MAX_SUGGESTIONS


def _fits_stage(row: MenuCatalogRow, stage: Stage) -> bool:
    """레시피가 가능한 최소 단계가 아이의 단계 이하인가. 태깅이 없으면 toddler 로 본다(보수적)."""
    return first_month_of(row.stage_min or "toddler") <= first_month_of(stage)


def _merge_same_name(rows: Sequence[MenuCatalogRow]) -> list[MenuCatalogRow]:
    """이름(`menu_key`)이 같은 레시피를 하나로 묶는다. 알레르기 코드 · 재료 · 식품군은 합집합이다.

    둘 다 두면 같은 이름이 후보에 두 번 나오고, 하나만 고르면 다른 쪽 알레르기를 놓친다. 단계는
    더 높은(늦은) 쪽을 따른다 — 어느 한쪽이라도 아직 이르면 이른 것이다.
    """
    groups: dict[str, list[MenuCatalogRow]] = {}
    for row in rows:
        groups.setdefault(row.menu_key, []).append(row)
    merged: list[MenuCatalogRow] = []
    for same in groups.values():
        first = same[0]
        if len(same) == 1:
            merged.append(first)
            continue
        merged.append(
            replace(
                first,
                ingredients=_union(row.ingredients for row in same),
                main_ingredients=_union(row.main_ingredients for row in same),
                allergen_codes=frozenset().union(*(row.allergen_codes for row in same)),
                food_groups=frozenset().union(*(row.food_groups for row in same)),
                stage_min=max((row.stage_min or "toddler" for row in same), key=first_month_of),
            )
        )
    return merged


def _union(groups: Iterable[Iterable[str]]) -> tuple[str, ...]:
    """순서를 지키며 중복을 뺀 합집합."""
    seen: dict[str, None] = {}
    for group in groups:
        for name in group:
            seen.setdefault(name, None)
    return tuple(seen)


async def _weights(
    context: FoodContext, rows: Sequence[MenuCatalogRow], *, stage: Stage, months: int
) -> dict[str, float]:
    """기피 ↓ · 선호 ↑ · 부족 식품군 ↑ · 오늘 먹은 식품군과 겹침 ↓ · 최근 3일 먹은 메뉴 ↓.
    곱해서 쓴다."""
    avoided, preferred = await _signals(context)
    deficit = await _deficit_groups(context, stage=stage, months=months)
    recent = await _recent_meals(context)
    today_groups = await _today_groups(context, recent)
    eaten = _eaten_keys(recent)

    weights: dict[str, float] = {}
    for row in rows:
        weight = BASE_WEIGHT
        names = (row.display_name, *row.ingredients)
        if any(_matches(subject, names) for subject in avoided):
            weight *= AVOID_FACTOR
        elif any(_matches(subject, names) for subject in preferred):
            weight *= PREFER_FACTOR
        if row.food_groups & deficit:
            weight *= DEFICIT_BOOST
        if row.food_groups & today_groups:
            weight *= TODAY_OVERLAP_FACTOR
        if row.menu_key in eaten:
            weight *= RECENT_REPEAT_FACTOR
        weights[row.menu_key] = weight
    return weights


async def _signals(context: FoodContext) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """기피 · 선호로 줄 세운 근거의 음식 이름. `rank_evidence` 와 같은 기준이다."""
    memory = context.ports.memory
    today = context.today
    observed = await memory.observations(
        child_id=context.child_id,
        date_from=today - timedelta(days=OBSERVATION_WINDOW_DAYS),
        date_to=today,
    )
    ranked = rank_evidence(
        tuple(await memory.affinities(child_id=context.child_id)),
        tuple(
            ObservationRow(
                id=row.id,
                kind="observation_food",
                subject=row.subject,
                polarity=row.polarity,
                observed_on=row.observed_on,
            )
            for row in observed
        ),
        today=today,
    )
    avoided = tuple(item.subject for item in ranked if item.polarity < 0)
    preferred = tuple(item.subject for item in ranked if item.polarity > 0)
    return avoided, preferred


async def _deficit_groups(context: FoodContext, *, stage: Stage, months: int) -> frozenset[str]:
    """부족 식품군 — 영양 판정이 `low` 인 항목의 식품군 + 적게 나온 채소 · 과일. 판정이 없거나
    기록이 모자라면 해당 쪽은 비어 있다."""
    if stage not in _NUTRIENT_STAGES:
        return frozenset()
    verdict = await evaluate_nutrient_bands(context, months=months)
    low: frozenset[str] = frozenset()
    if not verdict.insufficient:
        low = frozenset().union(
            *(
                LOW_NUTRIENT_GROUPS.get(nutrient, frozenset())
                for nutrient, band in verdict.bands.items()
                if band == "low"
            )
        )
    return low | _sparse_groups(verdict)


def _sparse_groups(verdict: NutrientVerdict) -> frozenset[str]:
    """최근 7일에 기록된 날의 `MIN_APPEARANCE_SHARE` 보다 적게 나온 채소 · 과일.

    구간(`insufficient`)과 따로 본다 — 1인분 중량이 빈 메뉴로 구간이 막혀도 "나왔는가" 는 센다."""
    if not verdict.appearance_ready:
        return frozenset()
    return frozenset(
        group
        for group in APPEARANCE_DEFICIT_GROUPS
        if verdict.food_group_days.get(group, 0) < verdict.recorded_days * MIN_APPEARANCE_SHARE
    )


async def _recent_meals(context: FoodContext) -> MealRecords:
    """최근 `RECENT_EATEN_DAYS` 일(오늘 포함)의 집 식사와 급식. 영양 판정과 같은 읽기 코드라 기관
    공지에서 온 관찰이 그날 급식과 겹치면 한 번만 센다(N-10). 오늘 급식은 아직 안 먹었어도 오늘
    먹을 식사라 넣고, 내일 이후 급식은 넣지 않는다."""
    today = context.today
    return await read_meal_records(
        context,
        date_from=today - timedelta(days=RECENT_EATEN_DAYS - 1),
        date_to=today,
        daycare_before=today + timedelta(days=1),
    )


def _menu_keys_on(records: MealRecords, days: Iterable[date]) -> set[str]:
    return {
        key
        for day in days
        for key in (
            *(normalize_menu_key(obs.subject) for obs in records.observations.get(day, ())),
            *(normalize_menu_key(k) for row in records.daycare.get(day, ()) for k in row.menu_keys),
        )
        if key
    }


def _eaten_keys(records: MealRecords) -> frozenset[str]:
    """최근 먹은 메뉴 이름(정규화). 이름이 같은 후보만 반복으로 본다 — 재료가 같은 것은 아니다."""
    return frozenset(_menu_keys_on(records, records.days))


async def _today_groups(context: FoodContext, records: MealRecords) -> frozenset[str]:
    """오늘 먹은(기록한) 메뉴와 오늘 급식의 식품군. 카탈로그에 있는 메뉴만 센다."""
    names = _menu_keys_on(records, [context.today])
    if not names:
        return frozenset()
    found = await context.ports.catalog.get_many(tuple(names))
    return frozenset().union(*(row.food_groups for row in found.values()))


def _matches(subject: str, names: Iterable[str]) -> bool:
    key = normalize_menu_key(subject)
    return bool(key) and any(
        (other := normalize_menu_key(name)) and related(key, other) for name in names
    )


def _weighted_order(
    items: Sequence[PoolItem], *, seed: str | None = None, rng: random.Random | None = None
) -> list[PoolItem]:
    """가중치대로 비복원 추출한 순서(Efraimidis–Spirakis). 같은 seed 면 같은 순서다.

    `u ** (1 / w)` 가 큰 순서로 세운다 — 가중치가 크면 앞에 설 확률이 높고 0 은 아니다.
    """
    source = rng or random.Random(seed)
    keyed = [(source.random() ** (1.0 / item.weight), item.row.menu_key, item) for item in items]
    keyed.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item for _, _, item in keyed]
