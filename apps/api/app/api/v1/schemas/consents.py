"""설정 화면 동의 켜고 끄기 — `GET /consents` · `POST /consents` (#181).

🚨 프론트 apps/web/src/lib/api/types.ts 의 ConsentRequest · ConsentResponse · ConsentsResponse ·
   ConsentHistoryEntry 와 같은 모양이어야 한다.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from app.domains.consent.models import ConsentAction

UsableScope = Literal["service_terms", "privacy_account", "location", "child_basic", "child_health"]
"""받는 동의 종류. quality_improve 는 쓰지 않는 값이라 받지 않는다 (consent/models.py)."""


class ConsentRequest(BaseModel):
    """동의 1건을 켜거나(granted) 끈다(withdrawn).

    같은 상태로 다시 보내면 기록을 더하지 않는다. 다만 새 약관 버전에 다시 동의하면 새로 기록한다.
    """

    model_config = ConfigDict(extra="forbid")

    scope: UsableScope = Field(
        description="동의 종류. 필수 동의(service_terms · privacy_account ·"
        " child_basic · child_health)는 끌 수 없다 — 끄는 것이 곧 탈퇴다"
    )
    action: ConsentAction
    policy_version: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        description="화면이 보여 준 약관 버전."
        " 켜기는 GET /policies 가 지금 보여 주는 버전이어야 하고,"
        " 끄기는 등록된 버전이면 받는다. 아니면 400 policy_version_invalid"
    )
    child_id: UUID | None = Field(
        default=None,
        description="아이 동의(child_basic · child_health)일 때만. 계정 동의에 붙이면 400",
    )
    guardian_attested: StrictBool | None = Field(
        default=None,
        description="아이 동의를 켤 때 '법정대리인이며 만 19세 이상' 확인. true 가 아니면 403",
    )


class ConsentOut(BaseModel):
    id: UUID
    scope: str
    action: ConsentAction
    policy_version: str
    acted_at: datetime


class ConsentResponse(BaseModel):
    """201 — 기록한 행(같은 상태였으면 마지막 행)과 지금 상태."""

    consent: ConsentOut
    effective: dict[str, bool] = Field(description="scope → 지금 동의했는가. 서버가 이력에서 계산")


class ConsentHistoryEntry(BaseModel):
    scope: str
    action: ConsentAction
    policy_version: str
    acted_at: datetime


class ConsentsResponse(BaseModel):
    """내 계정 동의와, child_id 를 주면 그 아이의 동의.

    history 는 최신순 증빙이다 — 지우는 길이 없다.
    """

    effective: dict[str, bool] = Field(
        description="scope → 지금 동의했는가. 화면이 history 를 접어 다시 계산하지 않는다"
    )
    history: list[ConsentHistoryEntry]
