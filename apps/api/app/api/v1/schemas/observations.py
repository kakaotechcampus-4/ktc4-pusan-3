"""관찰 목록 · 상세 응답 — #259 (07 기록 탭 · 기록 상세 시트).

모양은 화면 타입(apps/web `lib/api/types.ts` 의 `ObservationsResponse` ·
`ObservationDetailResponse` · `ObservationPromotable`)을 따른다.

🚨 건강 관찰(`observation_health`)은 목록 · 상세 응답에 실리지 않는다 — 첫 배포 범위 밖이다 (#259).
   `ObservationHealthOut` 은 04 run 의 `saved` 이벤트에만 실린다 (#471).
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


class _ObservationBaseOut(BaseModel):
    """food · education · activity · routine · health 가 같이 가진 칸."""

    id: str
    child_id: str
    raw_text: str
    confidence_source: Literal[
        "institution_notice", "parent_direct", "parent_hedged", "parent_hearsay"
    ]
    status: Literal["active", "stand_alone", "inactive"] = Field(
        description='deleted 는 응답에 나오지 않는다. stand_alone 은 "이번만 그랬어요" 를 누른 기록'
    )
    observed_from: date
    observed_to: date = Field(description="포함 날짜. DB 의 열린 상한에서 하루 뺀 값")
    observed_label: str = Field(description='observed_to 기준 "오늘" · "3일 전". 서버 문구다')
    domain_fields: dict[str, Any] = Field(
        description="표마다 다른 칸 (food: action · amount · reaction 등)"
    )
    source_writer: SourceWriterOut | None = Field(
        default=None, description="적은 보호자. 별명이 없거나 탈퇴해 행이 없으면 null"
    )
    created_at: datetime


class ObservationOut(_ObservationBaseOut):
    kind: ObservationKindOut
    subject: str
    polarity: int = Field(description="-1 싫어함 · 0 갈림 · 1 좋아함")
    strong_signals: list[str] = Field(description="routine 은 승격 대상이 아니라 늘 빈 배열이다")
    affinity: LinkedAffinityOut | None = Field(description="routine 은 늘 null")


class ObservationHealthOut(_ObservationBaseOut):
    """건강 관찰. 승격 파이프라인 밖이라 subject · polarity · strong_signals · affinity 키가 없다.

    화면은 null 검사가 아니라 `kind === "observation_health"` 로 가른다 (apps/web types.ts).
    """

    kind: Literal["observation_health"]


class ObservationsResponse(BaseModel):
    items: list[ObservationOut]
    total: int = Field(description="필터 · 날짜 · 검색을 건 뒤의 전체 건수. 쪽과 무관하다")
    page: int = Field(description="요청한 쪽 번호 (1부터)")
    size: int = Field(description="요청한 한 쪽 개수")
    total_pages: int = Field(description="전체 쪽 수 — ceil(total / size). 0건이면 0")


class UsedInOut(BaseModel):
    suggestion_id: str
    content: str
    status: Literal["draft", "approved", "rejected", "expired"]


class CorrectionOut(BaseModel):
    id: str
    verdict: Literal["once_only", "need_more_observation", "outdated", "wrong"]
    created_at: datetime


class ObservationDetailResponse(BaseModel):
    observation: ObservationOut
    used_in: list[UsedInOut] = Field(
        description='이 관찰을 근거로 쓴 추천. 비면 "제안 근거에서 빠져 있어요"'
    )
    corrections: list[CorrectionOut]
