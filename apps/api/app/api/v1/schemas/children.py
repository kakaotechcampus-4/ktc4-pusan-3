"""아이 엔드포인트의 요청·응답 — 계약서 §04 · §06.

🚨 프론트 apps/web/src/lib/api/types.ts 의 CreateChildRequest · CreateChildResponse ·
   CreateInputRequest · CreateInputResponse 와 같은 모양이어야 한다.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StringConstraints

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

    # 🚨 타입에 `| None` 을 붙이지 않는다. 기본값 None 은 "안 보냄" 표시일 뿐이고(검사하지 않는다),
    #    타입에 None 을 넣으면 Swagger 가 null 을 받는다고 안내한다.
    #    (docstring 이 아니라 주석인 이유 — docstring 은 Swagger 설명으로 그대로 나간다)
    nickname: Nickname = Field(
        default=None, description="아이를 부르는 이름. 앞뒤 공백을 떼고 1~20자"
    )
    birth_date: date = Field(
        default=None,
        description=(
            "생일 (YYYY-MM-DD). 한국 날짜로 오늘보다 뒤거나 올해 - 20년의 1월 1일보다 앞이면 400"
        ),
    )
    gender: Gender = Field(
        default=None, description="male · female · undisclosed('밝히지 않을래요')"
    )


class UpdateChildResponse(BaseModel):
    """200 — 고친 뒤의 프로필."""

    child: ChildProfile


MEASUREMENT_MAX = 300
"""키(cm) · 몸무게(kg)의 상한. 평가가 아니라 오타 막기다.

DB 칸(999.9)에 닿기 전에 400 으로 돌려보낸다. 화면은 0 보다 큰 숫자를 전부 보낸다
(apps/web/src/lib/measurement.ts).
"""

_ONE_PLACE = Decimal("0.1")


def _round_one_place(value: Decimal) -> Decimal:
    """소수 한 자리로 반올림한다 — DB 칸이 numeric(4,1) 이다 (docs/agents/data_model.md 09-22).

    DB 가 말없이 반올림하게 두면 응답(보낸 값)과 저장된 값이 달라진다.
    서버가 먼저 반올림하고 그 값을 저장 · 응답한다.
    반올림해서 0 이 되는 값(0.04)은 잰 것이 아니라 400 이다 — 그대로 두면 DB 의 "0 보다 커야" 에
    걸려 500 이 된다.
    """
    rounded = value.quantize(_ONE_PLACE, rounding=ROUND_HALF_UP)
    if rounded < _ONE_PLACE:
        raise ValueError("0.1 보다 작다")
    return rounded


Measurement = Annotated[Decimal, Field(gt=0, le=MEASUREMENT_MAX), AfterValidator(_round_one_place)]


class OnboardingRequest(BaseModel):
    """POST /children/{cid}/onboarding — 아이 등록 뒤 02 화면. 적은 칸만 보낸다. 하나도 없으면 400.

    null 은 받지 않는다(400) — 적지 않은 칸은 보내지 않는다.
    키 · 몸무게는 소수 한 자리로 반올림한다.

    알레르기(`safety` · `safety_status`)와 한 줄(`one_line`)은 받지 않는다(400). 알레르기는
    POST /children/{cid}/health-safety 로, 한 줄은 홈 입력으로 보낸다.
    """

    # 🚨 알레르기의 쓰기 경로는 승인 게이트 ㉡(health-safety) 하나다 (루트 §2) — 여기서 받지 않는다.
    #    계약서 초안 §05 의 `interests` · `dev_answers` 도 화면에서 빠져서 받지 않는다.
    model_config = ConfigDict(extra="forbid")

    relation: ParentChildRelation = Field(
        default=None, description="보내는 보호자와 아이의 관계. 다른 보호자의 관계는 바꾸지 않는다"
    )
    gender: Gender = Field(
        default=None, description="male · female · undisclosed. 화면은 기본값이면 보내지 않는다"
    )
    height_cm: Measurement = Field(
        default=None, description="키(cm). 0 보다 크고 300 이하. 잰 날은 서버가 오늘로 찍는다"
    )
    weight_kg: Measurement = Field(
        default=None, description="몸무게(kg). 0 보다 크고 300 이하. 잰 날은 서버가 오늘로 찍는다"
    )


class GrowthLogOut(BaseModel):
    """키 · 몸무게를 잰 날 한 줄."""

    # 🚨 증감 · 백분위 · 또래 비교 칸은 만들지 않는다 — 발달 평가로 읽힌다 (루트 §2)
    id: UUID
    measured_on: date
    height_cm: float | None
    weight_kg: float | None
    measured_label: str = Field(
        description=(
            "잰 날이 오늘로부터 얼마 전인지 (오늘 · 어제 · N일 전 · N주 전). 기록 탭과 같은 규칙"
        )
    )
    note: str | None


class GrowthLogsResponse(BaseModel):
    """GET /children/{cid}/growth — 잰 날 최신순. 한 화면에 다 들어오는 분량이라 커서는 늘 null."""

    items: list[GrowthLogOut]
    next_cursor: None = None


class CreateGrowthLogRequest(BaseModel):
    """POST /children/{cid}/growth — 잰 날 필수, 키 · 몸무게는 하나 이상.

    잰 날은 아이 생일부터 오늘(한국 날짜)까지. 키 · 몸무게는 소수 한 자리로 반올림해 저장한다.
    승인 게이트가 아니라 Idempotency-Key 를 받지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    measured_on: date = Field(description="잰 날 (YYYY-MM-DD). 생일보다 앞이거나 오늘보다 뒤면 400")
    height_cm: Measurement | None = Field(default=None, description="키(cm). 0 보다 크고 300 이하")
    weight_kg: Measurement | None = Field(
        default=None, description="몸무게(kg). 0 보다 크고 300 이하"
    )


class UpdateGrowthLogRequest(BaseModel):
    """PATCH /children/{cid}/growth/{id} — 보낸 칸만 바꾼다.

    하나도 없으면(`{}`) 바꾸지 않고 지금 줄을 200 으로 돌려준다.
    키 · 몸무게의 null 은 "그날 그건 안 잰 것으로 되돌리기" 다(안 보내면 그대로). 다만 고친 결과가
    둘 다 비면 400. 잰 날은 비울 수 없다. 키 · 몸무게는 소수 한 자리로 반올림해 저장한다.
    """

    model_config = ConfigDict(extra="forbid")

    measured_on: date = Field(default=None, description="잰 날. null 은 받지 않는다")
    height_cm: Measurement | None = Field(default=None, description="키(cm). null 이면 비운다")
    weight_kg: Measurement | None = Field(default=None, description="몸무게(kg). null 이면 비운다")


class GrowthLogResponse(BaseModel):
    """201 · 200 — 더하거나 고친 기록 한 줄."""

    log: GrowthLogOut


class OnboardingResponse(BaseModel):
    """200 — 저장한 뒤의 프로필과, 키·몸무게를 적었으면 그 기록 한 줄."""

    child: ChildProfile
    growth: GrowthLogOut | None = Field(description="키 · 몸무게를 둘 다 안 적었으면 null")


class InputLocation(BaseModel):
    """휴대폰이 흐려 보낸 좌표 (#380). 소수점 둘째 자리(약 1km)로 보낸다 — 서버도 한 번 더 자른다.

    🚨 저장하지 않고 로그에도 남기지 않는다. 이 요청의 run 안에서 날씨 격자 변환과 장소 거리
    계산에만 쓴다. `repr` 에 값을 싣지 않는다 — 요청 객체를 찍어도 좌표가 새지 않게.
    """

    lat: float = Field(ge=-90, le=90, repr=False, description="위도. 소수점 둘째 자리로 흐린 값")
    lon: float = Field(ge=-180, le=180, repr=False, description="경도. 소수점 둘째 자리로 흐린 값")


class CreateInputRequest(BaseModel):
    """POST /children/{cid}/inputs — 한 줄 입력."""

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    """보호자가 적은 한 줄. 공백뿐이면 400 — 빈 줄을 Agent 에 보내면 모델 호출 한 번이 낭비다."""

    source: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    """어디서 온 입력인가 — home_input · photo 등. 계약서가 값을 고정하지 않아 문자열로 받는다."""

    reply_to: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    """직전 run 에서 Memory가 물은 것에 대한 답이면 그 run_id. 없으면 일반 새 입력이다.

      값이 있는데 맥락을 못 찾으면 400 이다."""

    location: InputLocation | None = Field(
        default=None,
        description=(
            "선택. 위치정보 동의와 휴대폰 권한이 있을 때만 보낸다. 놀이 추천(Activity)의 날씨 · "
            "근처 장소 조회에만 쓰고 저장하지 않는다. 동의가 없으면 서버가 버린다"
        ),
    )


class CreateInputResponse(BaseModel):
    """202 — run_id. 결과는 전부 GET /runs/{rid}/events 로 흐른다.

    같은 키 재생에서 그 run 의 채널이 이미 지워졌고(닫힌 지 5분 뒤 · 서버 재시작) 대화 기록이
    남아 있으면 `finished` 와 `conversation_date` 가 같이 온다 (#538).
    """

    run_id: str
    finished: bool = Field(
        default=False,
        description=(
            "true 면 이미 끝나 결과가 대화 기록에 저장된 run 이다. GET /runs/{rid}/events 를 열지 "
            "말고 conversation_date 의 지난 대화(GET /children/{cid}/chat)로 결과를 그린다. "
            "false 면 지금처럼 SSE 를 연다 — 같은 키 재생에서 채널도 대화 기록도 없으면 저장됐는지 "
            "서버도 모르는 run 이라 false 다"
        ),
    )
    conversation_date: date | None = Field(
        default=None,
        description=(
            "finished 가 true 일 때만. 그 턴이 붙은 대화 날짜(KST). 되묻기에 답한 run 은 질문한 "
            "날이라 접수한 날과 다를 수 있다"
        ),
    )
