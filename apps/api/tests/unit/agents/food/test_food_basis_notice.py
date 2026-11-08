"""식단 추천 안내 줄 — 영양 반영(`meal.nutrient_basis`)과 레시피 출처(`meal.menu_source`) (#739).

두 줄 다 코드 문구다. 고른 메뉴와 run 이 읽은 영양 판정에서만 만들고 `suggestion_evidence` 는
건드리지 않는다. 메뉴 · 기록 · 아이는 전부 가짜다.
"""

import pytest

from app.agents.food.basis_notice import basis_notices
from app.agents.food.context import FoodContext, MealPick
from app.agents.food.nutrient_wording import wording_problem
from app.agents.food.nutrition_eval import NutrientVerdict
from app.agents.food.schemas.common import MealSlot
from app.agents.food.store import MenuCatalogRow
from tests.unit.agents.food.support import context, menu_row

VEG = frozenset({"vegetables"})
FRUIT = frozenset({"fruits"})


def verdict(
    *,
    group_days: dict[str, int] | None = None,
    unknown_days: dict[str, int] | None = None,
    recorded_days: int = 6,
    ready: bool = True,
) -> NutrientVerdict:
    return NutrientVerdict(
        bands={},
        unscored=(),
        records=12,
        resolved_ratio=1.0,
        insufficient=False,
        food_groups=frozenset(group_days or ()),
        food_group_days=dict(group_days or {}),
        recorded_days=recorded_days,
        group_unknown_days=dict(unknown_days or {}),
        appearance_ready=ready,
    )


def pick(key: str) -> MealPick:
    # 안내 줄은 메뉴 키만 읽는다 — 초안은 필요 없다
    return MealPick(draft=None, menu_key=key, meal_slot=MealSlot.DINNER)  # type: ignore[arg-type]


def ctx(result: NutrientVerdict | None) -> FoodContext:
    made = context(safety=())
    made.state.nutrient_verdict = result
    return made


def rows_of(*rows: MenuCatalogRow) -> dict[str, MenuCatalogRow]:
    return {row.menu_key: row for row in rows}


def bodies(readouts) -> list[str]:
    return [readout.body for readout in readouts]


SPINACH = menu_row("시금치나물", source="mafra_recipe", food_groups=VEG)
APPLE = menu_row("사과조림", source="mafra_recipe", food_groups=FRUIT)
TOFU = menu_row("두부조림", source="mafra_recipe")
SPARSE = verdict(group_days={"vegetables": 2, "fruits": 6})  # 기록된 6일 중 채소 2일


class TestNutrientLine:
    def test_적게_나온_채소가_든_메뉴를_골랐으면_한_줄을_낸다(self) -> None:
        result = basis_notices(ctx(SPARSE), [pick("시금치나물")], rows_of(SPINACH))

        assert (
            bodies(result)[0]
            == "기록된 6일 중 채소가 나온 날은 2일이라, 채소가 든 메뉴를 더 넣었어요."
        )

    def test_숫자는_영양소_분석과_같은_식품군_출현_일수다(self) -> None:
        sparse = verdict(group_days={"vegetables": 1, "fruits": 6}, recorded_days=5)

        body = bodies(basis_notices(ctx(sparse), [pick("시금치나물")], rows_of(SPINACH)))[0]

        assert "기록된 5일" in body and "나온 날은 1일" in body

    def test_채소가_든_메뉴가_없으면_영양_줄이_없다(self) -> None:
        result = basis_notices(ctx(SPARSE), [pick("두부조림")], rows_of(TOFU))

        assert not [b for b in bodies(result) if "더 넣었어요" in b]

    def test_충분히_나온_식품군은_말하지_않는다(self) -> None:
        enough = verdict(group_days={"vegetables": 5, "fruits": 6})

        result = basis_notices(ctx(enough), [pick("시금치나물")], rows_of(SPINACH))

        assert not [b for b in bodies(result) if "더 넣었어요" in b]

    def test_모르는_날을_다_나왔다고_치면_절반이_되는_식품군은_말하지_않는다(self) -> None:
        unsure = verdict(group_days={"vegetables": 2, "fruits": 6}, unknown_days={"vegetables": 1})

        result = basis_notices(ctx(unsure), [pick("시금치나물")], rows_of(SPINACH))

        assert not [b for b in bodies(result) if "더 넣었어요" in b]

    def test_채소와_과일이_둘_다_적으면_채소_과일_순서로_두_줄이다(self) -> None:
        both = verdict(group_days={"vegetables": 1, "fruits": 2})

        result = basis_notices(
            ctx(both), [pick("사과조림"), pick("시금치나물")], rows_of(SPINACH, APPLE)
        )

        nutrient = [b for b in bodies(result) if "더 넣었어요" in b]
        assert nutrient == [
            "기록된 6일 중 채소가 나온 날은 1일이라, 채소가 든 메뉴를 더 넣었어요.",
            "기록된 6일 중 과일이 나온 날은 2일이라, 과일이 든 메뉴를 더 넣었어요.",
        ]

    def test_판정이_없으면_영양_줄이_없다(self) -> None:
        # 12개월 미만은 영양 판정을 하지 않아 run state 에 판정이 없다
        result = basis_notices(ctx(None), [pick("시금치나물")], rows_of(SPINACH))

        assert bodies(result) == ["추천 메뉴는 농식품정보원 공개 레시피 중 재료가 검수된 메뉴예요."]

    def test_출현을_말할_수_없는_판정이면_영양_줄이_없다(self) -> None:
        result = basis_notices(
            ctx(verdict(group_days={}, ready=False)), [pick("시금치나물")], rows_of(SPINACH)
        )

        assert not [b for b in bodies(result) if "더 넣었어요" in b]


class TestSourceLine:
    def test_추천이_있으면_출처_줄이_한_줄이다(self) -> None:
        result = basis_notices(ctx(None), [pick("시금치나물")], rows_of(SPINACH))

        assert bodies(result) == ["추천 메뉴는 농식품정보원 공개 레시피 중 재료가 검수된 메뉴예요."]

    def test_추천이_없으면_아무_줄도_없다(self) -> None:
        assert basis_notices(ctx(SPARSE), [], {}) == ()

    def test_기관이_둘이면_처음_나온_순서로_잇는다(self) -> None:
        nics = menu_row("애호박볶음", source="nics_recipe")

        result = basis_notices(
            ctx(None), [pick("애호박볶음"), pick("시금치나물")], rows_of(SPINACH, nics)
        )

        assert bodies(result) == [
            "추천 메뉴는 농촌진흥청 국립식량과학원 · 농식품정보원 "
            "공개 레시피 중 재료가 검수된 메뉴예요."
        ]

    def test_같은_기관의_메뉴가_여럿이어도_기관은_한_번이다(self) -> None:
        result = basis_notices(
            ctx(None), [pick("시금치나물"), pick("두부조림")], rows_of(SPINACH, TOFU)
        )

        assert bodies(result) == ["추천 메뉴는 농식품정보원 공개 레시피 중 재료가 검수된 메뉴예요."]

    def test_표에_없는_소스는_건너뛴다(self) -> None:
        manual = menu_row("집밥", source="manual")

        result = basis_notices(ctx(None), [pick("집밥")], rows_of(manual))

        assert result == ()

    def test_표에_없는_소스가_섞이면_있는_기관만_말한다(self) -> None:
        manual = menu_row("집밥", source="manual")

        result = basis_notices(
            ctx(None), [pick("집밥"), pick("시금치나물")], rows_of(manual, SPINACH)
        )

        assert bodies(result) == ["추천 메뉴는 농식품정보원 공개 레시피 중 재료가 검수된 메뉴예요."]

    def test_합쳐진_레시피의_다른_소스도_기관에_넣는다(self) -> None:
        # 이름이 같은 두 소스의 레시피를 하나로 묶은 메뉴 — 메뉴 근거도 둘 다 가리킨다
        merged = menu_row("계란찜", source="mafra_recipe", merged_from=(("nics_recipe", "계란찜"),))

        result = basis_notices(ctx(None), [pick("계란찜")], rows_of(merged))

        assert "농촌진흥청 국립식량과학원" in bodies(result)[0]
        assert "농식품정보원" in bodies(result)[0]


class TestOrderAndSafety:
    def test_영양_줄_다음에_출처_줄이다(self) -> None:
        result = basis_notices(ctx(SPARSE), [pick("시금치나물")], rows_of(SPINACH))

        assert len(result) == 2
        assert "더 넣었어요" in result[0].body
        assert result[1].body.startswith("추천 메뉴는")

    def test_전부_코드가_쓴_안내_줄이다(self) -> None:
        result = basis_notices(ctx(SPARSE), [pick("시금치나물")], rows_of(SPINACH))

        assert {readout.kind for readout in result} == {"notice"}
        assert {readout.authored_by for readout in result} == {"code"}

    @pytest.mark.parametrize("group_days", [{"vegetables": 0}, {"vegetables": 1, "fruits": 0}])
    def test_만들_수_있는_모든_줄이_문장_규칙을_통과한다(self, group_days: dict[str, int]) -> None:
        both = verdict(group_days=group_days)
        nics = menu_row("애호박볶음", source="nics_recipe", food_groups=FRUIT)

        result = basis_notices(
            ctx(both),
            [pick("시금치나물"), pick("애호박볶음")],
            rows_of(SPINACH, nics),
        )

        assert result
        for readout in result:
            problem = wording_problem(readout.body)
            # 출처 줄은 먹은 식사가 아니라 레시피 기관을 말한다. 식사 주어 검사는 영양 문장의
            # 규칙이라 빼고, 수치 · 처방 · 몸 상태 · 아이 주어는 같이 막는다
            allowed = (
                {None, "no_meal_subject"} if readout.body.startswith("추천 메뉴는") else {None}
            )
            assert problem in allowed, readout.body
