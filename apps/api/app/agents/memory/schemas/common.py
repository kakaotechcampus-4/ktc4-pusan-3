# tool 스키마가 공유하는 enum/상수/공통 필드.

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALL_DAY_MIN = 1440  # "하루 종일" = 하루(1440분)
MAX_DURATION_MIN = ALL_DAY_MIN  # 활동/학습 세션은 하루를 넘길 수 없음


class TemporalDirection(StrEnum):
    """ "금요일"처럼 여러 해석이 가능한 날짜 표현을 과거/미래 중 어느 쪽으로 읽을지 방향을 결정."""

    PAST = "past"
    FUTURE = "future"
    NEAREST = "nearest"


class ConfidenceSource(StrEnum):
    """관찰 정보의 출처."""

    INSTITUTION_NOTICE = "institution_notice"  # 알림장/기관 공지
    PARENT_DIRECT = "parent_direct"  # 보호자가 직접 본 것
    PARENT_HEDGED = "parent_hedged"  # "~한 것 같아" 처럼 확신이 약한 진술
    PARENT_HEARSAY = "parent_hearsay"  # "선생님 말로는" 처럼 전해 들은 것


class Severity(StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"
    EMERGENCY = "emergency"


class EngagementLevel(StrEnum):
    LOW = "low"
    MID = "mid"
    HIGH = "high"


class RoutineCategory(StrEnum):
    """생활 행동의 종류."""

    SELF_CARE = "self_care"  # 양치 / 옷 입기 / 손 씻기
    MEALTIME = "mealtime"  # 식사 도구 / 식사 태도 — 무엇을 먹었는지는 food
    HOUSEHOLD_TASK = "household_task"  # 장난감 정리 / 심부름
    SOCIAL_MANNER = "social_manner"  # 인사 / 차례 지키기
    HABIT = "habit"  # 손톱 물어뜯기 / 손가락 빨기 (증상 X, 단순 버릇)
    TRANSITION = "transition"  # 등원 준비 / 잠자리 들기 / 놀이 끝내기


class AssistanceLevel(StrEnum):
    """얼마나 도움을 받아 해냈는지."""

    INDEPENDENT = "independent"  # 혼자
    VERBAL_PROMPT = "verbal_prompt"  # 말로 시켜야 함
    PARTIAL_ASSIST = "partial_assist"  # 일부 도와줘야 함
    FULL_ASSIST = "full_assist"  # 거의 다 해줘야 함


class CompletionStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    REFUSED = "refused"
    INTERRUPTED = "interrupted"


class StrongSignal(StrEnum):
    """성향 판단에 무게를 주는 행동 신호. 발화에 근거가 있을 때만 붙인다."""

    RESISTANCE_TO_REDIRECT = "resistance_to_redirect"
    SELF_INITIATED = "self_initiated"
    COMPARATIVE_CHOICE = "comparative_choice"
    ASKS_QUESTIONS = "asks_questions"
    ROLE_EXTENSION = "role_extension"


class EventType(StrEnum):
    CORE = "core"  # 일상적으로 반복되는 일정
    EPISODIC = "episodic"  # 단발성 일정


class EventCategory(StrEnum):
    INSTITUTION = "institution"
    HEALTH = "health"
    ACTIVITY = "activity"
    ETC = "etc"


# 날짜·시각은 원문 표현으로 받고 실제 계산은 common/datetime_rules.py에서 수행
DateExpr = Annotated[
    str,
    Field(description="원문의 날짜 표현. 예: 오늘, 모레, 금요일, 다음 주 목요일, 2023년 5월 3일"),
]

TimeExpr = Annotated[
    str | None,
    Field(
        default=None,
        description="원문의 시각 표현. 예: 오전 8시, 저녁 8시, 15:30. 발화에 없으면 비워둔다",
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


class ClearableUpdateArgs(ToolArgs):
    """이미 저장된 선택 필드를 비울 수 있는 update의 공통 base.

    필드의 None의 의미: "안 바꿈"
    값 비움: clear에 컬럼 이름 명시. 관찰은 인자 이름과 같고 event의 ends_at만 다르다
    CLEARABLE: 하위 스키마가 정하는 비울 수 있는 컬럼(NOT NULL 컬럼은 포함X)
    """

    CLEARABLE: ClassVar[frozenset[str]] = frozenset()

    clear: Annotated[
        list[str],
        Field(
            default_factory=list,
            description=(
                "값을 지울 필드 이름. 보호자가 이미 기록된 내용을 빼 달라고 할 때만 넣는다. "
                "발화에 없는 필드는 여기 넣지 않고 그냥 비워 둔다. 비워 둔 필드는 변경하지 않는다"
            ),
        ),
    ]

    @field_validator("clear")
    @classmethod
    def _only_clearable(cls, names: list[str]) -> list[str]:
        if not set(names) <= cls.CLEARABLE:
            raise ValueError(
                f"이 tool 에서 지울 수 있는 필드는 {_joined(cls.CLEARABLE)} 뿐이다. "
                "나머지는 지우지 않고 새 값으로 바꾼다"
            )
        return names

    @model_validator(mode="after")
    def _clear_or_set(self) -> Self:
        # ends_at 은 인자가 아니라 여기선 안 걸린다. ends_on·ends_time 과 겹치면 check_when 이 막음
        both = [name for name in self.clear if getattr(self, name, None) is not None]
        if both:
            raise ValueError(
                f"{_joined(both)} 에 새 값과 지우기가 함께 왔다. 값을 바꿀 거면 clear 에서 빼고, "
                "지울 거면 값을 비워 둔다"
            )
        return self


def _joined(names: Iterable[str]) -> str:
    return ", ".join(sorted(set(names)))


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
            description=(
                "보호자가 직접 관찰했거나 아이의 발언이면 parent_direct, "
                "전해 들었으면 parent_hearsay, 기관 공지면 institution_notice, "
                "확신이 약하면 parent_hedged"
            ),
        ),
    ]


class SubjectCreateArgs(ObservationCreateArgs):
    """`subject` · `polarity` 를 받는 네 도메인(food/education/activity/routine).

    routine 은 승격되지 않지만 관찰 자체가 티어 3 근거로 인용돼 이 두 칸은 받는다.
    """

    subject: Annotated[
        str,
        Field(
            description=(
                "핵심 대상 하나만 남긴 정규화 형태. 병합·검색 키로 쓰인다. "
                "'레고로 성 만들기' → '레고', '한글 자모 활동지' → '한글 자모'. "
                "수식어나 행동은 빼고 명사만 남긴다. "
                "'레고로 성 만들기'와 '레고로 성 쌓기'는 같은 subject다."
            )
        ),
    ]
    polarity: Annotated[
        int,
        Field(default=0, ge=-1, le=1, description="좋아함 1 / 중립 0 / 싫어함 -1"),
    ]


class PromotableCreateArgs(SubjectCreateArgs):
    """승격까지 가는 셋(food/education/activity) 전용. routine 에는 이 칸이 없다."""

    strong_signals: Annotated[
        list[StrongSignal],
        Field(default_factory=list, description="발화에 근거가 있을 때만. 없으면 빈 배열"),
    ]


class ObservationUpdateArgs(ClearableUpdateArgs):
    """observation update 가 공유하는 필드. 날짜도 고칠 수 있다."""

    observation_id: RecordId
    observed_on: Annotated[
        str | None,
        Field(default=None, description="바꿀 관찰 날짜 표현. 날짜를 안 바꾸면 비운다"),
    ]
    temporal_direction: Direction


class ObservationQueryArgs(ToolArgs):
    """observation 조회 조건. 자유 검색 대신 허용 필드만 연다."""

    temporal_direction: Annotated[
        TemporalDirection,
        Field(
            default=TemporalDirection.PAST,
            description=(
                "날짜 표현을 과거로 볼지 미래로 볼지. 이미 기록된 관찰을 찾는 것이므로 "
                "보통 past 다. 앞으로의 날짜를 찾을 때만 future"
            ),
        ),
    ]
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
