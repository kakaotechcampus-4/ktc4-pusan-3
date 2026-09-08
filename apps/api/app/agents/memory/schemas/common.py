# tool 스키마가 공유하는 enum/상수/공통 필드.

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

ALL_DAY_MIN = 1440              # "하루 종일" = 하루(1440분)
MAX_DURATION_MIN = ALL_DAY_MIN  # 활동/학습 세션은 하루를 넘길 수 없음


class TemporalDirection(StrEnum):
    """"금요일"처럼 여러 해석이 가능한 날짜 표현을 과거/미래 중 어느 쪽으로 읽을지 방향을 결정."""

    PAST = "past"
    FUTURE = "future"
    NEAREST = "nearest"


class ConfidenceSource(StrEnum):
    """관찰 정보의 출처."""

    INSTITUTION_NOTICE = "institution_notice"   # 알림장/기관 공지
    PARENT_DIRECT = "parent_direct"             # 보호자가 직접 본 것
    PARENT_HEDGED = "parent_hedged"             # "~한 것 같아" 처럼 확신이 약한 진술
    PARENT_HEARSAY = "parent_hearsay"           # "선생님 말로는" 처럼 전해 들은 것


class Severity(StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    EMERGENCY = "emergency"


class EngagementLevel(StrEnum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


class StrongSignal(StrEnum):
    """성향 판단에 무게를 주는 행동 신호. 발화에 근거가 있을 때만 붙인다."""

    RESISTANCE_TO_REDIRECT = "resistance_to_redirect"
    SELF_INITIATED = "self_initiated"
    COMPARATIVE_CHOICE = "comparative_choice"
    ASKS_QUESTIONS = "asks_questions"
    ROLE_EXTENSION = "role_extension"


class EventType(StrEnum):
    CORE = "core"           # 일상적으로 반복되는 일정
    EPISODIC = "episodic"   # 단발성 일정


class EventCategory(StrEnum):
    INSTITUTION = "institution"
    HEALTH = "health"
    ACTIVITY = "activity"
    ETC = "etc"


# 날짜·시각은 원문 표현으로 받고 실제 계산은 common/datetime_rules.py에서 수행
DateExpr = Annotated[
    str,
    Field(
        description="원문의 날짜 표현. 예: 오늘, 모레, 금요일, 다음 주 목요일, 2023년 5월 3일"
    ),
]

TimeExpr = Annotated[
    str | None,
    Field(
        default=None,
        description="원문의 시각 표현. 예: 오전 8시, 저녁 8시, 15:30. 발화에 없으면 비워둔다"
    ),
]
Direction = Annotated[
    TemporalDirection,
    Field(
        default=TemporalDirection.NEAREST,
        description=(
            "날짜 표현이 과거인지 미래인지. 이미 일어난 관찰은 past, 앞으로의 일정은 future. "
            "오늘/내일처럼 명확하면 nearest"
        ),
    ),
]
RawText = Annotated[
    str,
    Field(description="이 tool 호출의 근거가 된 원문 구간만. 발화 전체를 넣지 않는다"),
]
RecordId = Annotated[str, Field(description="조회 tool 이 돌려준 id. 임의로 만들지 않는다")]


class ToolArgs(BaseModel):
    """모든 tool argument 의 공통 base. 정의되지 않은 필드는 받지 않는다."""
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ObservationCreateArgs(ToolArgs):
    """observation 계층의 테이블 create가 공유하는 필드.
    id/child_id/source_writer/observed_range/status는
    전부 context 와 규칙이 채우는 영역이다.
    """

    raw_text: RawText
    observed_on: DateExpr
    temporal_direction: Direction
    confidence_source: Annotated[
        ConfidenceSource,
        Field(
            default=ConfidenceSource.PARENT_DIRECT,
            description="보호자가 직접 관찰했거나 아이의 발언이면 parent_direct, 전해 들었으면 parent_hearsay," \
            "기관 공지면 institution_notice, 확신이 약하면 parent_hedged",
        ),
    ]


class PromotableCreateArgs(ObservationCreateArgs):
    """승격 파이프라인에 관여하는 도메인(food/education/activity)의 추가 필드."""

    polarity: Annotated[
        int,
        Field(default=0, ge=-1, le=1, description="좋아함 1 / 중립 0 / 싫어함 -1"),
    ]
    strong_signals: Annotated[
        list[StrongSignal],
        Field(default_factory=list, description="발화에 근거가 있을 때만. 없으면 빈 배열"),
    ]


class ObservationQueryArgs(ToolArgs):
    """observation 조회 조건. 자유 검색 대신 허용 필드만 연다."""
    date_from: Annotated[
        str | None, Field(default=None, description="관찰 일자 시작. 표현 또는 YYYY-MM-DD")
    ]
    date_to: Annotated[
        str | None, Field(default=None, description="관찰 일자 끝. 표현 또는 YYYY-MM-DD")
    ]
    raw_text_query: Annotated[str | None, Field(default=None, description="원문에 포함된 키워드")]


class RecordRef(ToolArgs):
    """삭제처럼 대상 지정만 필요한 tool."""

    observation_id: RecordId
