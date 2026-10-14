"""Growth 모델 tool 인자 — 개수 고정 · 정의 안 된 칸 거부 · tool 정의 6개."""

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
    def test_모델_tool_정의는_여섯_개다(self):
        assert [d.name for d in TOOL_DEFINITIONS] == [
            "search_education_memory",
            "search_routine_memory",
            "search_activity_memory",
            "search_affinity",
            "propose_learning_activity",
            "propose_routine_plan",
        ]

    def test_모델이_kind_status_allergens_를_고르는_칸이_없다(self):
        for definition in TOOL_DEFINITIONS:
            schema = str(definition.args.model_json_schema())
            for forbidden in ("'kind'", "'status'", "'allergens'", "'expires_at'"):
                assert forbidden not in schema, (definition.name, forbidden)
