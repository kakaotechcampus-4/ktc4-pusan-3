"""Food 모델 tool 인자 스키마 검증.

- 식단 후보는 1 ~ `MAX_SUGGESTIONS` 개다. 후보 풀이 처음부터 모자라면 모델이 고를 수 있는 것도
  모자라서 1개부터 받는다. 채울 수 있는데 덜 낸 것은 인자 검증이 아니라 출력 tool 의
  `check_count` 가 막는다 (Tool_공통.md §5-2).
"""

import pytest
from pydantic import ValidationError

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.food.registry import TOOL_SPECS
from app.agents.food.schemas.recommend import ProposeMealCandidatesArgs


def candidate() -> dict:
    return dict(
        content="계란말이",
        ingredients=["계란"],
        meal_slot="dinner",
        why_this="계란을 잘 먹어요",
        why_now="오늘 급식과 겹치지 않아요",
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
        candidates = spec["function"]["parameters"]["properties"]["candidates"]
        assert candidates["minItems"] == 1
        assert candidates["maxItems"] == MAX_SUGGESTIONS
