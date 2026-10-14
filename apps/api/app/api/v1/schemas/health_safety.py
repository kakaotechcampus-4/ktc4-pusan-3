"""알레르기 · 건강 기록 — `GET` · `POST /children/{cid}/health-safety` · `PATCH` · `DELETE …/{id}`.

🚨 프론트 apps/web/src/lib/api/types.ts 의 HealthSafety · CreateHealthSafetyRequest ·
   CreateHealthSafetyResponse · UpdateHealthSafetyRequest · UpdateHealthSafetyResponse ·
   HealthSafetyListResponse 와 같은 모양이어야 한다.
   DB 의 health_safety.kind 는 API 에서 type 으로 내려간다 (Ref 의 kind 와 이름이 겹쳐서).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domains.safety.models import SafetyKind, SafetySeverity

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reaction = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# 앞뒤 공백을 뗀다. 빈 메모는 라우터가 null 로 맞춘다 — "" 와 null 이 다른 값이면 바뀐 것이 없는
# 고치기가 이력 줄을 만든다
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


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
    reactions: list[Reaction] = Field(
        default_factory=list, max_length=20, description="반응 — '두드러기' 등"
    )
    notes: Notes | None = Field(default=None, description="보호자 메모")


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


class UpdateHealthSafetyRequest(BaseModel):
    """고치기 — 심각도 · 증상 · 메모 중 보낸 것만 바꾼다. 하나도 안 바뀌면 지금 기록 그대로 200.

    고치면 지금 기록은 내려가고(retracted) 바뀐 값으로 새 기록이 생긴다 — 응답의 id 가 바뀐다.
    이력이 남는다: 같은 아이 · 종류 · 이름의 기록을 시간순으로 놓으면 그 항목이 바뀌어 온 길이다.
    종류(type)와 이름(label)은 그 기록의 정체라 받지 않는다(400) — 바꾸려면 내리고 새로 등록한다.
    """

    model_config = ConfigDict(extra="forbid")

    severity: SafetySeverity | None = Field(
        default=None,
        description=(
            "알레르기는 class_0~class_6, 나머지는 mild~anaphylaxis. 어긋나면 400. "
            "null 이면 '모르겠어요' 로 되돌린다. 보내지 않으면 그대로"
        ),
    )
    # 🚨 타입에 `| None` 을 붙이지 않는다 — 증상을 비우는 것은 [] 이고, null 은 400 이다.
    reactions: list[Reaction] = Field(
        default_factory=list, max_length=20, description="보내면 통째로 바꾼다. 비우려면 []"
    )
    notes: Notes | None = Field(default=None, description="null 이면 비운다. 보내지 않으면 그대로")


class UpdateHealthSafetyResponse(BaseModel):
    """200 — 고친 뒤의 기록. 바뀌었으면 새 id 다."""

    safety: HealthSafetyOut


class HealthSafetyListResponse(BaseModel):
    """지금 고려 대상(active)인 기록 — 보호자가 확정한 것만 들어 있다 (§2)."""

    items: list[HealthSafetyOut]
    updated_at: datetime = Field(
        description="목록의 가장 최근 변경 시각. 기록이 없으면 응답한 시각"
    )
