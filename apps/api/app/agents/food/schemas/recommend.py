"""식단 후보 제출 인자. suggestion form에 맞춰야 화면에서 보여줄 수 있음

모델은 코드가 보여 준 후보 목록에서 `menu_key` 만 고른다. 메뉴 이름과 재료는 모델이 쓰지 않는다 —
코드가 카탈로그 행에서 채운다. 모델이 "바나나 셰이크" 를 내면서 재료에 우유를 빠뜨려도 사후 필터가
그대로 통과시키는 일이 없게 하려는 것이다. MealCandidate는 suggestion의 reason · 근거로 옮겨진다.
evidence가 비면 일반 추천(또래 기준)으로 분류된다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.food.schemas.common import EvidenceRef, MealSlot, ToolArgs


class MealCandidate(ToolArgs):
    menu_key: Annotated[
        str,
        Field(
            min_length=1,
            description="보여 준 후보 목록에 있는 menu_key 만. 지어내거나 고쳐 쓰지 않는다",
        ),
    ]
    meal_slot: Annotated[MealSlot, Field(description="breakfast / lunch / dinner / snack")]
    reason: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "이 아이에게 맞는 이유와 지금인 이유를 한두 문장으로. "
                "조회한 기록에 있는 내용으로만 쓴다. "
                "싫어하는 것을 피해 골랐으면 무엇을 피했는지 쓴다. "
                "수치와 몸 상태는 쓰지 않는다. 보호자 화면에 그대로 나간다"
            ),
        ),
    ]
    evidence: Annotated[
        list[EvidenceRef],
        Field(
            default_factory=list,
            description="근거로 쓴 기록. 조회 결과에 있던 것만. 근거가 없으면 비운다",
        ),
    ]


class ProposeMealCandidatesArgs(ToolArgs):
    # 후보 풀이 처음부터 모자라면 모델이 고를 수 있는 것도 모자라서 1개부터 받는다.
    # 채울 수 있는데 덜 낸 것은 출력 tool 의 check_count 가 막는다 (Tool_공통.md §5-2)
    candidates: Annotated[
        list[MealCandidate],
        Field(
            min_length=1,
            max_length=MAX_SUGGESTIONS,
            description=f"식사·간식 후보. 최대 {MAX_SUGGESTIONS}개",
        ),
    ]
