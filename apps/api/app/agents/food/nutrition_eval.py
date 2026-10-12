"""영양 판정 — 저장하지 않고 원본(`observation_food` · `daycare_meal`)에서 매번 계산한다.
정본은 `docs/agents/food/영양소_계산_설계.md` §3.

이 파일은 둘로 나뉜다 — 하루 합계(`daily_totals`, 이 Task)와 구간 판정(`evaluate_nutrient_bands`,
다음 Task). 결과는 그 task 의 run state 에만 두고 DB · 로그에 남기지 않는다.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from app.agents.food.amount_rules import amount_of, portion_scale_for
from app.agents.food.context import FoodContext
from app.agents.food.menu_resolver import MenuResolver, Resolved
from app.agents.food.store.ports import DaycareMealRow, FoodObservation
from app.rules.age import months_between
from app.rules.menu_key import normalize_menu_key
from app.rules.nutrition import amount_weight, select_kdri_group


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
