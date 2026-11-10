"""식단 추천 안내 줄 — 고른 메뉴가 무엇을 보고 모였는지 코드 문구로 알린다 (#739 · #740).

- 영양 반영(`meal.nutrient_basis`) — 고른 메뉴에 적게 나온 채소 · 과일이 들었을 때만. 후보 풀의
  가중치(`meal_pool._deficit_groups`)가 실제로 한 일을 문장으로 옮긴 것이다. 판정을 저장하지
  않아서 가리킬 근거 행이 없고, 원본 행을 근거로 달면 세 추천이 모두 개인화로 집계된다 — 그래서
  근거(`suggestion_evidence`)가 아니라 Food 결과의 안내 줄(readout)이다.
- 메뉴 출처 — 추천이 있으면 한 줄. 고른 메뉴가 레시피북 메뉴뿐이면 어느 공개 레시피 기관에서 왔는지
  (`meal.menu_source`), 레시피북에 없는 생성 메뉴뿐이면 그렇다고(`meal.menu_generated`), 섞였으면
  생성 메뉴 이름과 나머지의 기관(`meal.menu_mixed`). 개인화 추천은 `build()` 가 메뉴 근거를 버려서
  그 카드에는 출처가 안 보인다.
- 재료 확인(`check.ingredients_caveat`) — 등록한 알레르기가 있는 아이에게 생성 메뉴가 나갈 때만.
  생성 메뉴의 재료는 모델이 쓴 것이라 검수된 레시피 재료와 같은 무게로 믿을 수 없다.

생성 메뉴는 카탈로그 행이 없다 — 풀의 행(`rows_by_key`)에 없는 메뉴가 생성 메뉴다. 식품군을 지어내지
않으므로 영양 줄 대상이 아니다. 모두 모델 입력에 넣지 않고 코드가 만든 값만 문구 자리에 채운다.
"""

from collections.abc import Mapping, Sequence

from app.agents.common.readout import Readout
from app.agents.food.context import FoodContext, MealPick, require_safety
from app.agents.food.meal_pool import APPEARANCE_DEFICIT_GROUPS, explainable_groups
from app.agents.food.nutrient_wording import APPEARANCE_GROUPS
from app.agents.food.readouts import (
    CHECK_INGREDIENTS_CAVEAT,
    MEAL_MENU_GENERATED,
    MEAL_MENU_MIXED,
    MEAL_MENU_SOURCE,
    MEAL_MENU_WEANING,
    MEAL_NUTRIENT_BASIS,
    READOUTS,
)
from app.agents.food.store.ports import MenuCatalogRow
from app.agents.food.weaning_menu import WEANING_MENU_SOURCE
from app.core.recipe_source import RECIPE_SOURCE_INFO
from app.rules.particle import topic_particle

# 식품군 이름에는 조사가 붙어 있다("채소가" · "과일이") — 문구의 두 `{group}` 자리에 그대로 맞는다
_LABELS: dict[str, str] = dict(APPEARANCE_GROUPS)


def basis_notices(
    context: FoodContext,
    picks: Sequence[MealPick],
    rows_by_key: Mapping[str, MenuCatalogRow],
) -> tuple[Readout, ...]:
    """고른 메뉴의 안내 줄. 순서는 영양 반영 → 출처 → 재료 확인이다. 추천이 없으면 비어 있다.

    `rows_by_key` 는 후보 풀의 카탈로그 행이다. 안전 필터 뒤 재호출이 `state.sampled` 를 덮어써서
    처음 고른 메뉴를 샘플로는 못 찾는다. 여기에 없는 메뉴가 레시피북 밖 생성 메뉴다.
    """
    rows = [rows_by_key[pick.menu_key] for pick in picks if pick.menu_key in rows_by_key]
    generated = [pick.draft.content for pick in picks if pick.menu_key not in rows_by_key]
    if not rows and not generated:
        return ()
    return (
        *_nutrient_lines(context, rows),
        *_source_lines(rows, generated),
        *_caveat_lines(context, generated),
    )


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


def _source_lines(rows: Sequence[MenuCatalogRow], generated: Sequence[str]) -> tuple[Readout, ...]:
    """고른 메뉴가 온 곳. 이름이 같아 합친 레시피(`merged_from`)의 소스도 넣는다 — 메뉴 근거가
    합쳐진 레시피를 모두 가리키는 것과 같다. 표에 없는 소스(`manual` 등)는 건너뛴다.

    이유식 식단 표 메뉴(이유기, #399)가 하나라도 있으면 그 줄 하나다 — 이유기 풀은 식단 표뿐이라
    레시피 · 생성 메뉴와 섞이지 않는다."""
    if any(row.source == WEANING_MENU_SOURCE for row in rows):
        return (READOUTS.render(MEAL_MENU_WEANING),)
    orgs: dict[str, None] = {}
    for row in rows:
        for source in (row.source, *(merged for merged, _ in row.merged_from)):
            info = RECIPE_SOURCE_INFO.get(source)  # type: ignore[call-overload]
            if info is not None:
                orgs.setdefault(info.org, None)
    if generated and orgs:
        return (
            READOUTS.render(
                MEAL_MENU_MIXED,
                menus=", ".join(generated),
                은는=topic_particle(generated[-1]),
                org=" · ".join(orgs),
            ),
        )
    if generated:
        return (READOUTS.render(MEAL_MENU_GENERATED),)
    if orgs:
        return (READOUTS.render(MEAL_MENU_SOURCE, org=" · ".join(orgs)),)
    return ()


def _caveat_lines(context: FoodContext, generated: Sequence[str]) -> tuple[Readout, ...]:
    """등록한 알레르기가 있는 아이에게 생성 메뉴가 나갈 때만 재료를 다시 확인하라고 알린다."""
    if not generated:
        return ()
    if not any(e.kind == "allergy" and e.status == "active" for e in require_safety(context)):
        return ()
    return (READOUTS.render(CHECK_INGREDIENTS_CAVEAT),)
