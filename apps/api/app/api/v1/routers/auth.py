"""카카오 OAuth 로그인 — 명세: docs/api/auth-kakao-v1.md §3

왜 둘로 나누나 — 경로가 고정된 /auth/logout 과 경로에 값이 들어가는
/auth/{provider} 를 한 라우터에 두면 등록 순서에 로그인이 걸린다
(app/api/v1/router.py 의 주석). 나눠 두면 그 순서가 붙어 있는 두 줄로 드러나고,
주석보다 깨지기 어렵다.

구현: #34 (이도헌) · 리뷰: 김명성
"""

import base64
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Cookie, Response
from fastapi.responses import RedirectResponse

from app.api.deps.auth import CurrentParent, hash_token
from app.api.deps.db import SessionDep
from app.api.errors import ApiError
from app.api.v1.schemas.auth import AuthStatusResponse
from app.api.v1.schemas.common import ErrorEnvelope
from app.core.config import settings
from app.core.constants import API_V1_PREFIX, AUTH_PREFIX, OAUTH_COOKIE_PATH
from app.domains.identity.models import AuthProvider
from app.domains.identity.repository import (
    create_handoff,
    delete_session,
    find_parent_id_by_identity,
)
from app.integrations.kakao.client import KakaoApiError, build_authorize_url, kakao_client

log = logging.getLogger(__name__)

fixed_router = APIRouter(prefix=AUTH_PREFIX, tags=["auth"])
"""경로가 고정된 엔드포인트. provider 값을 받지 않는다."""

provider_router = APIRouter(prefix=f"{AUTH_PREFIX}/{{provider}}", tags=["auth"])
"""경로에 provider 가 들어가는 엔드포인트.

🚨 provider 는 AuthProvider enum(app/domains/identity/models.py)으로 받는다.
   enum 밖 값이면 422 validation_failed 가 된다 (명세 §8-1).
   f-string 안의 {{provider}} 는 중괄호 이스케이프다 — 실제 경로는 /auth/{provider}.
"""

STATE_COOKIE = "oauth_state"

_CLIENTS = frozenset({"web", "app"})
"""복귀 대상은 URL 이 아니라 열거값이다. URL 을 받으면 그 순간 오픈 리다이렉트다 (§2-3)."""

_BIND_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
"""base64url 43자 = 256비트 (§3-2).

🚨 길이와 문자셋을 본다. "보냈다" 만 확인하면 bind=1 로도 통과해 §7-2 가 무의미해진다.
"""


# ──────────────────────────────────────────────────────────────────────────────
# 3-1. 상태 조회
# ──────────────────────────────────────────────────────────────────────────────


@provider_router.get("/status", response_model_exclude_none=True)
async def auth_status(provider: AuthProvider) -> AuthStatusResponse:
    """로그인을 시작할 수 있는지 — 명세 §3-1 · 테스트 A-20.

    무인증이다. 프론트가 00 화면 진입 시 prefetch 해서, 설정이 없으면 버튼을
    비활성화한다 — 죽은 버튼을 만들지 않는 것이 이 응답의 전부다.
    """
    ready = _is_ready(provider)
    return AuthStatusResponse(
        ready=ready,
        start_url=_start_url(provider),
        # 🚨 프로덕션은 ready: false 만 내린다. 무인증 엔드포인트가 "어떤 설정이
        #    비었는지" 를 알려주면 정찰에 쓰인다 (§3-1 · A-20).
        missing_keys=None if ready or settings.APP_ENV == "prod" else _missing_keys(provider),
    )


# ──────────────────────────────────────────────────────────────────────────────
# 3-2. 로그인 시작 — 302
# ──────────────────────────────────────────────────────────────────────────────


@provider_router.get("", status_code=302, response_class=RedirectResponse)
async def start(
    provider: AuthProvider,
    client: str | None = None,
    bind: str | None = None,
) -> RedirectResponse:
    """카카오 동의 화면으로 보낸다 — 명세 §3-2 · 테스트 A-06 · A-07 · A-10 · A-11.

    🚨 쿼리를 전부 Optional 로 받는다. `bind: str` 처럼 필수로 선언하면 FastAPI 의
       검증이 핸들러 본문보다 먼저 돌아 RequestValidationError 를 던지고, 전역
       핸들러가 400 JSON 봉투로 답해 버린다. 이 엔드포인트는 실패도 302 여야 한다
       (§8-2). 검증은 전부 이 안에서 한다.
    """
    if client is not None and client not in _CLIENTS:
        # 잘못된 값으로는 앱 복귀 여부를 신뢰할 수 없으므로 웹을 안전한 기본
        # 복귀 대상으로 쓴다 (§2-3 · A-10). 카카오를 부르지 않는다.
        return _error_redirect("web", "invalid_client")

    target = client or "web"

    if bind is None or not _BIND_PATTERN.fullmatch(bind):
        # 형식이 안 맞으면 카카오 동의까지 걷게 하지 말고 여기서 끊는다 (A-06 · A-07).
        return _error_redirect(target, "invalid_bind")

    if not _is_ready(provider):
        # 설정이 비었다. 프론트가 ready 를 먼저 보므로 정상 경로에서는 오지 않는다.
        # 302 엔드포인트라 봉투를 쓸 수 없어 §8-2 의 코드 중 가장 가까운 것으로 나간다.
        log.warning("설정이 없는 provider 로 로그인 시작 시도: %s", provider.value)
        return _error_redirect(target, "oauth_provider_error")

    state = secrets.token_urlsafe(32)
    response = RedirectResponse(
        build_authorize_url(
            rest_api_key=str(settings.KAKAO_REST_API_KEY),
            callback_url=str(settings.KAKAO_CALLBACK_URL),
            state=state,
        ),
        status_code=302,
    )
    response.headers["Cache-Control"] = "no-store"

    # 서버는 state 를 어디에도 저장하지 않는다. 쿠키가 대신 들고 있어서, 카카오 동의
    # 화면에서 이탈한 시도는 흔적을 남기지 않는다 (§5-5).
    response.set_cookie(
        STATE_COOKIE,
        _encode_state(state, target, hash_token(bind)),
        max_age=settings.OAUTH_STATE_TTL,
        # Path 를 좁혀 인증 외 요청에 실리지 않게 한다. 값은 상수 한 곳에서 온다 —
        # 마운트 지점이 바뀌면 쿠키 Path 도 함께 따라와야 한다 (§5-5).
        path=OAUTH_COOKIE_PATH,
        httponly=True,
        secure=True,
        # Lax 는 크로스 사이트 최상위 GET 이동에 실린다. 카카오 → 콜백이 그 경우다.
        samesite="lax",
    )
    return response


# ──────────────────────────────────────────────────────────────────────────────
# 3-3. 콜백 — 302. 카카오가 부른다
# ──────────────────────────────────────────────────────────────────────────────


@provider_router.get("/callback", status_code=302, response_class=RedirectResponse)
async def callback(
    provider: AuthProvider,
    session: SessionDep,
    oauth_state: Annotated[str | None, Cookie()] = None,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """카카오에서 돌아온 뒤 1회용 코드를 발급한다 — 명세 §3-3.

    테스트 A-08 · A-09 · A-16 · A-17. 여기서도 쿼리는 전부 Optional 이다 (start 참조).
    """
    unpacked = _decode_state(oauth_state)
    if unpacked is None:
        # 쿠키가 없거나 모양이 다르다 (A-09). client 를 모르니 웹으로 돌려보낸다.
        return _error_redirect("web", "invalid_state")
    cookie_state, target, bind_hash = unpacked

    if state is None or not hmac.compare_digest(cookie_state, state):
        # 🚨 timing-safe 비교. 불일치·부재면 인가 코드 교환을 시도하지 않는다 (A-08).
        #    여기가 뚫리면 로그인 CSRF 가 열린다.
        return _expire_state(_error_redirect(target, "invalid_state"))

    if error is not None:
        # 카카오가 실패를 돌려줬다. 사용자가 동의 화면에서 취소한 것만 따로 구분한다 —
        # 프론트가 "취소했어요" 와 "카카오가 이상해요" 를 다르게 말해야 한다 (A-17).
        denied = error == "access_denied"
        return _expire_state(
            _error_redirect(target, "oauth_denied" if denied else "oauth_provider_error")
        )

    if code is None or not _is_ready(provider):
        return _expire_state(_error_redirect(target, "oauth_provider_error"))

    try:
        async with kakao_client() as kakao:
            access_token = await kakao.exchange_code(code)
            provider_user_id = await kakao.fetch_user_id(access_token)
            # 회원번호만 얻으면 볼일이 끝난다. 저장하지 않으므로 바로 버린다 (§7-4).
            await kakao.revoke_token(access_token)
    except KakaoApiError:
        # 🚨 기본값으로 대체하지 않는다. 카카오가 죽었을 때 로그인을 통과시키는 경로는
        #    존재하지 않는다 (§8-1 · A-16).
        return _expire_state(_error_redirect(target, "oauth_provider_error"))

    parent_id = await find_parent_id_by_identity(
        session, provider=provider, provider_user_id=provider_user_id
    )

    handoff_code = secrets.token_urlsafe(32)
    await create_handoff(
        session,
        provider=provider,
        code_hash=hash_token(handoff_code),
        # 시작 때 쿠키에 심어 둔 해시를 그대로 옮긴다. 교환에서 클라이언트가 같은 원문을
        # 다시 제시해야 세션이 나간다 (§7-2).
        bind_hash=bind_hash,
        # 정확히 하나만 찬다 — 기존 회원이면 parent_id, 처음 보는 회원번호면 회원번호.
        parent_id=parent_id,
        provider_user_id=None if parent_id else provider_user_id,
        # 교환용 2분. 가입 대기표 10분은 교환 시점에 따로 만든다 (§5-4).
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.HANDOFF_TTL),
    )
    await session.commit()

    # 🚨 복귀 URL 에 에러 문구를 싣지 않듯 성공에도 코드 하나만 싣는다 (§8-2).
    return _expire_state(_redirect(_return_url(target), code=handoff_code))


# ──────────────────────────────────────────────────────────────────────────────
# 3-6. 로그아웃
# ──────────────────────────────────────────────────────────────────────────────


@fixed_router.post(
    "/logout",
    status_code=204,
    # 🚨 204 는 본문이 없어야 한다. response_class 를 두지 않으면 FastAPI 가
    #    JSON 으로 null 을 실으려 해서 Content-Length 가 어긋난다.
    response_class=Response,
    responses={401: {"model": ErrorEnvelope}},
)
async def logout(auth: CurrentParent, session: SessionDep) -> Response:
    """세션 행 1건 삭제 — 명세 §3-6 · 테스트 A-15.

    계약서 28개 목록에 없던 엔드포인트다. 없으면 클라이언트가 토큰을 버리는 흉내만
    내고 서버에서는 만료까지 유효하다.

    🚨 지운 즉시 무효다. 다음 요청은 get_current_parent 에서 401 로 떨어진다 — 불투명
       토큰을 JWT 대신 고른 이유가 이것이다 (§4-2).

    카카오 쪽 로그아웃은 부르지 않는다. 우리 서비스에서 나가는 것과 카카오 계정에서
    로그아웃하는 것은 다른 행위다 (§3-6).
    """
    await delete_session(session, session_id=auth.session_id)
    await session.commit()
    return Response(status_code=204)


# 아래 2개는 이어서 채운다. 둘 다 무인증이고 계약서 §01 에 예외로 명시해야 한다.
#   POST ""          → 세션 | consent_required  §3-4  (DELETE … RETURNING 소비)
#   POST "/signup"   → 세션                     §3-5  (한 트랜잭션)


# ──────────────────────────────────────────────────────────────────────────────
# 안쪽 도구
# ──────────────────────────────────────────────────────────────────────────────


def _is_ready(provider: AuthProvider) -> bool:
    """이 provider 로 로그인을 시작할 수 있는가.

    카카오 외에는 설정 자체가 없다. enum 에는 있으므로 422 가 아니라 ready: false 다.
    """
    return provider is AuthProvider.KAKAO and settings.kakao_ready


def _missing_keys(provider: AuthProvider) -> list[str]:
    if provider is AuthProvider.KAKAO:
        return settings.kakao_missing_keys
    return [f"{provider.value.upper()} 미구현"]


def _start_url(provider: AuthProvider) -> str:
    """시작 엔드포인트의 절대 URL (§3-1).

    등록된 콜백 URL 의 오리진에서 파생한다 — 시작과 콜백이 다른 오리진이면 state 쿠키가
    콜백에 실리지 않기 때문이다. 설정이 없으면 파생할 것이 없지만, 프론트 타입이
    nullable 이 아니고 ready: false 면 이 값을 보지 않으므로 빈 문자열을 내린다.
    """
    if not settings.KAKAO_CALLBACK_URL:
        return ""
    parsed = urlparse(settings.KAKAO_CALLBACK_URL)
    return f"{parsed.scheme}://{parsed.netloc}{API_V1_PREFIX}{AUTH_PREFIX}/{provider.value}"


def _return_url(target: str) -> str:
    """복귀 URL. 서버 환경변수에서만 온다 — 클라이언트는 URL 을 지정하지 못한다 (§2-3)."""
    raw = settings.AUTH_RETURN_URL_APP if target == "app" else settings.AUTH_RETURN_URL_WEB
    if not raw:
        # 돌려보낼 곳이 없으면 302 를 만들 수 없다. 흐름의 실패가 아니라 서버 설정
        # 문제라 봉투로 답한다 (§8-2 의 예외가 아니다).
        raise ApiError(500, "internal_error", "로그인 설정이 끝나지 않았어요")
    return raw


def _redirect(base: str, **params: str) -> RedirectResponse:
    separator = "&" if "?" in base else "?"
    response = RedirectResponse(f"{base}{separator}{urlencode(params)}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


def _error_redirect(target: str, code: str) -> RedirectResponse:
    """복귀 URL + ?error=<코드>.

    🚨 문구를 싣지 않는다. 서버 메시지를 URL 에 그대로 실으면 공격자가 프론트 화면에
       임의 문구를 띄우는 통로가 된다(피싱 문구 주입). 사용자에게 보일 문장은
       프론트가 만든다 (§8-2).
    """
    return _redirect(_return_url(target), error=code)


def _encode_state(state: str, target: str, bind_hash: bytes) -> str:
    """oauth_state 쿠키 값 — state · client · bind 해시 세 칸 (§5-5).

    점으로 잇는다. state 는 base64url 이라 점이 들어가지 않고, 세 칸이 고정이라
    파싱에 모호함이 없다. 🚨 bind 원문은 담지 않는다 — 쿠키가 노출돼도 원문은 못 얻는다.
    """
    return f"{state}.{target}.{_b64(bind_hash)}"


def _decode_state(raw: str | None) -> tuple[str, str, bytes] | None:
    """쿠키를 세 칸으로 되돌린다. 모양이 다르면 None — 부재와 같이 취급한다 (A-09)."""
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    state, target, encoded = parts
    if target not in _CLIENTS:
        return None
    try:
        bind_hash = _unb64(encoded)
    except ValueError:
        return None
    return state, target, bind_hash


def _expire_state(response: RedirectResponse) -> RedirectResponse:
    """쓴 쿠키는 콜백에서 만료시킨다 (§3-3 5번). Path 가 심을 때와 같아야 지워진다."""
    response.delete_cookie(STATE_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(encoded: str) -> bytes:
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
