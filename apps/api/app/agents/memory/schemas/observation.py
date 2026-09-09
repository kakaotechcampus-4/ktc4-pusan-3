"""observation 4테이블(food / health / education / activity)의 tool argument 스키마.

각 테이블마다 create / query / update / delete 4종 = 16개.
LLM 이 채우지 않는 필드(id · child_id · source_writer · observed_range 등)는 노출하지 않는다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.memory.schemas.common import (
    MAX_DURATION_MIN,
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
    Severity,
)

_DURATION = Field(
    default=None,
    ge=1, le=MAX_DURATION_MIN,
    description=f"분 단위. 하루 종일이면 {MAX_DURATION_MIN}. 발화에 없으면 비워둔다",
)
_ENGAGEMENT = Field(default=None, description="몰입도 정보. low / mid / high 중 하나로, 발화에 없으면 비워둔다")


class ObservationFoodCreate(PromotableCreateArgs):
    subject: Annotated[str, Field(description="대상 음식. 예: 당근, 닭갈비")]
    action: Annotated[str | None, Field(default=None, description="먹었다 / 뱉었다 / 남김")]
    amount: Annotated[str | None, Field(default=None, description="반 그릇 / 다 먹음")]
    reaction: Annotated[str | None, Field(default=None, description="좋아함 / 싫어함 / 무반응")]


class ObservationFoodUpdate(ObservationUpdateArgs):
    subject: Annotated[str | None, Field(default=None, description="바꿀 음식")]
    action: Annotated[str | None, Field(default=None, description="바꿀 action")]
    amount: Annotated[str | None, Field(default=None, description="바꿀 amount")]
    reaction: Annotated[str | None, Field(default=None, description="바꿀 reaction")]


class ObservationHealthCreate(ObservationCreateArgs):
    """증상과 컨디션 기록. 진단하지 않는다."""

    symptom: Annotated[list[str], Field(min_length=1, description="증상. 예: 발열, 콧물, 두드러기")]
    severity: Annotated[
        Severity | None,
        Field(default=None, description="mild / moderate / severe / emergency. 추측하지 않고 발화에 정도가 드러날 때만 채우기"),
    ]
    body_part: Annotated[str | None, Field(default=None, description="증상이 나타난 신체 부위. 예: 얼굴 / 팔. 발화에 드러날 때만 채우기.")]
    suspected_trigger: Annotated[
        str | None,
        Field(
            default=None,
            description="증상이 관찰된 계기. 예: 우유 먹은 뒤. 발화에 드러날 때만 채우기.",
        ),
    ]
    action_taken: Annotated[str | None, Field(default=None, description="병원 / 해열제 / 경과 지켜봄. 발화에 드러날 때만 채우기.")]
    observed_time: Annotated[
        str | None, Field(default=None,
            description=(
                "증상을 본 시각. 예: 오전 8시, 저녁 7시, 15:30. "
                "날짜는 observed_on 이 담당한다. 발화에 드러날 때만 채우기.")
            )]


class ObservationHealthUpdate(ObservationUpdateArgs):
    symptom: Annotated[list[str] | None, Field(default=None, description="바꿀 증상 목록")]
    body_part: Annotated[str | None, Field(default=None, description="바꿀 신체 부위")]
    suspected_trigger: Annotated[
        str | None,
        Field(default=None, description="바꿀 증상이 관찰된 계기."),
    ]
    severity: Annotated[Severity | None, Field(default=None, description="바꿀 정도")]
    action_taken: Annotated[str | None, Field(default=None, description="바꿀 조치")]
    observed_time: Annotated[str | None, Field(default=None, description="바꿀 시각. 예: 오전 8시, 15:30")]


class ObservationEducationCreate(PromotableCreateArgs):
    topic: Annotated[str, Field(description="학습 주제. 예: 숫자세기, 영어 말하기")]
    session_type: Annotated[
        str | None,
        Field(default=None, description="독서 / 수업 / 학습지 등"),
    ]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]


class ObservationEducationUpdate(ObservationUpdateArgs):
    topic: Annotated[str | None, Field(default=None, description="바꿀 학습 주제")]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]
    session_type: Annotated[str | None, Field(default=None, description="바꿀 세션 형태")]


class ObservationActivityCreate(PromotableCreateArgs):
    activity: Annotated[str, Field(description="활동 이름. 예: 모래놀이, 레고 조립")]
    location: Annotated[str | None, Field(default=None, description="장소 정보. 집 / 기관 / 놀이터")]
    companions: Annotated[str | None, Field(default=None, description="활동 참여자. 혼자 / 친구와 / 부모")]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]


class ObservationActivityUpdate(ObservationUpdateArgs):
    activity: Annotated[str | None, Field(default=None, description="바꿀 활동")]
    duration_min: Annotated[int | None, _DURATION]
    engagement_level: Annotated[EngagementLevel | None, _ENGAGEMENT]
    location: Annotated[str | None, Field(default=None, description="바꿀 장소")]
    companions: Annotated[str | None, Field(default=None, description="바꿀 동반자")]


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
    "ObservationUpdateArgs",
    "RawText",
    "RecordRef",
]
