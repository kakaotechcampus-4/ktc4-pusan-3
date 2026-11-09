"""Food 모델 tool 인자 스키마 검증.

- 식단 후보(레시피 `candidates` + 생성 `generated`)는 합쳐 1 ~ `MAX_SUGGESTIONS` 개다. 후보 풀이
  처음부터 모자라면 모델이 고를 수 있는 것도 모자라서 1개부터 받는다. 채울 수 있는데 덜 낸 것은
  인자 검증이 아니라 출력 tool 의 `check_count` 가 막는다 (Tool_공통.md §5-2).
"""

import pytest
from pydantic import ValidationError

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.food.registry import TOOL_SPECS
from app.agents.food.schemas.recommend import (
    GeneratedMeal,
    MealCandidate,
    ProposeMealCandidatesArgs,
)


def candidate() -> dict:
    return dict(
        menu_key="계란말이",
        meal_slot="dinner",
        reason="계란을 잘 먹어서 골랐어요",
    )


def propose(count: int) -> dict:
    return {"candidates": [candidate() for _ in range(count)]}


class TestCandidateCount:
    @pytest.mark.parametrize("count", [1, 2, 3])
    def test_최대_개수까지_받는다(self, count):
        args = ProposeMealCandidatesArgs.model_validate(propose(count))
        assert len(args.candidates) == count

    @pytest.mark.parametrize("count", [0, MAX_SUGGESTIONS + 1])
    def test_0개거나_넘치면_거절한다(self, count):
        with pytest.raises(ValidationError):
            ProposeMealCandidatesArgs.model_validate(propose(count))

    def test_모델에게_보내는_스펙에도_최대_개수가_실린다(self):
        """검증 규칙과 모델 스펙이 같은 Pydantic 모델에서 나온다."""
        spec = next(s for s in TOOL_SPECS if s["function"]["name"] == "propose_meal_candidates")
        properties = spec["function"]["parameters"]["properties"]
        assert properties["candidates"]["maxItems"] == MAX_SUGGESTIONS
        assert properties["generated"]["maxItems"] == MAX_SUGGESTIONS


class TestCandidateShape:
    """모델이 메뉴 이름 · 재료를 쓰는 칸이 없다. 둘 다 코드가 카탈로그 행에서 채운다."""

    def test_후보는_menu_key_끼니_이유_근거만_받는다(self):
        assert set(MealCandidate.model_fields) == {"menu_key", "meal_slot", "reason", "evidence"}

    @pytest.mark.parametrize("field", ["content", "ingredients", "why_this", "why_now"])
    def test_메뉴_이름_재료_옛_이유_칸을_넣으면_거절한다(self, field):
        with pytest.raises(ValidationError):
            MealCandidate.model_validate({**candidate(), field: "x"})

    def test_menu_key_와_이유가_비면_거절한다(self):
        with pytest.raises(ValidationError):
            MealCandidate.model_validate({**candidate(), "menu_key": ""})
        with pytest.raises(ValidationError):
            MealCandidate.model_validate({**candidate(), "reason": ""})

    def test_모델에게_보내는_스펙에_재료_칸이_없다(self):
        spec = next(s for s in TOOL_SPECS if s["function"]["name"] == "propose_meal_candidates")
        item = spec["function"]["parameters"]["properties"]["candidates"]["items"]

        assert set(item["properties"]) == {"menu_key", "meal_slot", "reason", "evidence"}
        assert "재료는 빠짐없이" not in spec["function"]["description"]


def meal(**overrides) -> dict:
    return {
        "name": "파프리카볶음",
        "main_ingredients": ["파프리카"],
        "ingredients": ["파프리카", "올리브유"],
        "meal_slot": "dinner",
        "reason": "요청하신 조건에 맞아서 골랐어요",
        **overrides,
    }


class TestGeneratedMeal:
    """조건이 인정되고 레시피북이 모자랄 때만 쓰는 생성 후보. 이름 · 재료는 코드가 검사한다."""

    def test_생성_후보만_내도_받는다(self):
        args = ProposeMealCandidatesArgs.model_validate(
            {"requested_food": "파프리카", "requested_kind": "ingredient", "generated": [meal()]}
        )

        assert args.candidates == []
        assert [item.name for item in args.generated] == ["파프리카볶음"]

    def test_레시피와_생성_후보를_섞어도_합쳐_세_개까지다(self):
        mixed = {"candidates": [candidate(), candidate()], "generated": [meal()]}

        assert len(ProposeMealCandidatesArgs.model_validate(mixed).generated) == 1
        with pytest.raises(ValidationError):
            ProposeMealCandidatesArgs.model_validate(
                {"candidates": [candidate(), candidate()], "generated": [meal(), meal()]}
            )

    def test_둘_다_비면_거절한다(self):
        with pytest.raises(ValidationError):
            ProposeMealCandidatesArgs.model_validate({"candidates": [], "generated": []})

    def test_조건_칸은_안_적어도_된다(self):
        args = ProposeMealCandidatesArgs.model_validate(propose(1))

        assert args.requested_food is None and args.requested_kind is None
        assert args.generated == []

    def test_조건_종류는_재료와_요리_종류만이다(self):
        for kind in ("ingredient", "cuisine"):
            ProposeMealCandidatesArgs.model_validate({**propose(1), "requested_kind": kind})
        with pytest.raises(ValidationError):
            ProposeMealCandidatesArgs.model_validate({**propose(1), "requested_kind": "mood"})

    def test_조건은_스무_자까지다(self):
        with pytest.raises(ValidationError):
            ProposeMealCandidatesArgs.model_validate({**propose(1), "requested_food": "가" * 21})

    @pytest.mark.parametrize(
        "overrides",
        [
            {"name": "굴"},  # 두 글자 미만
            {"name": "가" * 21},
            {"main_ingredients": []},
            {"main_ingredients": ["a", "b", "c", "d"]},
            {"ingredients": []},
            {"ingredients": [f"재료{i}" for i in range(9)]},
            {"ingredients": [""]},
            {"ingredients": ["가" * 16]},
            {"reason": ""},
        ],
    )
    def test_생성_후보의_칸_제한을_넘으면_거절한다(self, overrides):
        with pytest.raises(ValidationError):
            GeneratedMeal.model_validate(meal(**overrides))

    def test_생성_후보는_정해진_칸만_받는다(self):
        assert set(GeneratedMeal.model_fields) == {
            "name",
            "main_ingredients",
            "ingredients",
            "meal_slot",
            "reason",
            "evidence",
        }
        with pytest.raises(ValidationError):
            GeneratedMeal.model_validate({**meal(), "recipe": "볶는다"})  # 레시피는 쓰지 않는다
