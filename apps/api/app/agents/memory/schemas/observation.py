"""observation 5테이블(food / health / education / activity / routine)의 tool argument 스키마.

각 테이블마다 create / query / update 3종 = 15개. 삭제는 update 의 status=deleted 다.
LLM 이 채우지 않는 필드(id · child_id · source_writer · observed_range 등)는 노출하지 않는다.
"""

from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import Field, model_validator

from app.agents.memory.schemas.common import (
    MAX_DURATION_MIN,
    AssistanceLevel,
    CompletionStatus,
    ConfidenceSource,
    DateExpr,
    Direction,
    EngagementLevel,
    ObservationCreateArgs,
    ObservationQueryArgs,
    ObservationUpdateArgs,
    PromotableCreateArgs,
    RawText,
    RoutineCategory,
    Severity,
    SubjectCreateArgs,
)

_DURATION = Field(
    default=None,
    ge=1,
    le=MAX_DURATION_MIN,
    description=f"분 단위. 하루 종일이면 {MAX_DURATION_MIN}. 발화에 없으면 비워둔다",
)


def _optional(description: str) -> Any:
    """말에 나오지 않으면 비우는 선택 필드."""
    return Field(default=None, description=description)


_ENGAGEMENT = _optional("low / mid / high 중 하나. 발화에 없으면 비워둔다")
_FOOD_SUBJECT = (
    "먹거나 마신 것의 이름. 예: 딸기케이크, 김밥, 우유. "
    "'아침·점심·저녁·간식' 은 끼니 이름이라 subject 가 될 수 없다. "
    "무엇을 먹었는지 발화에 없으면 이 tool을 부르지 않고, 메뉴 이름에 대해 되묻는다. "
)


class ObservationFoodCreate(PromotableCreateArgs):
    subject: Annotated[str, Field(description=_FOOD_SUBJECT)]
    action: Annotated[str | None, Field(default=None, description="먹었다 / 뱉었다 / 남김")]
    amount: Annotated[str | None, Field(default=None, description="반 그릇 / 다 먹음")]
    reaction: Annotated[str | None, Field(default=None, description="좋아함 / 싫어함 / 무반응")]


class ObservationFoodUpdate(ObservationUpdateArgs):
    CLEARABLE: ClassVar[frozenset[str]] = frozenset({"action", "amount", "reaction"})
    subject: Annotated[str | None, _optional("바꿀 음식 이름. 끼니 이름은 넣지 않는다")]
    action: Annotated[str | None, Field(default=None, description="바꿀 action")]
    amount: Annotated[str | None, Field(default=None, description="바꿀 amount")]
    reaction: Annotated[str | None, Field(default=None, description="바꿀 reaction")]


# 체온 — 보호자가 말한 숫자 그대로(℃). 범위 밖은 저장하지 않고 되묻는다. DB CHECK 와 같은 범위
_TEMPERATURE = Field(
    default=None,
    ge=Decimal("30.0"),
    le=Decimal("45.0"),
    decimal_places=1,
    description=(
        "체온 숫자만. 예: 38.5. 말한 숫자를 바꾸거나 단위를 바꾸지 않는다. "
        "30.0~45.0 밖이면 넣지 않고 보호자에게 되묻는다"
    ),
)
MeasureSite = Literal["ear", "forehead", "armpit", "oral", "rectal"]
_MEASURE_SITE = _optional(
    "잰 부위. 귀 ear / 이마 forehead / 겨드랑이 armpit / 입 oral / 항문 rectal. "
    "말에 부위가 있을 때만"
)
_MEASURED_AT = _optional("체온을 잰 시각. 예: 밤 11시. 증상을 본 시각(observed_time)과 다르다")
_MEDICATION_GIVEN = Field(
    default=None,
    min_length=1,
    description=(
        "이미 먹인 약 이름을 raw_text 에 있는 글자 그대로. 예: 해열제, 타이레놀. "
        "성분명이나 다른 이름으로 바꾸지 않는다"
    ),
)

# 증상 행에만 있는 칸. 약만 말했으면 얹을 증상 행이 없다.
# 수정 tool 도 이 표로 약 행에 증상 칸을 얹는지 본다(observed_time 은 약 행에선 먹인 시각이라 뺀다)
SYMPTOM_ONLY = (
    "severity",
    "body_part",
    "suspected_trigger",
    "action_taken",
    "observed_time",
    "temperature",
    "measured_at",
    "measure_site",
)


class ObservationHealthCreate(ObservationCreateArgs):
    """증상과 컨디션, 이미 먹인 약 기록. 진단하지 않는다.

    한 번 호출에 증상과 먹인 약을 같이 받는다. 저장할 때 코드가 증상 행과 약 행으로 나누고
    약 행에 증상 행을 잇는다 — 잇는 id 는 인자에 없다.
    """

    symptom: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="증상. 예: 발열, 콧물, 두드러기. 먹인 약만 말했으면 비운다",
        ),
    ]
    severity: Annotated[Severity | None, _optional("추측하지 않고 정도가 드러날 때만")]
    body_part: Annotated[str | None, _optional("예: 얼굴 / 팔. 발화에 드러날 때만")]
    suspected_trigger: Annotated[str | None, _optional("예: 우유 먹은 뒤. 발화에 드러날 때만")]
    action_taken: Annotated[
        str | None, _optional("병원 / 찜질 등. 발화에 드러날 때만. 먹인 약은 medication_given")
    ]
    observed_time: Annotated[str | None, _optional("증상을 본 시각만. 예: 오전 8시, 15:30")]
    temperature: Annotated[Decimal | None, _TEMPERATURE]
    measured_at: Annotated[str | None, _MEASURED_AT]
    measure_site: Annotated[MeasureSite | None, _MEASURE_SITE]
    medication_given: Annotated[str | None, _MEDICATION_GIVEN]
    given_time: Annotated[str | None, _optional("약을 먹인 시각. 예: 새벽 3시. 말했을 때만")]

    @model_validator(mode="after")
    def _symptom_or_medication(self) -> Self:
        if not self.symptom and self.medication_given is None:
            raise ValueError(
                "symptom 과 medication_given 이 둘 다 비었다. 말한 증상을 symptom 에, "
                "이미 먹인 약을 medication_given 에 넣는다. 둘 다 없으면 이 tool 을 부르지 않는다"
            )
        if self.given_time is not None and self.medication_given is None:
            raise ValueError("given_time 은 먹인 약 시각이다. medication_given 과 같이 넣는다")
        filled = [name for name in SYMPTOM_ONLY if getattr(self, name) is not None]
        if not self.symptom and filled:
            raise ValueError(
                f"{', '.join(filled)} 는 증상 기록의 칸이다. 증상 없이 먹인 약만 말했으면 비운다. "
                "먹인 시각은 given_time 에 넣는다"
            )
        return self


class ObservationHealthUpdate(ObservationUpdateArgs):
    CLEARABLE: ClassVar[frozenset[str]] = frozenset(
        {
            "severity",
            "body_part",
            "suspected_trigger",
            "action_taken",
            "observed_time",
            "temperature",
            "measured_at",
            "measure_site",
        }
    )
    symptom: Annotated[
        list[str] | None, Field(default=None, min_length=1, description="바꿀 증상 목록")
    ]
    body_part: Annotated[str | None, Field(default=None, description="바꿀 신체 부위")]
    suspected_trigger: Annotated[str | None, _optional("바꿀 계기")]
    severity: Annotated[Severity | None, Field(default=None, description="바꿀 정도")]
    action_taken: Annotated[str | None, Field(default=None, description="바꿀 조치")]
    observed_time: Annotated[
        str | None, _optional("바꿀 시각. 예: 오전 8시, 15:30. 먹인 약 기록이면 먹인 시각")
    ]
    temperature: Annotated[Decimal | None, _TEMPERATURE]
    measured_at: Annotated[str | None, _optional("바꿀 잰 시각. 예: 밤 11시")]
    measure_site: Annotated[MeasureSite | None, _optional("바꿀 잰 부위")]
    medication_given: Annotated[
        str | None,
        Field(
            default=None,
            min_length=1,
            description="먹인 약 기록의 약 이름을 고칠 때만. 증상 기록에 약을 덧붙이지 않는다",
        ),
    ]


class ObservationEducationCreate(PromotableCreateArgs):
    topic: Annotated[str, Field(description="발화 그대로의 학습 주제. 예: 한글 자모 활동지")]
    session_type: Annotated[str | None, _optional("독서 / 수업 / 학습지 등")]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]


class ObservationEducationUpdate(ObservationUpdateArgs):
    CLEARABLE: ClassVar[frozenset[str]] = frozenset(
        {"session_type", "duration_min", "engagement_level"}
    )
    topic: Annotated[str | None, Field(default=None, description="바꿀 학습 주제")]
    subject: Annotated[str | None, _optional("바꿀 정규화 대상. topic을 바꾸면 같이 바꾼다")]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]
    session_type: Annotated[str | None, Field(default=None, description="바꿀 세션 형태")]


class ObservationActivityCreate(PromotableCreateArgs):
    activity: Annotated[str, Field(description="발화 그대로의 활동. 예: 레고로 성 만들기")]
    location: Annotated[str | None, _optional("장소. 집 / 기관 / 놀이터")]
    companions: Annotated[str | None, _optional("혼자 / 친구와 / 부모")]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]


class ObservationActivityUpdate(ObservationUpdateArgs):
    CLEARABLE: ClassVar[frozenset[str]] = frozenset(
        {"location", "companions", "duration_min", "engagement_level"}
    )
    activity: Annotated[str | None, Field(default=None, description="바꿀 활동")]
    subject: Annotated[str | None, _optional("바꿀 정규화 대상. activity를 바꾸면 같이 바꾼다")]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]
    location: Annotated[str | None, Field(default=None, description="바꿀 장소")]
    companions: Annotated[str | None, Field(default=None, description="바꿀 동반자")]


_ROUTINE_SUBJECT = (
    "행동 이름으로 정규화한 대상. 예: 양치하기, 손톱 물어뜯기, 인사하기, 장난감 정리. "
    "다른 도메인과 달리 명사만 남기지 않고 행동을 이름으로 남긴다"
)
_ROUTINE_CATEGORY = (
    "self_care(양치/옷 입기/손 씻기) / mealtime(식사 도구 및 태도) / "
    "household_task(정리/심부름) / social_manner(인사/차례 지키기/친구 도와주기·달래기) / "
    "habit(손톱 물어뜯기/손가락 빨기) / transition(등원 준비/잠자리 들기/놀이 끝내기)"
)
_ASSISTANCE = _optional(
    "해내는 데 필요했던 도움. 시키지 않아도 스스로 했으면 independent, "
    "말로 시켜서 했으면 verbal_prompt(시킨 뒤에 혼자 했어도), 일부 도와줬으면 partial_assist, "
    "거의 다 해줬으면 full_assist. 발화에 드러날 때만"
)
_COMPLETION = _optional("completed / partial / refused / interrupted. 해냈는지 드러날 때만")


class ObservationRoutineCreate(SubjectCreateArgs):
    """생활 행동·자립 수행·습관·사회적 생활기술. 습관은 증상이 아니다."""

    subject: Annotated[str, Field(description=_ROUTINE_SUBJECT)]
    routine_category: Annotated[RoutineCategory, Field(description=_ROUTINE_CATEGORY)]
    context: Annotated[
        str | None,
        _optional("행동이 나타난 상황. 예: 식사 중, 등원 준비, 잠들기 전. 발화에 드러날 때만"),
    ]
    assistance_level: Annotated[AssistanceLevel | None, _ASSISTANCE]
    completion_status: Annotated[CompletionStatus | None, _COMPLETION]
    trigger: Annotated[
        str | None,
        _optional("행동을 부른 계기. 예: 긴장할 때, 정리하라고 했을 때. 발화에 드러날 때만"),
    ]


class ObservationRoutineUpdate(ObservationUpdateArgs):
    CLEARABLE: ClassVar[frozenset[str]] = frozenset(
        {"context", "assistance_level", "completion_status", "trigger"}
    )
    subject: Annotated[str | None, _optional("바꿀 행동 이름")]
    routine_category: Annotated[RoutineCategory | None, _optional("바꿀 행동 종류")]
    context: Annotated[str | None, Field(default=None, description="바꿀 상황")]
    assistance_level: Annotated[AssistanceLevel | None, _optional("바꿀 도움 정도")]
    completion_status: Annotated[CompletionStatus | None, _optional("바꿀 수행 결과")]
    trigger: Annotated[str | None, Field(default=None, description="바꿀 계기")]


__all__ = [
    "ConfidenceSource",
    "DateExpr",
    "Direction",
    "ObservationActivityCreate",
    "ObservationActivityUpdate",
    "ObservationEducationCreate",
    "ObservationEducationUpdate",
    "ObservationFoodCreate",
    "ObservationFoodUpdate",
    "ObservationHealthCreate",
    "ObservationHealthUpdate",
    "ObservationQueryArgs",
    "ObservationRoutineCreate",
    "ObservationRoutineUpdate",
    "ObservationUpdateArgs",
    "RawText",
]
