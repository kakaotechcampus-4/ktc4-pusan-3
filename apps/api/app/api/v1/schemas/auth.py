"""인증 엔드포인트의 응답 — 명세 docs/api/auth-kakao-v1.md §3

🚨 프론트 apps/web/src/lib/api/types.ts 의 AuthStatus 와 같은 모양이어야 한다.
   그쪽 start_url 은 nullable 이 아니고 lib/auth/oauth.ts 가 new URL(start_url) 을
   부른다. 설정이 비어도 문자열을 내리는 이유가 이것이다 (아래 참조).
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, StringConstraints

from app.domains.consent.models import ConsentScope

BIND_PATTERN = r"^[A-Za-z0-9_-]{43}$"
"""base64url 43자 = 256비트 (§3-2).

🚨 길이와 문자셋을 함께 본다. "보냈다" 만 확인하면 bind=1 로도 통과해 §7-2 가
   무의미해진다. 시작(쿼리)과 교환·가입(바디) 세 곳이 같은 값을 쓴다.
"""

Bind = Annotated[str, StringConstraints(pattern=BIND_PATTERN)]

NICKNAME_MAX = 20
"""가입 화면(apps/web/src/app/auth/consent/page.tsx MAX_NICKNAME)과 같은 상한.

한쪽만 바꾸지 않는다."""

Nickname = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=NICKNAME_MAX,
        pattern=r"^[^\x00-\x1F\x7F]+$",
    ),
]
"""앞뒤 공백을 떼고 1자 이상. 공백만 보낸 이름은 이름이 아니다.

🚨 제어문자(NUL · 줄바꿈 등)를 막는다. NUL 은 Postgres text 에 들어가지 못해 저장에서
   500 이 나고, 줄바꿈은 한 줄짜리 이름을 여러 줄로 그린다.
"""


class AuthStatusResponse(BaseModel):
    """GET /auth/{provider}/status — 명세 §3-1.

    죽은 버튼을 만들지 않으려고 있는 응답이다. ready: false 면 프론트가 버튼을
    비활성화하고 "아직 연결 전" 이라고 말한다.
    """

    ready: bool
    """이 provider 로 로그인을 시작할 수 있는 서버 설정이 갖춰졌는가."""

    start_url: str
    """시작 엔드포인트의 절대 URL.

    서버가 내려주는 이유 — 프론트가 API_BASE_URL 로 조립하면 그 값과
    KAKAO_CALLBACK_URL 의 오리진이 어긋난 배포에서 state 쿠키가 조용히 깨진다.
    시작과 콜백이 다른 오리진이면 쿠키가 콜백에 실리지 않는다.
    """

    missing_keys: list[str] | None = None
    """🚨 개발 환경에서만 채운다 (§3-1).

    프로덕션에서 무인증 엔드포인트가 "어떤 설정이 비었는지" 를 알려주면 정찰에 쓰인다.
    None 일 때 키 자체가 빠지도록 라우터가 response_model_exclude_none 을 쓴다.
    """


class ExchangeRequest(BaseModel):
    """POST /auth/{provider} 요청 — 명세 §3-4. 웹·앱이 같은 바디를 보낸다."""

    code: str
    """1회용 코드. 카카오 인가 코드가 아니라 우리 서버가 발급한 값이다."""

    bind: Bind
    """시작 때 만든 비밀의 원문. 형식이 틀리면 400 validation_failed (§8-1)."""


class ConsentInput(BaseModel):
    """동의 1건 — 명세 §3-5."""

    scope: ConsentScope
    policy_version: str


class SignupRequest(BaseModel):
    """POST /auth/{provider}/signup 요청 — 명세 §3-5."""

    consent_code: str
    """가입 대기표. 교환이 신규라고 판정했을 때만 나온다."""

    bind: Bind
    """여기서도 요구한다 — consent_code 만으로 계정이 만들어지는 것을 막는다 (§3-5)."""

    nickname: Nickname
    """가입 화면의 "부르는 이름" (필수, 실명이 아니어도 된다). parent.nickname 에 저장한다.

    동의문 1 이 "부르는 이름을 받는다" 고 알린다 — 칸이 없던 동안은 화면이 보낸 값을
    서버가 조용히 버려 그 문장이 사실이 아니었다 (#172).
    """

    consents: list[ConsentInput]


class ParentSummary(BaseModel):
    """세션 응답에 함께 싣는 보호자 최소 정보 — 명세 §3-4."""

    id: UUID
    nickname: str | None = None
    """가입 화면에서 받는다. 카카오에서 가져오지 않는다. 이름 칸이 생기기 전(#172)에 가입한
    계정은 null 일 수 있다 (§5-2)."""


class SessionResponse(BaseModel):
    """세션을 내주는 응답 — 명세 §3-4 · §3-5. 교환과 가입이 같은 모양을 쓴다."""

    token: str
    """불투명 난수. JWT 가 아니다 — 로그아웃·탈퇴·파기 3곳이 즉시 무효화를 요구한다 (§4-2)."""

    expires_in: int
    """초. 계약서에 없어서 추가한 값이다 — 없으면 프론트가 만료를 미리 알 수 없어
    매번 401 을 맞고 나서야 재로그인한다 (§3-4)."""

    is_new: bool
    parent: ParentSummary
    consent_required: list[ConsentScope]
    """아직 granted 가 아닌 계정 **필수** 동의 스코프. 선택 동의(location)는 여기 나오지 않는다.

    🚨 지금은 "한 번이라도 동의했는가" 만 본다 — 약관 버전이 바뀌어도 기존 회원에게 다시 묻지
    않는다. 약관 제3조 ⑥ 의 재동의는 배포 전에 만들 일이다 (docs/safety/consent-texts-v1.md).
    """


class ConsentRequiredResponse(BaseModel):
    """처음 보는 회원번호 — 아직 parent 가 없다 (§6-1).

    🚨 프론트는 status 필드 유무로 분기한다. token 유무로 판단하면 신규 응답에서
       signIn(undefined) 가 불려 토큰 없는 세션이 저장된다 (docs/web/kakao-login-v1.md).
    """

    status: Literal["consent_required"] = "consent_required"
    consent_code: str
