"""아이 엔드포인트의 요청·응답 — 계약서 §04 · §06.

🚨 프론트 apps/web/src/lib/api/types.ts 의 CreateChildRequest · CreateChildResponse ·
   CreateInputRequest · CreateInputResponse 와 같은 모양이어야 한다.
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.api.v1.schemas.auth import ConsentInput, Nickname

Role = Literal["owner", "member"]
"""아이의 owner(등록한 보호자)인가, 초대로 연결된 member 인가 — child.owner_parent_id 와 비교."""


class CreateChildRequest(BaseModel):
    """POST /children — 아이 정보와 아이 동의를 한 요청에 (#92 결정, 2026-09-18)."""

    nickname: Nickname = Field(description="아이를 부르는 이름. 앞뒤 공백을 떼고 1~20자")
    birth_date: date = Field(description="생일 (YYYY-MM-DD). 한국 날짜로 오늘보다 뒤면 400")
    consents: list[ConsentInput] = Field(
        description="아이 동의 — child_basic · child_health 둘 다 필수. 버전은 GET /policies 의 것"
    )
    guardian_attested: bool = Field(
        description="'나는 이 아이의 법정대리인이며 만 19세 이상입니다' 체크. true 가 아니면 403"
    )


class CreateChildResponse(BaseModel):
    """201 — 등록한 아이. 등록한 보호자는 항상 owner 다."""

    id: UUID
    nickname: str
    age_display: str = Field(description="화면에 그대로 쓰는 나이 문구 (생후 N일 · N개월 · 만 N세)")
    role: Role


class CreateInputRequest(BaseModel):
    """POST /children/{cid}/inputs — 한 줄 입력."""

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    """보호자가 적은 한 줄. 공백뿐이면 400 — 빈 줄을 Agent 에 보내면 모델 호출 한 번이 낭비다."""

    source: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    """어디서 온 입력인가 — home_input · photo 등. 계약서가 값을 고정하지 않아 문자열로 받는다."""

    reply_to: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    """직전 run 에서 Memory가 물은 것에 대한 답이면 그 run_id. 없으면 일반 새 입력이다.

      값이 있는데 맥락을 못 찾으면 400 이다."""


class CreateInputResponse(BaseModel):
    """202 — run_id 만. 결과는 전부 GET /runs/{rid}/events 로 흐른다."""

    run_id: str
