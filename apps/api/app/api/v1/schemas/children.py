"""아이 엔드포인트의 요청·응답 — 계약서 §04 · §06.

🚨 프론트 apps/web/src/lib/api/types.ts 의 CreateChildRequest · CreateChildResponse ·
   CreateInputRequest · CreateInputResponse 와 같은 모양이어야 한다.
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, StringConstraints, field_validator

from app.api.v1.schemas.auth import ConsentInput, Nickname
from app.domains.child.models import Gender, ParentChildRelation

Role = Literal["owner", "member"]
"""아이의 owner(등록한 보호자)인가, 초대로 연결된 member 인가 — child.owner_parent_id 와 비교."""


class CreateChildRequest(BaseModel):
    """POST /children — 아이 정보와 아이 동의를 한 요청에 (#92 결정, 2026-09-18)."""

    nickname: Nickname = Field(description="아이를 부르는 이름. 앞뒤 공백을 떼고 1~20자")
    birth_date: date = Field(
        description=(
            "생일 (YYYY-MM-DD). 한국 날짜로 오늘보다 뒤거나 올해 - 20년의 1월 1일보다 앞이면 400"
        )
    )
    consents: list[ConsentInput] = Field(
        description="아이 동의 — child_basic · child_health 둘 다 필수. 버전은 GET /policies 의 것"
    )
    guardian_attested: StrictBool = Field(
        description=(
            "'나는 이 아이의 법정대리인이며 만 19세 이상입니다' 체크. JSON true · false 만 받는다"
            " (법적 증빙 — 'yes' · 1 은 400). false 면 403"
        )
    )


class CreateChildResponse(BaseModel):
    """201 — 등록한 아이. 등록한 보호자는 항상 owner 다."""

    id: UUID
    nickname: str
    age_display: str = Field(description="화면에 그대로 쓰는 나이 문구 (생후 N일 · N개월 · 만 N세)")
    role: Role


class ChildProfile(BaseModel):
    """아이 프로필 — GET /children/{cid} 의 응답이자 PATCH 의 응답 안 child.

    관계(relation) · 역할(role)은 보는 보호자 기준이다. 같은 아이라도 보호자마다 다르게 받는다.
    """

    id: UUID
    nickname: str
    birth_date: date
    age_display: str = Field(description="화면에 그대로 쓰는 나이 문구 (생후 N일 · N개월 · 만 N세)")
    gender: Gender = Field(
        description=(
            "male · female · undisclosed. 기본값은 undisclosed('밝히지 않을래요'). "
            "화면 표시 전용 — Agent 에 넘기지 않는다"
        )
    )
    relation: ParentChildRelation = Field(description="보는 보호자와 아이의 관계")
    role: Role


class UpdateChildRequest(BaseModel):
    """PATCH /children/{cid} — 고친 칸만 보낸다. 안 보낸 칸은 그대로 둔다. 하나도 없으면 400.

    null 은 받지 않는다(400). 별명 · 생일 · 성별은 늘 값이 있어서 "비우기" 가 없다.
    """

    nickname: Nickname | None = Field(
        default=None, description="아이를 부르는 이름. 앞뒤 공백을 떼고 1~20자"
    )
    birth_date: date | None = Field(
        default=None,
        description=(
            "생일 (YYYY-MM-DD). 한국 날짜로 오늘보다 뒤거나 올해 - 20년의 1월 1일보다 앞이면 400"
        ),
    )
    gender: Gender | None = Field(
        default=None, description="male · female · undisclosed('밝히지 않을래요')"
    )

    @field_validator("nickname", "birth_date", "gender")
    @classmethod
    def _not_null(cls, value):
        # 안 보낸 칸(기본값 None)에는 불리지 않는다 — null 을 직접 보낸 칸만 여기서 막힌다
        if value is None:
            raise ValueError("null 은 받지 않는다 — 고치지 않을 칸은 보내지 않는다")
        return value


class UpdateChildResponse(BaseModel):
    """200 — 고친 뒤의 프로필."""

    child: ChildProfile


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
