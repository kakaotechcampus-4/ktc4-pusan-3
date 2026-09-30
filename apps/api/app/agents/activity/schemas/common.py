# Activity tool 스키마가 공유하는 enum · 공통 인자.

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ToolArgs(BaseModel):
    """모든 Activity tool argument의 공통 base. 정의되지 않은 필드는 받지 않는다."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ActivitySetting(StrEnum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    EITHER = "either"


class Intensity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CaregiverRole(StrEnum):
    """보호자가 어디까지 붙어 있어야 하는가. 0–17개월은 together 만 통과한다 (출력 검증)."""

    TOGETHER = "together"
    NEARBY = "nearby"
    INDEPENDENT = "independent"


class PlaceCategory(StrEnum):
    """장소 검색어가 되는 닫힌 값. 모델이 만든 문자열은 Kakao `query`에 넣지 않는다 (D8).

    보호자 발화 원문이 외부 서버 로그에 남을 수 있어서다.
    """

    PARK = "park"
    PLAYGROUND = "playground"
    LIBRARY = "library"
    INDOOR_PLAYGROUND = "indoor_playground"
    EXPERIENCE_CENTER = "experience_center"


class DayLabel(StrEnum):
    """조회 날짜 라벨. 모델은 라벨만 고르고 날짜는 코드가 계산한다."""

    TODAY = "today"
    TOMORROW = "tomorrow"
    THIS_WEEKEND = "this_weekend"


class EvidencePick(ToolArgs):
    """모델이 고른 근거 하나. 공통 `EvidenceCitation`과 다르다.

    모델은 `id`를 고르고 `note`만 쓴다. `source_kind` · `source_updated_at` · `polarity` ·
    `label`은 출력 tool이 그 id를 돌려준 조회 결과에서 채워 `EvidenceCitation`으로 바꾼다.
    모델에게 맡기면 틀린 값이 들어갈 수 있다 (D6).
    """

    id: Annotated[
        str,
        Field(description="search_activity_memory 결과에 있던 id만. 지어내지 않는다"),
    ]
    note: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "그 기록에서 무엇을 근거로 봤는지 한 줄. "
                "예: 지난주 공원에서 모래놀이를 한 시간 넘게 했어요. "
                "보호자 화면에 그대로 나간다"
            ),
        ),
    ]
