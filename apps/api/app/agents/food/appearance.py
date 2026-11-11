"""식품군 출현 규칙 — 식단 추천 가중(`meal_pool`)과 영양소 분석 문장(`nutrient_wording`)이 쓴다.

두 곳이 같은 기준으로 "적게 나왔다" 를 말해야 해서 한 곳에 둔다. `nutrition_eval` 이
`nutrient_wording` 을 import 하므로 이 모듈은 판정(`NutrientVerdict`)을 타입으로만 본다.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.food.nutrition_eval import NutrientVerdict

# 영양 구간에 대응하는 항목이 없는 식품군 — 출현 창 중 나온 날 수로 적게 나왔는지를 본다(N-19).
# 기록된 날의 이 비율보다 적게 나오면 부족 식품군에 더한다
APPEARANCE_DEFICIT_GROUPS: tuple[str, ...] = ("vegetables", "fruits")
MIN_APPEARANCE_SHARE = 0.5


def sparse_groups(verdict: "NutrientVerdict") -> frozenset[str]:
    """출현 창에 기록된 날의 `MIN_APPEARANCE_SHARE` 보다 적게 나온 채소 · 과일.
    식단 추천 가중이 쓴다.

    확인한 날만 센다 — 식품군을 모르는 날은 "안 나왔다" 로 친다(`explainable_groups` 와 짝).
    가중의 세기는 여기서 정하지 않는다(식품군 집합만 — 세기는 `meal_pool._deficit_groups`).
    구간(`insufficient`)과 따로 본다 — 1인분 중량이 빈 메뉴로 구간이 막혀도 "나왔는가" 는 센다.
    """
    if not verdict.appearance_ready:
        return frozenset()
    return frozenset(
        group
        for group in APPEARANCE_DEFICIT_GROUPS
        if verdict.food_group_days.get(group, 0) < verdict.recorded_days * MIN_APPEARANCE_SHARE
    )


def explainable_groups(verdict: "NutrientVerdict") -> frozenset[str]:
    """보호자에게 "적게 나왔다" 고 말해도 되는 채소 · 과일.

    가중(`sparse_groups`)은 확인한 날만 센다. 문장은 식품군을 모르는 메뉴가 낀 날을 전부
    "나왔다" 고 쳐도 기록된 날의 `MIN_APPEARANCE_SHARE` 미만일 때만 낸다 — 가중은 틀려도 후보를
    막지 않지만 문장은 틀린 숫자로 읽힌다(N-19). 모르는 날을 더해도 결론이 안 뒤집히는 식품군만
    남는다.
    """
    if not verdict.appearance_ready:
        return frozenset()
    return frozenset(
        group
        for group in APPEARANCE_DEFICIT_GROUPS
        if verdict.food_group_days.get(group, 0) + verdict.group_unknown_days.get(group, 0)
        < verdict.recorded_days * MIN_APPEARANCE_SHARE
    )
