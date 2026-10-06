"""식단 후보 제출 인자. suggestion form에 맞춰야 화면에서 보여줄 수 있음
MealCandidate는 suggestion의 content · reason · source_refs로 옮겨진다.
evidence가 비면 일반 추천(또래 기준)으로 분류된다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.food.schemas.common import EvidenceRef, MealSlot, ToolArgs


class MealCandidate(ToolArgs):
    content: Annotated[
        str,
        Field(description="식사·간식 후보. 예: 계란말이와 미역국. 정확한 레시피는 쓰지 않는다"),
    ]
    ingredients: Annotated[
        list[str],
        Field(
            min_length=1,
            description="들어가는 재료를 빠짐없이 포함. 알레르기 필터가 이 목록으로 거른다",
        ),
    ]
    meal_slot: Annotated[MealSlot, Field(description="breakfast / lunch / dinner / snack")]
    why_this: Annotated[
        str,
        Field(description="이 아이에게 맞는 이유. 조회한 기록에 있는 내용으로만 쓴다"),
    ]
    why_now: Annotated[
        str,
        Field(description="지금인 이유. 예: 오늘 급식과 겹치지 않음, 최근 채소가 적었음"),
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
