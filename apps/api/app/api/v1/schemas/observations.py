"""관찰 목록 · 상세 응답 — #259 (07 기록 탭 · 기록 상세 시트).

모양은 화면 타입(apps/web `lib/api/types.ts` 의 `ObservationsResponse` ·
`ObservationDetailResponse` · `ObservationPromotable`)을 따른다.

🚨 건강 관찰(`observation_health`)은 이 응답에 실리지 않는다 — 첫 배포 범위 밖이다 (#259).
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ObservationKindOut = Literal[
    "observation_food", "observation_education", "observation_activity", "observation_routine"
]


class LinkedAffinityOut(BaseModel):
    """이 관찰이 묶인 기억. 화면은 "묶인 기억" 줄에 merge_key 를 그린다."""

    id: str
    merge_key: str
    state: str


class SourceWriterOut(BaseModel):
    parent_id: str
    nickname: str


class ObservationOut(BaseModel):
    id: str
    child_id: str
    kind: ObservationKindOut
    raw_text: str
    subject: str
    polarity: int = Field(description="-1 싫어함 · 0 갈림 · 1 좋아함")
    strong_signals: list[str] = Field(description="routine 은 승격 대상이 아니라 늘 빈 배열이다")
    confidence_source: Literal[
        "institution_notice", "parent_direct", "parent_hedged", "parent_hearsay"
    ]
    status: Literal["active", "stand_alone", "inactive"] = Field(
        description='deleted 는 응답에 나오지 않는다. stand_alone 은 "이번만 그랬어요" 를 누른 기록'
    )
    observed_from: date
    observed_to: date = Field(description="포함 날짜. DB 의 열린 상한에서 하루 뺀 값")
    observed_label: str = Field(description='observed_to 기준 "오늘" · "3일 전". 서버 문구다')
    affinity: LinkedAffinityOut | None = Field(description="routine 은 늘 null")
    domain_fields: dict[str, Any] = Field(
        description="표마다 다른 칸 (food: action · amount · reaction 등)"
    )
    source_writer: SourceWriterOut | None = Field(
        default=None, description="적은 보호자. 별명이 없거나 탈퇴해 행이 없으면 null"
    )
    created_at: datetime


class ObservationsResponse(BaseModel):
    items: list[ObservationOut]
    next_cursor: str | None
    total: int = Field(description="필터를 건 뒤의 건수")


class UsedInOut(BaseModel):
    suggestion_id: str
    content: str
    status: Literal["draft", "approved", "rejected", "expired"]


class CorrectionOut(BaseModel):
    id: str
    verdict: Literal["confirm", "once_only", "need_more_observation", "outdated", "wrong"]
    created_at: datetime


class ObservationDetailResponse(BaseModel):
    observation: ObservationOut
    used_in: list[UsedInOut] = Field(
        description='이 관찰을 근거로 쓴 추천. 비면 "제안 근거에서 빠져 있어요"'
    )
    corrections: list[CorrectionOut]
