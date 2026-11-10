"""식단 후보 출력 검증 — 보호자가 정한 조건과 레시피북 밖 생성 후보 (#740).

생성 후보(`generated`)는 보호자가 재료 · 요리 종류를 정해 물었고(`requested_food` 인정) 레시피북에서
맞는 메뉴가 모자랄 때만 받는다. 이름 · 재료를 모델이 쓰므로 코드가 형식 · 조건 · 최근 이름 · 안전
필터(이름 + 재료)를 건다. 메뉴 · 재료 · 아이는 전부 가짜다.
"""

from uuid import uuid4

import pytest

from app.agents.common.evidence import RankedEvidence
from app.agents.common.refs import Ref
from app.agents.food.meal_review import NO_REQUEST, RejectReason, RequestContext, review_candidates
from app.agents.food.preparation_items import preparation_items
from app.agents.food.readouts import GENERAL_REASON_REQUESTED, READOUTS
from app.agents.food.schemas.common import EvidenceRef
from app.agents.food.schemas.recommend import GeneratedMeal, MealCandidate
from app.agents.food.store import MenuCatalogRow, SafetyEntry
from app.agents.food.weaning_menu import weaning_menu_citation, weaning_menu_rows
from tests.unit.agents.food.support import MEAL_DOC, NOW, day, menu_row

REQUESTED_REASON = READOUTS.get(GENERAL_REASON_REQUESTED).template
EGG = [SafetyEntry(kind="allergy", label="달걀", status="active")]


def recipe(name: str, *ingredients: str, **overrides: object) -> MenuCatalogRow:
    return menu_row(
        name,
        source="mafra_recipe",
        ingredients=ingredients or ("가짜재료",),
        main_ingredients=ingredients[:1],
        **overrides,
    )


WEANING = weaning_menu_rows(7, synced_at=NOW)[0]  # 쇠고기애호박죽 — 중기(6–7개월) 첫 행
PAPRIKA_STIR = recipe("파프리카볶음", "파프리카", "올리브유")
TOFU = recipe("두부조림", "두부", "간장")
CUCUMBER = recipe("오이무침", "오이", "참기름")

ASKED = RequestContext(food="파프리카", kind="ingredient")


def meal(name: str = "파프리카볶음", **overrides) -> GeneratedMeal:
    base = {
        "name": name,
        "main_ingredients": ["파프리카"],
        "ingredients": ["파프리카", "올리브유"],
        "meal_slot": "dinner",
        "reason": "요청하신 조건에 맞아서 골랐어요",
    }
    return GeneratedMeal.model_validate({**base, **overrides})


def pick(menu_key: str, **kwargs) -> MealCandidate:
    kwargs.setdefault(
        "evidence", [EvidenceRef(kind="food_doc", id=str(MEAL_DOC.id), note="식단 자료를 참고함")]
    )
    return MealCandidate(
        menu_key=menu_key, meal_slot="dinner", reason="어제 급식과 겹치지 않아서 골랐어요", **kwargs
    )


def review(
    candidates=(),
    generated=(),
    *,
    request: RequestContext = ASKED,
    sampled=(),
    entries=(),
    stage="toddler",
    months=28,
    seen=None,
):
    return review_candidates(
        list(candidates),
        generated=list(generated),
        request=request,
        sampled={row.menu_key: row for row in sampled},
        seen=seen or {},
        docs={MEAL_DOC.id: MEAL_DOC},
        entries=entries,
        stage=stage,
        months=months,
        today=day(0),
    )


def reasons(result) -> list[RejectReason]:
    return [item.reason for item in result.rejections]


class TestGeneratedAccepted:
    def test_조건이_인정되면_생성_후보를_추천으로_받는다(self) -> None:
        result = review(generated=[meal()])

        assert result.rejections == ()
        (item,) = result.picks
        assert item.menu_key == "파프리카볶음"
        assert item.draft.content == "파프리카볶음"
        assert item.draft.agent == "food"

    def test_근거가_없는_생성은_요청_기반_일반_추천이다(self) -> None:
        (item,) = review(generated=[meal()]).picks

        assert item.draft.kind == "general"
        assert item.draft.requested is True
        assert item.draft.reason == REQUESTED_REASON
        assert item.draft.citations == ()  # 근거 0행이 정상이다

    def test_준비물은_주재료로_만든다(self) -> None:
        (item,) = review(generated=[meal(main_ingredients=["파프리카", "당근"])]).picks

        assert item.draft.items == preparation_items(("파프리카", "당근"))

    def test_모델이_쓴_끼니를_따른다(self) -> None:
        (item,) = review(generated=[meal(meal_slot="snack")]).picks

        assert item.meal_slot == "snack"

    def test_아이_기록을_인용하면_개인화_추천이다(self) -> None:
        key = uuid4()
        seen = {
            key: RankedEvidence(
                ref=Ref(kind="profile_affinity", id=key),
                tier=1,
                label="파프리카",
                polarity=1,
                observed_on=day(1),
                subject="파프리카",
            )
        }
        cite = EvidenceRef(kind="profile_affinity", id=str(key), note="파프리카를 잘 먹음")

        (item,) = review(generated=[meal(evidence=[cite])], seen=seen).picks

        assert item.draft.kind == "personalized"
        assert item.draft.requested is False
        assert item.draft.reason == "요청하신 조건에 맞아서 골랐어요"

    def test_레시피와_생성을_섞으면_둘_다_요청_기반_문구다(self) -> None:
        request = RequestContext(food="파프리카", kind="ingredient", matched=1)

        result = review(
            [pick("파프리카볶음", evidence=[])],
            [meal("잡채", ingredients=["파프리카", "당면"])],
            request=request,
            sampled=[PAPRIKA_STIR],
        )

        assert [p.draft.content for p in result.picks] == ["파프리카볶음", "잡채"]
        assert {p.draft.reason for p in result.picks} == {REQUESTED_REASON}
        # 레시피북 메뉴는 메뉴 근거를 그대로 단다 — 생성만 근거가 0행이다
        assert [len(p.draft.citations) for p in result.picks] == [1, 0]


class TestGeneratedNotAllowed:
    def test_조건이_인정되지_않으면_생성을_받지_않는다(self) -> None:
        result = review(generated=[meal()], request=NO_REQUEST)

        assert reasons(result) == [RejectReason.GENERATED_NOT_ALLOWED]
        assert result.picks == ()

    @pytest.mark.parametrize("stage", ["infant_weaning", "infant_milk"])
    def test_이유기_이하는_생성을_받지_않는다(self, stage: str) -> None:
        result = review(generated=[meal()], stage=stage, months=8)

        assert reasons(result) == [RejectReason.GENERATED_NOT_ALLOWED]

    def test_조건이_없으면_레시피_후보는_지금처럼_일반_추천_문구다(self) -> None:
        result = review([pick("두부조림")], request=NO_REQUEST, sampled=[TOFU])

        assert result.picks[0].draft.reason != REQUESTED_REASON
        assert result.picks[0].draft.requested is False


class TestGeneratedCount:
    def test_조건_맞는_레시피가_샘플에_있으면_그만큼_자리가_준다(self) -> None:
        request = RequestContext(food="파프리카", kind="ingredient", matched=2)

        result = review(
            generated=[meal(), meal("잡채", ingredients=["파프리카", "당면"])], request=request
        )

        assert reasons(result) == [RejectReason.GENERATED_TOO_MANY]
        assert result.rejections[0].index == 1

    def test_조건_맞는_레시피가_없으면_세_개까지_낼_수_있다(self) -> None:
        names = ["파프리카볶음", "파프리카전", "파프리카무침"]

        result = review(generated=[meal(name) for name in names])

        assert result.rejections == ()
        assert len(result.picks) == 3


class TestGeneratedFormat:
    @pytest.mark.parametrize(
        "ingredients",
        [
            ["파프리카 2개", "올리브유"],
            ["파프리카", "올리브유 1큰술"],
            ["파프리카(빨강)", "올리브유"],
            ["파프리카", "소금 5g"],
            ["파프리카", "우유 200ml"],
        ],
    )
    def test_재료에_숫자_단위_괄호가_있으면_거절한다(self, ingredients: list[str]) -> None:
        result = review(generated=[meal(ingredients=ingredients)])

        assert reasons(result) == [RejectReason.GENERATED_FORMAT]

    def test_이름에_숫자가_있으면_거절한다(self) -> None:
        assert reasons(review(generated=[meal("3색파프리카볶음")])) == [
            RejectReason.GENERATED_FORMAT
        ]

    def test_주재료에_단위가_있으면_거절한다(self) -> None:
        result = review(generated=[meal(main_ingredients=["파프리카 반 개"])])

        assert reasons(result) == [RejectReason.GENERATED_FORMAT]


class TestRequestCondition:
    def test_재료_조건이면_생성_후보에_그_재료가_있어야_한다(self) -> None:
        result = review(
            generated=[meal("두부조림", main_ingredients=["두부"], ingredients=["두부", "간장"])]
        )

        assert reasons(result) == [RejectReason.REQUEST_MISMATCH]

    def test_이름에만_있어도_재료_조건을_채운다(self) -> None:
        result = review(
            generated=[meal("파프리카덮밥", main_ingredients=["밥"], ingredients=["밥", "소고기"])]
        )

        assert result.rejections == ()

    def test_재료_조건이면_레시피_후보도_그_재료가_있어야_한다(self) -> None:
        result = review([pick("두부조림")], request=ASKED, sampled=[TOFU])

        assert reasons(result) == [RejectReason.REQUEST_MISMATCH]

    def test_재료_조건을_채우는_레시피는_받는다(self) -> None:
        result = review([pick("파프리카볶음")], request=ASKED, sampled=[PAPRIKA_STIR])

        assert result.rejections == ()

    def test_요리_종류_조건은_재료를_보지_않는다(self) -> None:
        request = RequestContext(food="태국음식", kind="cuisine")

        result = review(
            generated=[meal("팟타이", main_ingredients=["쌀국수"], ingredients=["쌀국수", "숙주"])],
            request=request,
        )

        assert result.rejections == ()


class TestRecentAndPoolNames:
    def test_최근_추천한_이름은_거절한다(self) -> None:
        request = RequestContext(
            food="파프리카", kind="ingredient", recent_keys=frozenset({"파프리카볶음"})
        )

        assert reasons(review(generated=[meal()], request=request)) == [
            RejectReason.GENERATED_RECENT
        ]

    def test_풀의_레시피와_이름이_같으면_그_레시피_행으로_받는다(self) -> None:
        request = RequestContext(
            food="파프리카",
            kind="ingredient",
            pool={"파프리카볶음": PAPRIKA_STIR},
            recipe_keys=frozenset({"파프리카볶음"}),
        )

        (item,) = review(
            generated=[meal(ingredients=["파프리카", "마가린"])], request=request
        ).picks

        # 이름 · 재료는 검수된 레시피 행에서 오고, 모델이 쓴 재료는 쓰지 않는다. 출처 근거가 붙는다
        assert item.draft.content == "파프리카볶음"
        assert len(item.draft.citations) == 1
        assert item.draft.citations[0].ref.kind == "recipe_catalog"

    def test_풀_밖_레시피와_이름이_같으면_막힌_것으로_보고_뺀다(self) -> None:
        # 안전 필터나 최근 추천으로 풀에서 빠진 레시피가 이름만 바꿔 돌아오지 않게 한다
        request = RequestContext(
            food="파프리카", kind="ingredient", recipe_keys=frozenset({"파프리카볶음"})
        )

        result = review(generated=[meal()], request=request)

        assert result.picks == () and result.rejections == ()
        assert result.removed == ("파프리카볶음",)

    def test_레시피_후보와_이름이_같은_생성은_중복이다(self) -> None:
        request = RequestContext(
            food="파프리카", kind="ingredient", pool={"파프리카볶음": PAPRIKA_STIR}, matched=1
        )

        result = review([pick("파프리카볶음")], [meal()], request=request, sampled=[PAPRIKA_STIR])

        assert reasons(result) == [RejectReason.DUPLICATE_MENU]


class TestGeneratedSafety:
    def test_이름과_재료로_안전_필터를_건다(self) -> None:
        result = review(generated=[meal(ingredients=["파프리카", "계란"])], entries=EGG)

        assert result.picks == ()
        assert result.removed == ("파프리카볶음",)
        assert result.rejections == ()  # 걸린 것은 거절이 아니라 뺀다 — 사유를 돌려주지 않는다

    def test_주재료도_안전_필터에_건다(self) -> None:
        result = review(generated=[meal(main_ingredients=["파프리카", "계란"])], entries=EGG)

        assert result.removed == ("파프리카볶음",)

    def test_통과한_생성은_들어_있을_수_있는_알레르기를_싣는다(self) -> None:
        (item,) = review(generated=[meal(ingredients=["파프리카", "계란"])]).picks

        assert "난류" in item.draft.allergens

    def test_걸린_후보와_통과한_후보가_섞이면_통과한_것만_남는다(self) -> None:
        result = review(
            generated=[meal("파프리카달걀말이", ingredients=["파프리카", "계란"]), meal()],
            entries=EGG,
        )

        assert [p.draft.content for p in result.picks] == ["파프리카볶음"]
        assert result.removed == ("파프리카달걀말이",)

    def test_알레르기_등록이_없으면_그대로_통과한다(self) -> None:
        assert review(generated=[meal(ingredients=["파프리카", "계란"])]).removed == ()


class TestGeneratedReason:
    def test_이유에_수치가_있으면_거절한다(self) -> None:
        result = review(generated=[meal(reason="비타민이 100mg 들어 있어요")])

        assert reasons(result) == [RejectReason.WORDING]

    def test_이유에_막힌_음식을_말하면_거절한다(self) -> None:
        result = review(generated=[meal(reason="계란이 들어가 영양이 좋아요")], entries=EGG)

        assert reasons(result) == [RejectReason.FOOD_MENTION]

    def test_조회하지_않은_근거는_거절한다(self) -> None:
        cite = EvidenceRef(kind="profile_affinity", id=str(uuid4()), note="지어낸 근거")

        result = review(generated=[meal(evidence=[cite])])

        assert reasons(result) == [RejectReason.UNKNOWN_EVIDENCE]

    def test_거절_사유는_번호와_고칠_방향만_담는다(self) -> None:
        from app.agents.food.meal_review import explain

        text = explain(review(generated=[meal(ingredients=["파프리카 2개"])]).rejections)

        assert "1번 후보" in text
        assert "파프리카" not in text  # 모델이 쓴 값을 message 에 싣지 않는다


class TestInfantKeepsRecipeBookOnly:
    """생성할 수 없는 단계(돌 이전)에서는 조건 검사 · 요청 기반 문구를 적용하지 않는다.

    재료 조건 검사를 그대로 걸면 레시피북에 그 재료가 없을 때 모델이 빠져나갈 길이 없다 — 레시피는
    조건 불일치로 막히고 생성은 단계로 막힌다. 이유기는 이유식 식단 표 메뉴만 낸다(#399).
    """

    def test_레시피가_조건과_안_맞아도_거절하지_않는다(self) -> None:
        porridge = recipe("쌀미음", "쌀", stage_min="infant_weaning")

        result = review(
            [pick("쌀미음")], request=ASKED, sampled=[porridge], stage="infant_weaning", months=7
        )

        assert result.rejections == ()
        assert len(result.picks) == 1

    def test_이유는_요청_기반이_아니라_이유기_일반_추천_문구다(self) -> None:
        porridge = recipe("쌀미음", "쌀", stage_min="infant_weaning")

        (item,) = review(
            [pick("쌀미음")], request=ASKED, sampled=[porridge], stage="infant_weaning", months=7
        ).picks

        assert item.draft.reason == "이 시기 이유식 단계에 맞춘 메뉴예요."
        assert item.draft.requested is False

    def test_이유식_식단_메뉴의_일반_추천은_food_doc_식단_행이_근거다(self) -> None:
        (item,) = review(
            [pick(WEANING.menu_key, evidence=[])],
            request=NO_REQUEST,
            sampled=[WEANING],
            stage="infant_weaning",
            months=7,
        ).picks

        assert item.draft.kind == "general"
        assert item.draft.reason == "이 시기 이유식 단계에 맞춘 메뉴예요."
        assert item.draft.citations == (weaning_menu_citation(WEANING),)

    def test_이유식_메뉴도_아이_기록을_인용하면_개인화이고_식단_근거는_버린다(self) -> None:
        key = uuid4()
        seen = {
            key: RankedEvidence(
                ref=Ref(kind="observation_food", id=key),
                tier=3,
                label="애호박",
                polarity=1,
                observed_on=day(1),
                subject="애호박",
            )
        }
        cite = EvidenceRef(kind="observation_food", id=str(key), note="애호박을 잘 먹음")

        (item,) = review(
            [pick(WEANING.menu_key, evidence=[cite])],
            request=NO_REQUEST,
            sampled=[WEANING],
            seen=seen,
            stage="infant_weaning",
            months=7,
        ).picks

        assert item.draft.kind == "personalized"
        assert [c.ref.kind for c in item.draft.citations] == ["observation_food"]

    def test_유아식_단계에서는_조건_검사가_계속_걸린다(self) -> None:
        result = review([pick("두부조림")], request=ASKED, sampled=[TOFU])

        assert reasons(result) == [RejectReason.REQUEST_MISMATCH]
