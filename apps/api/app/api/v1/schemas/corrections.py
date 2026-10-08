"""고치기 요청 · 응답 — #259 (기록 · 기억 고치기).

모양은 화면 타입(apps/web `lib/api/types.ts` 의 `CorrectionRequest` · `Response`)을 따른다.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.v1.schemas.affinities import AffinityOut
from app.api.v1.schemas.common import Ref
from app.api.v1.schemas.observations import ObservationOut

TargetKind = Literal[
    "observation_food",
    "observation_education",
    "observation_activity",
    "observation_routine",
    "profile_affinity",
]


class TargetRef(BaseModel):
    kind: TargetKind = Field(description="observation_health 는 첫 배포 범위 밖이라 받지 않는다")
    id: str = Field(description="기록 또는 기억의 id")


class CorrectionRequest(BaseModel):
    target_ref: TargetRef = Field(description="고칠 대상 1건. 배열이 아니라 객체다")
    verdict: Literal["once_only", "need_more_observation", "outdated", "wrong"] = Field(
        description=(
            "기록은 once_only(이번만 그랬어요) · wrong(잘못된 기록), "
            "기억은 need_more_observation · outdated · wrong. 어긋나면 400"
        )
    )
    child_id: str = Field(description="연결된 보호자의 아이만 가능하다. 아니면 403")


class CorrectionMade(BaseModel):
    id: str
    verdict: Literal["once_only", "need_more_observation", "outdated", "wrong"]
    created_at: datetime


class Cascade(BaseModel):
    affinities_recomputed: list[Ref] = Field(description="이 고치기로 다시 계산한 기억")
    suggestions_recalculated: list[str] = Field(
        description="다시 계산한 추천 id. 추천을 다시 만드는 서버 코드가 아직 없어 늘 빈 배열이다"
    )


class CorrectionResponse(BaseModel):
    correction: CorrectionMade
    target: ObservationOut | AffinityOut = Field(description="고친 뒤의 대상 상태")
    cascade: Cascade
