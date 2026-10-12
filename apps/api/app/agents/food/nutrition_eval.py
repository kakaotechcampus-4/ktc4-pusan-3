"""영양 판정 — 저장하지 않고 원본(`observation_food` · `daycare_meal`)에서 매번 계산한다.
정본은 `docs/agents/food/영양소_계산_설계.md` §3.

이 파일은 둘로 나뉜다 — 하루 합계(`daily_totals`, 이 Task)와 구간 판정(`evaluate_nutrient_bands`,
다음 Task). 결과는 그 task 의 run state 에만 두고 DB · 로그에 남기지 않는다.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from app.agents.food.amount_rules import amount_of, portion_scale_for
from app.agents.food.context import FoodContext
from app.agents.food.kdri import NutrientRef, refs_for
from app.agents.food.menu_resolver import MenuResolver, Resolved
from app.agents.food.store.ports import DaycareMealRow, FoodObservation
from app.rules.age import months_between
from app.rules.menu_key import normalize_menu_key
from app.rules.nutrition import (
    NutrientBand,
    amount_weight,
    band_range,
    band_upper,
    energy_share,
    is_insufficient,
    mean_over_recorded_days,
    select_kdri_group,
    settle_ear_band,
)

# 판정 창(최근 7일) + 다시 판정 창(그 앞 7일) = 14일. 전날 구간은 저장하지 않고 다시 판정해서
# 얻는다(설계 §3 ④) — 그래서 히스테리시스가 필요한 ear-kind 항목만 14일 전부를 쓴다
_WINDOW_DAYS = 7
_LOOKBACK_DAYS = 14

# "정하는 것 ③" — 합계에 들어간 메뉴(또는 해석된 메뉴)가 전체 메뉴의 이 비율 미만이면 insufficient
MIN_RESOLUTION_RATIO = 0.5

# AMDR · 상한(%kcal) 항목을 에너지 비율로 바꿀 때 쓰는 열량 환산계수(g → kcal)
_KCAL_PER_GRAM: dict[str, float] = {
    "carbohydrate": 4.0,
    "protein": 4.0,
    "fat": 9.0,
    "sugars": 4.0,
    "saturated_fat": 9.0,
}


@dataclass(frozen=True)
class DayTotals:
    """하루치 합계. 저장하지 않고 판정할 때마다 다시 만든다 (설계 §3)."""

    day: date
    grams: dict[str, float]  # 영양소 키 → 그날 섭취량. 단위는 영양소마다 다르다(mg · g · kcal)
    food_groups: frozenset[str]
    records: int  # observation_food · daycare_meal 의 행 수(기관 중복 제외 후, N-10)
    menus: int  # 그날 언급된 메뉴 수 — 급식은 한 행에 메뉴가 여럿일 수 있다
    resolved_menus: int  # 메뉴 카탈로그에서 찾은 메뉴 수(영양값 유무와 무관)
    weighed_menus: int  # 1인분 중량 · 영양값이 다 있어 영양 합계에 실제로 들어간 메뉴 수


def _is_institutional_duplicate(obs: FoodObservation, daycare_menu_keys: frozenset[str]) -> bool:
    """그날 급식이 있을 때 기관 출처로 보이는 관찰인가 (설계 §3 N-10).

    알림장 · 기관 공지 출처는 늘 중복이다. 전해 들은 말은 그날 급식 메뉴를 가리킬 때만 중복이다 —
    급식에 없는 메뉴(할머니 댁 등)는 집 식사로 남는다.
    """
    if obs.confidence_source == "institution_notice":
        return True
    if obs.source_notice_id is not None:
        return True
    if obs.confidence_source == "parent_hearsay":
        return normalize_menu_key(obs.subject) in daycare_menu_keys
    return False


async def daily_totals(context: FoodContext, *, date_from: date, date_to: date) -> list[DayTotals]:
    """원본에서 날짜별 영양 합계를 만든다. 기록이 있는 날만 돌려준다 (설계 §1-3).

    흐름 (설계 §3 ①~③): 원본 두 번 읽기 → 기관 중복 관찰 빼기 → 메뉴를 한 번에 조회·해석 →
    날짜별로 양 가중 · 유아 보정 계수를 적용해 더한다.
    """
    ports = context.ports
    observations = await ports.memory.observations(
        child_id=context.child_id, date_from=date_from, date_to=date_to
    )
    daycare_rows = await ports.daycare.rows(
        child_id=context.child_id, date_from=date_from, date_to=date_to
    )

    daycare_by_day: dict[date, list[DaycareMealRow]] = defaultdict(list)
    for row in daycare_rows:
        daycare_by_day[row.serve_date].append(row)

    observations_by_day: dict[date, list[FoodObservation]] = defaultdict(list)
    for obs in observations:
        observations_by_day[obs.observed_on].append(obs)

    kept_observations_by_day: dict[date, list[FoodObservation]] = {}
    for day, day_obs in observations_by_day.items():
        today_daycare = daycare_by_day.get(day, ())
        if not today_daycare:
            kept_observations_by_day[day] = day_obs
            continue
        daycare_menu_keys = frozenset(key for row in today_daycare for key in row.menu_keys)
        kept_observations_by_day[day] = [
            obs for obs in day_obs if not _is_institutional_duplicate(obs, daycare_menu_keys)
        ]

    days = sorted({*daycare_by_day, *kept_observations_by_day})
    if not days:
        return []

    names: list[str] = []
    for day in days:
        names.extend(obs.subject for obs in kept_observations_by_day.get(day, ()))
        for row in daycare_by_day.get(day, ()):
            names.extend(row.menu_keys)

    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    group = select_kdri_group(months_between(birth_date, context.today))
    scale = portion_scale_for(group)

    resolver = MenuResolver(catalog=ports.catalog, source=ports.menu_source, now=context.now)
    resolutions = await resolver.resolve_many(names)

    totals: list[DayTotals] = []
    for day in days:
        grams: dict[str, float] = {}
        food_groups: set[str] = set()
        menus = 0
        resolved_menus = 0
        weighed_menus = 0

        def _apply(menu_name: str, factor: float, known: bool) -> None:
            nonlocal menus, resolved_menus, weighed_menus
            menus += 1
            resolution = resolutions.get(normalize_menu_key(menu_name))
            if not isinstance(resolution, Resolved):
                return
            resolved_menus += 1
            row = resolution.row
            food_groups.update(row.food_groups)
            if row.serving_g is None or not row.nutrients:
                return
            weighed_menus += 1
            eaten_grams = row.serving_g * scale * amount_weight(factor, known=known)
            for nutrient, per_100g in row.nutrients.items():
                grams[nutrient] = grams.get(nutrient, 0.0) + per_100g * eaten_grams / 100

        for obs in kept_observations_by_day.get(day, ()):
            rule = amount_of(obs.amount_text)
            _apply(obs.subject, rule.factor, rule.known)
        for row in daycare_by_day.get(day, ()):
            for menu_key in row.menu_keys:
                _apply(menu_key, row.amount_factor, row.amount_known)

        records = len(kept_observations_by_day.get(day, ())) + len(daycare_by_day.get(day, ()))
        totals.append(
            DayTotals(
                day=day,
                grams=grams,
                food_groups=frozenset(food_groups),
                records=records,
                menus=menus,
                resolved_menus=resolved_menus,
                weighed_menus=weighed_menus,
            )
        )
    return totals


@dataclass(frozen=True)
class NutrientVerdict:
    """영양 구간 판정 결과. 저장하지 않고 `context.state.nutrient_verdict` 에만 둔다 (설계 §3 ⑤)."""

    bands: dict[str, NutrientBand]
    unscored: tuple[str, ...]  # 판정 기간에 빈 칸이 있어 구간을 내지 못한 항목
    records: int  # 최근 7일(판정 창) 행 수
    resolved_ratio: float  # 최근 7일 메뉴 해석률
    insufficient: bool
    food_groups: frozenset[str]  # 최근 7일에 나온 식품군


async def evaluate_nutrient_bands(context: FoodContext, *, months: int) -> NutrientVerdict:
    """항목별 영양 구간. 기록 행 수 · 해석률이 임계 아래면 구간 없이 `insufficient` 만 돌려준다.

    흐름 (설계 §3 ③~⑤): 14일 치 하루 합계를 받아 최근 7일로 기록·해석 충분성을 먼저 보고,
    충분하면 항목마다 구간을 매긴다 — EAR 항목은 7일 전부터 하루씩 다시 판정해 히스테리시스를
    지키고, AMDR · 상한 항목은 최근 7일 창 하나의 평균만 본다.
    """
    group = select_kdri_group(months)  # 12개월 미만이면 여기서 ValueError

    window_end = context.today - timedelta(days=1)  # "정하는 것 ①" — 오늘 이전
    window_start = window_end - timedelta(days=_WINDOW_DAYS - 1)
    lookback_start = window_end - timedelta(days=_LOOKBACK_DAYS - 1)

    day_totals = await daily_totals(context, date_from=lookback_start, date_to=window_end)
    by_day = {dt.day: dt for dt in day_totals}

    current_days = [window_start + timedelta(days=i) for i in range(_WINDOW_DAYS)]
    current_totals = [by_day[d] for d in current_days if d in by_day]

    records = sum(dt.records for dt in current_totals)
    total_menus = sum(dt.menus for dt in current_totals)
    total_resolved = sum(dt.resolved_menus for dt in current_totals)
    total_weighed = sum(dt.weighed_menus for dt in current_totals)
    resolved_ratio = total_resolved / total_menus if total_menus else 0.0
    weighed_ratio = total_weighed / total_menus if total_menus else 0.0
    food_groups: frozenset[str] = frozenset().union(*(dt.food_groups for dt in current_totals))

    if (
        is_insufficient(records)
        or resolved_ratio < MIN_RESOLUTION_RATIO
        or weighed_ratio < MIN_RESOLUTION_RATIO
    ):
        return NutrientVerdict(
            bands={},
            unscored=(),
            records=records,
            resolved_ratio=resolved_ratio,
            insufficient=True,
            food_groups=food_groups,
        )

    bands: dict[str, NutrientBand] = {}
    unscored: list[str] = []
    for nutrient, ref in refs_for(group).items():
        if nutrient == "energy":
            continue  # 이번 범위 밖 — 권장 열량 계산이 붙을 때 쓴다(설계 §2)
        band = _band_for(nutrient, ref, by_day, current_days)
        if band is None:
            unscored.append(nutrient)
        else:
            bands[nutrient] = band

    verdict = NutrientVerdict(
        bands=bands,
        unscored=tuple(unscored),
        records=records,
        resolved_ratio=resolved_ratio,
        insufficient=False,
        food_groups=food_groups,
    )
    context.state.nutrient_verdict = verdict
    return verdict


def _band_for(
    nutrient: str, ref: NutrientRef, by_day: dict[date, DayTotals], current_days: list[date]
) -> NutrientBand | None:
    """한 항목의 구간. EAR 항목만 히스테리시스(7일 전부터 다시 판정)가 필요하다."""
    if ref.kind == "ear":
        return _ear_band(ref, nutrient, by_day, current_days)

    mean = _window_mean(nutrient, by_day, current_days)
    if mean is None:
        return None
    if ref.unit == "%kcal":
        energy_mean = _window_mean("energy", by_day, current_days)
        if energy_mean is None:
            return None
        value = energy_share(mean, _KCAL_PER_GRAM[nutrient], energy_mean)
        if value is None:
            return None
    else:
        value = mean

    if ref.kind == "amdr":
        return band_range(value, ref.low, ref.high)
    return band_upper(value, ref.upper)  # kind == "upper"


def _ear_band(
    ref: NutrientRef, nutrient: str, by_day: dict[date, DayTotals], current_days: list[date]
) -> NutrientBand | None:
    """EAR 기준 구간. 전날 구간을 모르므로 7일 전부터 하루씩 다시 판정해서 얻는다 (설계 §3 ④).

    미량영양소는 상한을 넘어도 `high` 로 보고하지 않는다(설계 §2 "과잉을 말하는 항목은 나트륨 ·
    당류 · 포화지방으로 제한한다") — `ref.upper` 는 여기서 쓰지 않는다.
    """
    daily_means: list[float] = []
    for end_day in current_days:
        window = [end_day - timedelta(days=i) for i in range(_WINDOW_DAYS)]
        mean = _window_mean(nutrient, by_day, window)
        if mean is None:
            return None
        daily_means.append(mean)
    return settle_ear_band(daily_means, ear=ref.ear)


def _window_mean(nutrient: str, by_day: dict[date, DayTotals], days: list[date]) -> float | None:
    values = {
        d: by_day[d].grams[nutrient] for d in days if d in by_day and nutrient in by_day[d].grams
    }
    return mean_over_recorded_days(values)
