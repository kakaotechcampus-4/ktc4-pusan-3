"""알레르기 · 건강 기록 — `GET` · `POST /children/{cid}/health-safety` · `DELETE …/{id}`.

🚨 프론트 apps/web/src/lib/api/types.ts 의 HealthSafety · CreateHealthSafetyRequest ·
   CreateHealthSafetyResponse · HealthSafetyListResponse 와 같은 모양이어야 한다.
   DB 의 health_safety.kind 는 API 에서 type 으로 내려간다 (Ref 의 kind 와 이름이 겹쳐서).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domains.safety.models import SafetyKind, SafetySeverity

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class CreateHealthSafetyRequest(BaseModel):
    """승인 게이트 ㉡ — 보호자가 확인한 값만 들어온다. LLM 이 이 요청을 만들지 않는다 (§2).

    심각도 · 메모의 null 은 "안 보냄" 과 같다.
    """

    # null 을 400 으로 막지 않는 이유 — 웹 HealthSafetyRequest 타입이 null 을 허용한다
    model_config = ConfigDict(extra="forbid")

    type: SafetyKind = Field(
        description="allergy · chronic_disease · behavioral · environmental · other_medical"
    )
    label: Label = Field(description="구체적 대상 — '우유' · '천식'. 앞뒤 공백을 떼고 1~100자")
    severity: SafetySeverity | None = Field(
        default=None,
        description=(
            "알레르기는 class_0~class_6, 나머지는 mild~anaphylaxis. 어긋나면 400. "
            "모르면 보내지 않거나 null"
        ),
    )
    reactions: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    ] = Field(default_factory=list, max_length=20, description="반응 — '두드러기' 등")
    notes: Annotated[str, StringConstraints(max_length=500)] | None = Field(
        default=None, description="보호자 메모"
    )


class WrittenBy(BaseModel):
    parent_id: UUID
    nickname: str = Field(description="적은 보호자의 부르는 이름")


class HealthSafetyOut(BaseModel):
    kind: Literal["health_safety"] = "health_safety"
    id: UUID
    type: SafetyKind
    label: str
    severity: SafetySeverity | None
    reactions: list[str]
    management: str | None = Field(
        description="알레르기 · 질환 관리 정보."
        " 등록 요청으로는 받지 않아 이 API 로 만든 기록은 null"
    )
    notes: str | None
    created_by: WrittenBy | None = Field(
        description="적은 보호자. 탈퇴했거나 부르는 이름이 없는 계정이면 null"
    )
    updated_at: datetime


class CreateHealthSafetyResponse(BaseModel):
    """201 — 등록한 기록."""

    safety: HealthSafetyOut


class HealthSafetyListResponse(BaseModel):
    """지금 고려 대상(active)인 기록 — 보호자가 확정한 것만 들어 있다 (§2)."""

    items: list[HealthSafetyOut]
    updated_at: datetime = Field(
        description="목록의 가장 최근 변경 시각. 기록이 없으면 응답한 시각"
    )
