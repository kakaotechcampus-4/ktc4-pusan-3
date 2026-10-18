"""Growth 모델 tool 인자 — 개수 고정 · 정의 안 된 칸 거부 · tool 정의 8개."""

import pytest
from pydantic import ValidationError

from app.agents.growth.schemas.learning import ProposeLearningActivityArgs
from app.agents.growth.schemas.memory import SearchAffinityArgs, SearchRoutineMemoryArgs
from app.agents.growth.schemas.routine import ProposeRoutinePlanArgs
from app.agents.growth.schemas.tool_defs import TOOL_DEFINITIONS


def learning_item(title: str = "블록 숫자 세기", **overrides) -> dict:
    return {
        "title": title,
        "steps": ["블록을 하나씩 늘어놓는다", "하나 둘 세며 손으로 짚는다"],
        "materials": ["블록"],
        "evidence": [],
        "reason": "이 나이에 많이 하는 수 세기 놀이예요",
        **overrides,
    }


class TestLearning:
    def test_후보는_세_개_고정이다(self):
        ProposeLearningActivityArgs.model_validate({"items": [learning_item()] * 3})
        for count in (2, 4):
            with pytest.raises(ValidationError):
                ProposeLearningActivityArgs.model_validate({"items": [learning_item()] * count})

    def test_단계는_한_개에서_네_개다(self):
        with pytest.raises(ValidationError):
            ProposeLearningActivityArgs.model_validate(
                {"items": [learning_item(steps=["a", "b", "c", "d", "e"])] * 3}
            )
        with pytest.raises(ValidationError):
            ProposeLearningActivityArgs.model_validate({"items": [learning_item(steps=[])] * 3})

    def test_정의되지_않은_칸은_받지_않는다(self):
        with pytest.raises(ValidationError):
            ProposeLearningActivityArgs.model_validate(
                {"items": [learning_item(kind="general")] * 3}
            )


class TestRoutine:
    def test_단계가_없으면_items_는_비어도_된다(self):
        args = ProposeRoutinePlanArgs.model_validate(
            {"category": "self_care", "subject": "양치", "items": []}
        )
        assert args.items == []

    def test_모르는_카테고리는_거부한다(self):
        with pytest.raises(ValidationError):
            ProposeRoutinePlanArgs.model_validate(
                {"category": "sleep", "subject": "잠", "items": []}
            )

    def test_subject_는_20자를_넘지_않는다(self):
        with pytest.raises(ValidationError):
            ProposeRoutinePlanArgs.model_validate(
                {"category": "self_care", "subject": "가" * 21, "items": []}
            )

    def test_루틴_기억_검색은_카테고리를_비워도_된다(self):
        assert SearchRoutineMemoryArgs.model_validate({}).category is None


class TestAffinity:
    def test_routine_은_affinity_도메인이_아니다(self):
        with pytest.raises(ValidationError):
            SearchAffinityArgs.model_validate({"domains": ["routine"]})


class TestDefinitions:
    def test_모델_tool_정의는_여덟_개다(self):
        assert [d.name for d in TOOL_DEFINITIONS] == [
            "search_education_memory",
            "search_routine_memory",
            "search_activity_memory",
            "search_affinity",
            "search_books",
            "propose_learning_activity",
            "propose_routine_plan",
            "propose_books",
        ]

    def test_모델이_kind_status_allergens_를_고르는_칸이_없다(self):
        for definition in TOOL_DEFINITIONS:
            schema = str(definition.args.model_json_schema())
            for forbidden in ("'kind'", "'status'", "'allergens'", "'expires_at'"):
                assert forbidden not in schema, (definition.name, forbidden)


class TestBookArgs:
    """검색어는 정보나루로 나간다 — 문장 · 문장부호 · 긴 이름이 밖으로 나가지 않게 모양을 묶는다."""

    @pytest.mark.parametrize("words", [["공룡"], ["아기 공룡", "dino", "숫자 123"]])
    def test_주제_낱말은_받는다(self, words):
        from app.agents.growth.schemas.books import SearchBooksArgs

        assert SearchBooksArgs.model_validate({"keywords": words}).keywords == words

    @pytest.mark.parametrize(
        "words",
        [
            [],
            ["공룡", "기차", "동물", "숫자"],
            ["가" * 13],
            [" 공룡"],
            ["공룡  책"],
            ["우리 아이가 좋아하는 공룡!"],
            ["공룡\n책"],
        ],
    )
    def test_모양이_다르면_받지_않는다(self, words):
        from app.agents.growth.schemas.books import SearchBooksArgs

        with pytest.raises(ValidationError):
            SearchBooksArgs.model_validate({"keywords": words})

    def test_책은_3권까지_빈_제출도_모양으로는_받는다(self):
        from app.agents.growth.schemas.books import ProposeBooksArgs

        assert ProposeBooksArgs.model_validate({"items": []}).items == []
        item = {"isbn": "9788900000001", "reason": "공룡이 나와요"}
        with pytest.raises(ValidationError):
            ProposeBooksArgs.model_validate({"items": [item] * 4})
