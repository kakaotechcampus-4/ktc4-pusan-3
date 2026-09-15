"""observation 5테이블(food / health / education / activity / routine)의 tool argument 스키마.

각 테이블마다 create / query / update / delete 4종 = 20개.
LLM 이 채우지 않는 필드(id · child_id · source_writer · observed_range 등)는 노출하지 않는다.
"""

from typing import Annotated, Any

from pydantic import Field

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
    RecordRef,
    RoutineCategory,
    Severity,
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


class ObservationFoodCreate(PromotableCreateArgs):
    action: Annotated[str | None, Field(default=None, description="먹었다 / 뱉었다 / 남김")]
    amount: Annotated[str | None, Field(default=None, description="반 그릇 / 다 먹음")]
    reaction: Annotated[str | None, Field(default=None, description="좋아함 / 싫어함 / 무반응")]


class ObservationFoodUpdate(ObservationUpdateArgs):
    subject: Annotated[str | None, _optional("바꿀 정규화 대상")]
    action: Annotated[str | None, Field(default=None, description="바꿀 action")]
    amount: Annotated[str | None, Field(default=None, description="바꿀 amount")]
    reaction: Annotated[str | None, Field(default=None, description="바꿀 reaction")]


class ObservationHealthCreate(ObservationCreateArgs):
    """증상과 컨디션 기록. 진단하지 않는다."""

    symptom: Annotated[list[str], Field(min_length=1, description="증상. 예: 발열, 콧물, 두드러기")]
    severity: Annotated[Severity | None, _optional("추측하지 않고 정도가 드러날 때만")]
    body_part: Annotated[str | None, _optional("예: 얼굴 / 팔. 발화에 드러날 때만")]
    suspected_trigger: Annotated[str | None, _optional("예: 우유 먹은 뒤. 발화에 드러날 때만")]
    action_taken: Annotated[str | None, _optional("병원 / 해열제 등. 발화에 드러날 때만")]
    observed_time: Annotated[str | None, _optional("증상을 본 시각만. 예: 오전 8시, 15:30")]


class ObservationHealthUpdate(ObservationUpdateArgs):
    symptom: Annotated[list[str] | None, Field(default=None, description="바꿀 증상 목록")]
    body_part: Annotated[str | None, Field(default=None, description="바꿀 신체 부위")]
    suspected_trigger: Annotated[str | None, _optional("바꿀 계기")]
    severity: Annotated[Severity | None, Field(default=None, description="바꿀 정도")]
    action_taken: Annotated[str | None, Field(default=None, description="바꿀 조치")]
    observed_time: Annotated[str | None, _optional("바꿀 시각. 예: 오전 8시, 15:30")]


class ObservationEducationCreate(PromotableCreateArgs):
    topic: Annotated[str, Field(description="발화 그대로의 학습 주제. 예: 한글 자모 활동지")]
    session_type: Annotated[str | None, _optional("독서 / 수업 / 학습지 등")]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]


class ObservationEducationUpdate(ObservationUpdateArgs):
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
    "household_task(정리/심부름) / social_manner(인사/차례 지키기) / "
    "habit(손톱 물어뜯기/손가락 빨기) / transition(등원 준비/잠자리 들기/놀이 끝내기)"
)
_ASSISTANCE = _optional(
    "해내는 데 필요했던 도움. 시키지 않아도 스스로 했으면 independent, "
    "말로 시켜서 했으면 verbal_prompt(시킨 뒤에 혼자 했어도), 일부 도와줬으면 partial_assist, "
    "거의 다 해줬으면 full_assist. 발화에 드러날 때만"
)
_COMPLETION = _optional("completed / partial / refused / interrupted. 해냈는지 드러날 때만")


class ObservationRoutineCreate(PromotableCreateArgs):
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
    "RecordRef",
]
