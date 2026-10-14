"""영양 판정 — 저장하지 않고 원본(`observation_food` · `daycare_meal`)에서 매번 계산한다.
정본은 `docs/agents/food/영양소_계산_설계.md` §3.

이 파일은 둘로 나뉜다 — 하루 합계(`daily_totals`, 이 Task)와 구간 판정(`evaluate_nutrient_bands`,
다음 Task). 결과는 그 task 의 run state 에만 두고 DB · 로그에 남기지 않는다.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.agents.food.amount_rules import amount_of, portion_scale_for
from app.agents.food.context import FoodContext
from app.agents.food.kdri import NutrientRef, refs_for
from app.agents.food.menu_resolver import MenuResolver, Resolved
from app.agents.food.store.ports import DaycareMealRow, FoodObservation
from app.core.mfds_nutri_raw import OPTIONAL_NUTRIENT_KEYS
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

# "정하는 것 ③" — 해석된 메뉴(영양값이 있는 메뉴)가 전체 메뉴의 이 비율 미만이면 insufficient
MIN_RESOLUTION_RATIO = 0.5

# 1인분 중량이 빈 메뉴(우유 · 원재료성 행)는 합계에 넣지 않는다 — 양을 가정한 숫자를 만들지 않는다.
# 대신 그 메뉴가 표준 1인분(성인 200g)이었다면 더했을 양을 따로 세서, 합계에서 빠진 몫이 큰
# 영양소의 구간을 막는 데만 쓴다(N-16). 알려진 합계가 (알려진 합계 + 빠진 몫)의 이 비율 미만이면
# 구간을 내지 않는다
NOMINAL_SERVING_G = 200.0
MIN_KNOWN_SHARE = 0.5

# AMDR · 상한(%kcal) 항목을 에너지 비율로 바꿀 때 쓰는 열량 환산계수(g → kcal)
_KCAL_PER_GRAM: dict[str, float] = {
    "carbohydrate": 4.0,
    "protein": 4.0,
    "fat": 9.0,
    "sugars": 4.0,
    "saturated_fat": 9.0,
}


# 핵심 영양소(CORE_NUTRIENT_KEYS)는 `core/nutrition_pick._usable()` 이 한 행에 다 있어야만
# 카탈로그 행으로 올린다 — 실제 데이터에서 핵심 키만 빠지는 일은 없다. 당류 · 포화지방(선택
# 칸)만 한 메뉴에서 빠질 수 있어 "섞인" 날을 추적할 대상도 이 둘뿐이다(review focus 2).
_TRACKED_NUTRIENT_KEYS = OPTIONAL_NUTRIENT_KEYS


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
    incomplete: frozenset[str] = frozenset()  # 그날 메뉴 중 하나라도 그 칸을 모르는 영양소 키
    # 영양값은 있는데 1인분 중량이 빈 메뉴가 표준 1인분이었다면 더했을 양. 합계(`grams`)에는 넣지
    # 않고 구간을 낼 수 있는지만 가른다
    unweighed: dict[str, float] = field(default_factory=dict)
    blank_menus: int = 0  # 해석됐지만 영양값이 하나도 없는 메뉴 수 — 얼마나 빠졌는지 알 수 없다
    grouped_menus: int = 0  # 식품군을 아는 메뉴 수 — 찌개 · 국 같은 음식 행은 식품군이 빈다


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


@dataclass(frozen=True)
class MealRecords:
    """날짜별로 묶은 식사 원본. 기관에서 온 중복 관찰은 이미 뺐다.

    키는 기록이 있는 날이다. 관찰이 전부 중복이라 빈 목록이 된 날도 키로 남는다.
    """

    observations: dict[date, list[FoodObservation]]
    daycare: dict[date, list[DaycareMealRow]]

    @property
    def days(self) -> list[date]:
        return sorted({*self.daycare, *self.observations})


async def read_meal_records(
    context: FoodContext,
    *,
    date_from: date,
    date_to: date,
    daycare_before: date | None = None,
) -> MealRecords:
    """`observation_food` 와 `daycare_meal` 을 읽어 날짜별로 묶고 기관 중복(N-10)을 뺀다.

    영양 판정(`daily_totals`)과 기록 tool(`analyze_meal_records` · `check_repeated_menus`)이 같은
    코드를 쓴다 — 기관 중복을 두 곳에서 따로 세지 않는다.

    `daycare_before` 를 주면 그날 이전 급식만 읽는다. 오늘 급식은 앞으로 나올 식단이라 먹은 기록이
    아니다. 급식을 자른 뒤에 중복을 보므로, 오늘 알림장이 전한 식사는 급식과 겹치지 않고 남는다.
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
        if daycare_before is None or row.serve_date < daycare_before:
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
    return MealRecords(observations=kept_observations_by_day, daycare=dict(daycare_by_day))


async def daily_totals(context: FoodContext, *, date_from: date, date_to: date) -> list[DayTotals]:
    """원본에서 날짜별 영양 합계를 만든다. 기록이 있는 날만 돌려준다 (설계 §1-3).

    흐름 (설계 §3 ①~③): 원본 두 번 읽기 → 기관 중복 관찰 빼기(`read_meal_records`) → 메뉴를 한
    번에 조회·해석 → 날짜별로 양 가중 · 유아 보정 계수를 적용해 더한다.
    """
    ports = context.ports
    records = await read_meal_records(context, date_from=date_from, date_to=date_to)
    daycare_by_day = records.daycare
    kept_observations_by_day = records.observations

    days = records.days
    if not days:
        return []

    names: list[str] = []
    for day in days:
        names.extend(obs.subject for obs in kept_observations_by_day.get(day, ()))
        for row in daycare_by_day.get(day, ()):
            names.extend(row.menu_keys)

    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    group = select_kdri_group(months_between(birth_date, context.today))

    resolver = MenuResolver(catalog=ports.catalog, source=ports.menu_source, now=context.now)
    resolutions = await resolver.resolve_many(names)

    totals: list[DayTotals] = []
    for day in days:
        grams: dict[str, float] = {}
        unweighed: dict[str, float] = {}
        food_groups: set[str] = set()
        incomplete: set[str] = set()
        menus = 0
        resolved_menus = 0
        weighed_menus = 0
        blank_menus = 0
        grouped_menus = 0

        def _apply(menu_name: str, factor: float, known: bool, *, disliked: bool = False) -> None:
            nonlocal menus, resolved_menus, weighed_menus, blank_menus, grouped_menus
            menus += 1
            resolution = resolutions.get(normalize_menu_key(menu_name))
            if not isinstance(resolution, Resolved):
                return
            resolved_menus += 1
            row = resolution.row
            if row.food_groups:
                grouped_menus += 1
            # 출현은 먹은 것만 센다 — 안 먹음 · 뱉음(양 0)이나 싫어한다는 관찰은 "나온 날" 이 아니다
            if not disliked and amount_weight(factor, known=known) > 0:
                food_groups.update(row.food_groups)
            if not row.nutrients:
                blank_menus += 1
                return
            scale = portion_scale_for(group, row.food_groups)
            if row.serving_g is None:
                standard = NOMINAL_SERVING_G * scale * amount_weight(factor, known=known)
                for nutrient, per_100g in row.nutrients.items():
                    unweighed[nutrient] = unweighed.get(nutrient, 0.0) + per_100g * standard / 100
                return
            weighed_menus += 1
            # 이 메뉴가 모르는 칸은 그날 전체를 그 항목에서 빼야 한다 — 아는 메뉴만으로 합계를
            # 내면 모르는 메뉴를 0 으로 채우는 것과 같아진다(리뷰 초점 2, 설계 §3)
            incomplete.update(key for key in _TRACKED_NUTRIENT_KEYS if key not in row.nutrients)
            eaten_grams = row.serving_g * scale * amount_weight(factor, known=known)
            for nutrient, per_100g in row.nutrients.items():
                grams[nutrient] = grams.get(nutrient, 0.0) + per_100g * eaten_grams / 100

        for obs in kept_observations_by_day.get(day, ()):
            rule = amount_of(obs.amount_text)
            _apply(obs.subject, rule.factor, rule.known, disliked=obs.polarity < 0)
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
                incomplete=frozenset(incomplete),
                unweighed=unweighed,
                blank_menus=blank_menus,
                grouped_menus=grouped_menus,
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
    # 식품군 → 최근 7일 중 나온 날 수(나온 식품군만). 1인분 중량과 무관하게 센다
    food_group_days: dict[str, int] = field(default_factory=dict)
    recorded_days: int = 0  # 최근 7일 중 기록이 있는 날 수 — 출현 문장의 분모
    # 출현을 말해도 되는가. 구간(`insufficient`)과 따로 본다 — 구간은 1인분 중량이 빈 메뉴에도
    # 막히지만 출현은 "나왔는가" 라서 기록 행 수(N-1) · 메뉴 해석률만 본다(N-17)
    appearance_ready: bool = False


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
    total_blank = sum(dt.blank_menus for dt in current_totals)
    resolved_ratio = total_resolved / total_menus if total_menus else 0.0
    # 해석은 됐어도 영양값이 하나도 없는 메뉴는 얼마나 빠졌는지 알 수 없어 해석 안 된 것과 같게 센다
    quantified_ratio = (total_resolved - total_blank) / total_menus if total_menus else 0.0
    food_groups: frozenset[str] = frozenset().union(*(dt.food_groups for dt in current_totals))
    food_group_days = dict(Counter(group for dt in current_totals for group in dt.food_groups))
    recorded_days = sum(1 for dt in current_totals if dt.records > 0)
    # 식품군을 모르는 메뉴가 많으면 "0일" 은 안 나온 게 아니라 모르는 것이다 — 해석률과 같게 본다
    grouped_ratio = (
        sum(dt.grouped_menus for dt in current_totals) / total_menus if total_menus else 0.0
    )
    appearance_ready = (
        not is_insufficient(records)
        and resolved_ratio >= MIN_RESOLUTION_RATIO
        and grouped_ratio >= MIN_RESOLUTION_RATIO
    )

    if (
        is_insufficient(records)
        or resolved_ratio < MIN_RESOLUTION_RATIO
        or quantified_ratio < MIN_RESOLUTION_RATIO
        or _known_share("energy", current_totals) < MIN_KNOWN_SHARE
    ):
        verdict = NutrientVerdict(
            bands={},
            unscored=(),
            records=records,
            resolved_ratio=resolved_ratio,
            insufficient=True,
            food_groups=food_groups,
            food_group_days=food_group_days,
            recorded_days=recorded_days,
            appearance_ready=appearance_ready,
        )
        context.state.nutrient_verdict = verdict
        return verdict

    bands: dict[str, NutrientBand] = {}
    unscored: list[str] = []
    for nutrient, ref in refs_for(group).items():
        if nutrient == "energy":
            continue  # 이번 범위 밖 — 권장 열량 계산이 붙을 때 쓴다(설계 §2)
        if _known_share(nutrient, current_totals) < MIN_KNOWN_SHARE:
            unscored.append(nutrient)  # 중량이 빈 메뉴가 이 영양소의 큰 몫이라 합계가 실제보다 작다
            continue
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
        food_group_days=food_group_days,
        recorded_days=recorded_days,
        appearance_ready=appearance_ready,
    )
    context.state.nutrient_verdict = verdict
    return verdict


def _known_share(nutrient: str, totals: list[DayTotals]) -> float:
    """합계에 들어간 양이 (들어간 양 + 중량이 비어 못 넣은 몫)에서 차지하는 비율. 못 넣은 몫이
    없으면 1. 구간을 내도 되는가를 가르는 데만 쓰고, 못 넣은 몫은 어디에도 내보내지 않는다."""
    known = sum(dt.grams.get(nutrient, 0.0) for dt in totals)
    missing = sum(dt.unweighed.get(nutrient, 0.0) for dt in totals)
    return known / (known + missing) if known + missing > 0 else 1.0


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
        # 다시 판정하는 앞 창에서 중량이 빈 몫이 크면 그 창의 거짓 low 가 히스테리시스로 남는다
        if _known_share(nutrient, [by_day[d] for d in window if d in by_day]) < MIN_KNOWN_SHARE:
            return None
        mean = _window_mean(nutrient, by_day, window)
        if mean is None:
            return None
        daily_means.append(mean)
    return settle_ear_band(daily_means, ear=ref.ear)


def _window_mean(nutrient: str, by_day: dict[date, DayTotals], days: list[date]) -> float | None:
    # 그날 메뉴 중 하나라도 이 영양소 칸을 몰랐으면(incomplete) 그 날은 뺀다 — 아는 메뉴만의
    # 부분합을 "그날 전체"로 쓰면 모르는 메뉴를 0 으로 채우는 것과 같아진다(review focus 2)
    values = {
        d: by_day[d].grams[nutrient]
        for d in days
        if d in by_day and nutrient in by_day[d].grams and nutrient not in by_day[d].incomplete
    }
    return mean_over_recorded_days(values)
