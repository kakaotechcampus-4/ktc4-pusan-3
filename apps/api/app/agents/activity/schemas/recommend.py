"""놀이 후보 제출 인자. suggestion 모양에 맞춰야 화면에서 보여줄 수 있다.

ActivityCandidate는 suggestion의 content · reason과 suggestion_evidence 행으로 옮겨진다.
evidence가 비면(아이 기록 0행) 코드가 일반 추천(또래 기준)으로 분류한다.
모델이 kind를 고르지 않는다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.activity.schemas.common import (
    ActivitySetting,
    CaregiverRole,
    EvidencePick,
    Intensity,
    ToolArgs,
)
from app.agents.common.suggestion import MAX_SUGGESTIONS


class ActivityCandidate(ToolArgs):
    content: Annotated[
        str,
        Field(min_length=1, description="놀이·외출 후보. 예: 구슬 꿰기로 목걸이 만들기"),
    ]
    setting: Annotated[ActivitySetting, Field(description="indoor / outdoor / either")]
    materials: Annotated[
        list[str],
        Field(
            default_factory=list,
            description=(
                "활동에 쓰는 물건을 빠짐없이. 예: 구슬, 실. "
                "안전 검사는 코드가 이 목록과 content 로 한다 — 알아서 빼거나 바꾸지 않는다"
            ),
        ),
    ]
    physical_intensity: Annotated[Intensity, Field(description="low / medium / high")]
    involves_food: Annotated[
        bool,
        Field(description="음식·식재료를 만지거나 먹는 활동이면 true"),
    ]
    caregiver_role: Annotated[
        CaregiverRole,
        Field(description="together(같이) / nearby(곁에서 지켜봄) / independent(혼자)"),
    ]
    place_name: Annotated[
        str | None,
        Field(
            default=None,
            description="search_nearby_places 결과에 있는 이름만. 없으면 비운다. 지어내지 않는다",
        ),
    ]
    duration_min: Annotated[int | None, Field(default=None, ge=1, description="대략 몇 분")]
    why_this: Annotated[
        str,
        Field(description="이 아이에게 맞는 이유. 조회한 기록에 있는 내용으로만 쓴다"),
    ]
    why_now: Annotated[
        str,
        Field(description="지금인 이유. 예: 오늘 오후가 비어 있음, 미세먼지 좋음"),
    ]
    evidence: Annotated[
        list[EvidencePick],
        Field(
            default_factory=list,
            description="근거로 쓴 기록. search_activity_memory 결과에 있던 것만. 없으면 비운다",
        ),
    ]


class ProposeActivityCandidatesArgs(ToolArgs):
    # 모델은 늘 최대 개수만큼 낸다. 2개 이하나 4개 이상이면 인자 검증에서 거절된다.
    # 보호자에게 1~2개가 가는 것은 안전 필터로 빠진 뒤의 일이다 (Tool_공통.md §5-2)
    candidates: Annotated[
        list[ActivityCandidate],
        Field(
            min_length=MAX_SUGGESTIONS,
            max_length=MAX_SUGGESTIONS,
            description=f"놀이 후보 {MAX_SUGGESTIONS}개",
        ),
    ]
