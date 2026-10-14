"""교육 활동 제출 인자. 후보는 suggestion 의 content · reason · items 와 근거 행으로 옮겨진다.

모델이 kind 를 고르지 않는다 — 아이 기록 근거가 0행이면 코드가 일반 추천으로 분류한다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.growth.schemas.common import EvidencePick, ToolArgs

# 단계는 짧게 — 카드 한 장에 다 보여야 한다 (Growth_Tool_명세 §2)
MAX_STEPS = 4

Step = Annotated[str, Field(min_length=1)]


class LearningItem(ToolArgs):
    title: Annotated[str, Field(min_length=1, max_length=40, description="활동 이름")]
    steps: Annotated[
        list[Step],
        Field(min_length=1, max_length=MAX_STEPS, description=f"하는 순서 1~{MAX_STEPS}줄"),
    ]
    materials: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="쓰는 물건을 빠짐없이. 안전 검사는 코드가 이 목록과 문장으로 한다",
        ),
    ]
    evidence: Annotated[
        list[EvidencePick],
        Field(default_factory=list, description="근거로 쓴 기록이나 [예시]. 없으면 비운다"),
    ]
    reason: Annotated[
        str,
        Field(
            min_length=1,
            description="이 아이에게 맞는 이유 한두 문장. 조회한 사실로만. 화면에 그대로 나간다",
        ),
    ]


class ProposeLearningActivityArgs(ToolArgs):
    # 늘 최대 개수만큼 낸다. 1~2개가 나가는 것은 안전 필터로 빠진 뒤의 일이다 (Tool_공통 §5-2)
    items: Annotated[
        list[LearningItem],
        Field(
            min_length=MAX_SUGGESTIONS,
            max_length=MAX_SUGGESTIONS,
            description=f"교육 활동 후보 {MAX_SUGGESTIONS}개",
        ),
    ]
