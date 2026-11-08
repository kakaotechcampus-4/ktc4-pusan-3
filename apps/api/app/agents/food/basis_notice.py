"""식단 추천 안내 줄 — 고른 메뉴가 무엇을 보고 모였는지 코드 문구로 알린다 (#739).

- 영양 반영(`meal.nutrient_basis`) — 고른 메뉴에 적게 나온 채소 · 과일이 들었을 때만. 후보 풀의
  가중치(`meal_pool._deficit_groups`)가 실제로 한 일을 문장으로 옮긴 것이다. 판정을 저장하지
  않아서 가리킬 근거 행이 없고, 원본 행을 근거로 달면 세 추천이 모두 개인화로 집계된다 — 그래서
  근거(`suggestion_evidence`)가 아니라 Food 결과의 안내 줄(readout)이다.
- 레시피 출처(`meal.menu_source`) — 고른 메뉴가 어느 공개 레시피 기관에서 왔는지. 개인화 추천은
  `build()` 가 메뉴 근거를 버려서 그 카드에는 출처가 안 보인다.

둘 다 모델 입력에 넣지 않는다. 코드가 만든 값만 문구 자리에 채운다.
"""

from collections.abc import Mapping, Sequence

from app.agents.common.readout import Readout
from app.agents.food.context import FoodContext, MealPick
from app.agents.food.meal_pool import APPEARANCE_DEFICIT_GROUPS, explainable_groups
from app.agents.food.nutrient_wording import APPEARANCE_GROUPS
from app.agents.food.readouts import MEAL_MENU_SOURCE, MEAL_NUTRIENT_BASIS, READOUTS
from app.agents.food.store.ports import MenuCatalogRow
from app.core.recipe_source import RECIPE_SOURCE_INFO

# 식품군 이름에는 조사가 붙어 있다("채소가" · "과일이") — 문구의 두 `{group}` 자리에 그대로 맞는다
_LABELS: dict[str, str] = dict(APPEARANCE_GROUPS)


def basis_notices(
    context: FoodContext,
    picks: Sequence[MealPick],
    rows_by_key: Mapping[str, MenuCatalogRow],
) -> tuple[Readout, ...]:
    """고른 메뉴의 안내 줄. 순서는 영양 반영 → 출처다. 추천이 없으면 비어 있다.

    `rows_by_key` 는 후보 풀의 카탈로그 행이다. 안전 필터 뒤 재호출이 `state.sampled` 를 덮어써서
    처음 고른 메뉴를 샘플로는 못 찾는다.
    """
    rows = [rows_by_key[pick.menu_key] for pick in picks if pick.menu_key in rows_by_key]
    if not rows:
        return ()
    return (*_nutrient_lines(context, rows), *_source_lines(rows))


def _nutrient_lines(context: FoodContext, rows: Sequence[MenuCatalogRow]) -> tuple[Readout, ...]:
    verdict = context.state.nutrient_verdict
    if verdict is None:
        return ()  # 12개월 미만은 영양 판정을 하지 않는다
    groups = explainable_groups(verdict)
    return tuple(
        READOUTS.render(
            MEAL_NUTRIENT_BASIS,
            days=verdict.recorded_days,
            group=_LABELS[group],
            count=verdict.food_group_days.get(group, 0),
        )
        for group in APPEARANCE_DEFICIT_GROUPS
        if group in groups and any(group in row.food_groups for row in rows)
    )


def _source_lines(rows: Sequence[MenuCatalogRow]) -> tuple[Readout, ...]:
    """고른 메뉴가 온 기관. 이름이 같아 합친 레시피(`merged_from`)의 소스도 넣는다 — 메뉴 근거가
    합쳐진 레시피를 모두 가리키는 것과 같다. 표에 없는 소스(`manual` 등)는 건너뛴다."""
    orgs: dict[str, None] = {}
    for row in rows:
        for source in (row.source, *(merged for merged, _ in row.merged_from)):
            info = RECIPE_SOURCE_INFO.get(source)  # type: ignore[call-overload]
            if info is not None:
                orgs.setdefault(info.org, None)
    if not orgs:
        return ()
    return (READOUTS.render(MEAL_MENU_SOURCE, org=" · ".join(orgs)),)
