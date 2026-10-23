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
from uuid import UUID

from fastapi import APIRouter, Cookie, Response
from fastapi.responses import RedirectResponse

from app.api.deps.auth import CurrentParent, hash_token
from app.api.deps.db import SessionDep
from app.api.errors import ApiError, envelope
from app.api.runs import pending_reply, suggestion_retry
from app.api.v1.schemas.auth import (
    BIND_PATTERN,
    AuthStatusResponse,
    ConsentRequiredResponse,
    ExchangeRequest,
    ParentSummary,
    RefreshResponse,
    SessionResponse,
    SignupRequest,
    WithdrawRequest,
    WithdrawResponse,
)
from app.api.v1.schemas.common import ErrorEnvelope
from app.core.config import settings
from app.core.constants import API_V1_PREFIX, AUTH_COOKIE_PATH, AUTH_PREFIX
from app.domains.consent.models import ConsentScope
from app.domains.consent.repository import (
    ACCOUNT_SCOPES,
    REQUIRED_ACCOUNT_SCOPES,
    grant_account_scope,
    missing_account_scopes,
)
from app.domains.identity.models import AuthProvider
from app.domains.identity.repository import (
    consume_handoff,
    create_access,
    create_handoff,
    create_identity,
    create_parent,
    create_session,
    delete_session,
    find_parent,
    find_parent_id_by_identity,
    lock_session_by_refresh,
    rotate_refresh,
)
from app.domains.identity.withdraw import withdraw_parent
from app.domains.policy.repository import find_active_versions
from app.integrations.kakao.client import (
    KakaoApiError,
    build_authorize_url,
    kakao_admin,
    kakao_client,
    loggable_code,
)

log = logging.getLogger(__name__)

fixed_router = APIRouter(prefix=AUTH_PREFIX, tags=["auth"])
"""경로가 고정된 엔드포인트. provider 값을 받지 않는다. 인증 라우터에 붙는다."""

refresh_router = APIRouter(prefix=AUTH_PREFIX, tags=["auth"])
"""POST /auth/refresh 하나 (#466). 경로는 고정이지만 Bearer 없이 쿠키로 인증해서 fixed_router 와
나눴다 — fixed_router 는 인증 라우터에 붙어 access 가 만료된 요청을 401 로 먼저 막는다.

🚨 공개 라우터에서 provider_router 보다 먼저 붙인다. 늦으면 /auth/refresh 가
   provider="refresh" 로 잡혀 422 가 된다 (app/api/v1/router.py).
"""

provider_router = APIRouter(prefix=f"{AUTH_PREFIX}/{{provider}}", tags=["auth"])
"""경로에 provider 가 들어가는 엔드포인트.

🚨 provider 는 AuthProvider enum(app/domains/identity/models.py)으로 받는다.
   enum 밖 값이면 422 validation_failed 가 된다 (명세 §8-1).
   f-string 안의 {{provider}} 는 중괄호 이스케이프다 — 실제 경로는 /auth/{provider}.
"""

STATE_COOKIE = "oauth_state"

REFRESH_COOKIE = "icatch_rt"
"""refresh 토큰을 싣는 쿠키 (#466). 웹 JS 가 읽지 못하는 HttpOnly 라 앱을 꺼도 남는다."""

_CLIENTS = frozenset({"web", "app"})
"""복귀 대상은 URL 이 아니라 열거값이다. URL 을 받으면 그 순간 오픈 리다이렉트다 (§2-3)."""

_BIND_PATTERN = re.compile(BIND_PATTERN)
"""시작 쿼리의 bind 검증. 교환·가입 바디는 같은 패턴을 pydantic 이 검증한다.

🚨 쿼리 쪽은 pydantic 에 맡길 수 없다 — 검증이 핸들러보다 먼저 돌아 302 대신 400 JSON 이
   나가기 때문이다 (start 의 주석). 그래서 패턴 문자열 하나를 schemas 에서 공유한다.
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
        path=AUTH_COOKIE_PATH,
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
        if not denied:
            # 취소가 아니면 원인을 남긴다 (#197). 쿼리는 누구나 만들 수 있어 분류값 모양만 싣는다.
            # 걸러진 값은 "(형식 밖)" 으로 — None 으로 찍으면 "error 가 없었다" 로 읽힌다.
            log.warning(
                "카카오가 인가 단계 실패를 돌려줬다: provider=%s error=%s",
                provider.value,
                loggable_code(error) or "(형식 밖)",
            )
        return _expire_state(
            _error_redirect(target, "oauth_denied" if denied else "oauth_provider_error")
        )

    if code is None or not _is_ready(provider):
        # error 도 code 도 없이 왔거나 그사이 설정이 비었다. 정상 경로에선 오지 않는다 (#197).
        # provider 를 같이 남긴다 — 미구현 provider 주소로 들어와도 "설정 없음" 으로 찍혀서,
        # 그것 없이는 .env 부터 뒤지게 된다.
        log.warning(
            "콜백을 이어갈 수 없다: provider=%s %s",
            provider.value,
            "인가 코드 없음" if code is None else "설정 없음",
        )
        return _expire_state(_error_redirect(target, "oauth_provider_error"))

    try:
        async with kakao_client() as kakao:
            access_token = await kakao.exchange_code(code)
            provider_user_id = await kakao.fetch_user_id(access_token)
            # 회원번호만 얻으면 볼일이 끝난다. 저장하지 않으므로 바로 버린다 (§7-4).
            await kakao.revoke_token(access_token)
    except KakaoApiError as exc:
        # 🚨 기본값으로 대체하지 않는다. 카카오가 죽었을 때 로그인을 통과시키는 경로는
        #    존재하지 않는다 (§8-1 · A-16).
        #    사유는 로그에만 남긴다. 문구는 client 가 분류값까지만 담아 만든다 (§7-5 · #197).
        log.warning("카카오 호출 실패: %s", exc)
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
    response = Response(status_code=204)
    _expire_refresh(response)
    return response


# ──────────────────────────────────────────────────────────────────────────────
# refresh (#466) — Bearer 가 아니라 쿠키로 인증한다. 루트 CLAUDE.md §9 의 무인증 목록에 있다
# ──────────────────────────────────────────────────────────────────────────────


@refresh_router.post("/refresh", responses={401: {"model": ErrorEnvelope}})
async def refresh(
    session: SessionDep,
    response: Response,
    icatch_rt: Annotated[str | None, Cookie()] = None,
) -> RefreshResponse:
    """refresh 쿠키로 새 access 를 받는다. 현재 refresh 면 회전한다 — #466.

    | 들어온 쿠키 | 결과 |
    | --- | --- |
    | 현재 refresh, 만료 전 | 새 access + 회전된 쿠키 |
    | 직전 refresh, 회전 후 유예(30초) 안 | 새 access 만. 쿠키는 이긴 요청이 이미 바꿨다 |
    | 직전 refresh, 유예 밖 | 재사용으로 보고 세션 삭제 → 401 |
    | 없음 · 모르는 값 · 유휴 또는 절대 만료 | 401 (만료면 세션도 삭제) |

    🚨 실패 사유를 구분하지 않는다 — 전부 401 unauthenticated 다 (명세 §8-1).
       실패하면 쿠키도 지운다. 남겨 두면 다음 실행마다 같은 401 을 다시 맞는다.
    🚨 로그에는 토큰도 해시도 남기지 않는다 (§7-5). 재사용 삭제만 세션 id 로 남긴다.
    """
    response.headers["Cache-Control"] = "no-store"
    if not icatch_rt:
        return _refresh_failed()

    presented = hash_token(icatch_rt)
    row = await lock_session_by_refresh(session, refresh_hash=presented)
    if row is None:
        return _refresh_failed()

    # 🚨 시각은 잠금을 얻은 뒤에 잡는다. 앞에서 잡으면 기다리는 사이 만료가 지나도 만료 전
    #    시각으로 판정해 이미 만료된 세션을 연장할 수 있다.
    now = datetime.now(UTC)
    if row.refresh_expires_at <= now or row.absolute_expires_at <= now:
        await delete_session(session, session_id=row.session_id)
        await session.commit()
        return _refresh_failed()

    if row.refresh_hash != presented:
        # 직전 refresh 로 걸렸다. 회전 직후면 동시 요청에서 진 쪽이고, 아니면 재사용이다.
        in_grace = row.rotated_at is not None and now - row.rotated_at <= timedelta(
            seconds=settings.REFRESH_REUSE_GRACE
        )
        if not in_grace:
            # 탈취와 정상 기기의 쿠키 유실(응답 직전 앱 종료 · Android 쿠키 기록 지연)이 여기서
            # 똑같이 보인다. 한 건으로는 못 가르지만 건수가 많으면 정상 사용자가 쫓겨나는 중이다.
            log.warning(
                "직전 refresh 가 유예 밖에서 와서 세션을 지운다: session=%s", row.session_id
            )
            await delete_session(session, session_id=row.session_id)
            await session.commit()
            return _refresh_failed()
        token, expires_in = await _issue_access(
            session, session_id=row.session_id, now=now, absolute_expires_at=row.absolute_expires_at
        )
        await session.commit()
        return RefreshResponse(token=token, expires_in=expires_in)

    rotated = secrets.token_urlsafe(32)
    refresh_expires_at = min(
        now + timedelta(seconds=settings.REFRESH_IDLE_TTL), row.absolute_expires_at
    )
    await rotate_refresh(
        session,
        session_id=row.session_id,
        old_hash=presented,
        new_hash=hash_token(rotated),
        rotated_at=now,
        refresh_expires_at=refresh_expires_at,
    )
    token, expires_in = await _issue_access(
        session, session_id=row.session_id, now=now, absolute_expires_at=row.absolute_expires_at
    )
    await session.commit()
    _set_refresh(response, rotated, max_age=int((refresh_expires_at - now).total_seconds()))
    return RefreshResponse(token=token, expires_in=expires_in)


def _refresh_failed() -> Response:
    """refresh 실패 응답 — 401 봉투 + 쿠키 삭제.

    ApiError 로 던지지 않는다. 예외 처리기가 새 응답을 만들어 쿠키 삭제가 실리지 않는다.
    """
    failed = envelope(401, "unauthenticated", "다시 로그인해 주세요")
    failed.headers["Cache-Control"] = "no-store"
    _expire_refresh(failed)
    return failed


# ──────────────────────────────────────────────────────────────────────────────
# 탈퇴 (#379) — 계약서 밖. 화면 목(apps/web/src/mocks/handlers/auth.ts)이 먼저 세운 모양이다
# ──────────────────────────────────────────────────────────────────────────────


@fixed_router.post(
    "/withdraw",
    responses={400: {"model": ErrorEnvelope}, 401: {"model": ErrorEnvelope}},
)
async def withdraw(
    body: WithdrawRequest, auth: CurrentParent, session: SessionDep, response: Response
) -> WithdrawResponse:
    """탈퇴 — 유예 없이 바로 지운다. 등록 보호자면 아이와 기록까지 지운다.

    순서가 계약이다.
    1. DB 삭제를 한 트랜잭션으로 커밋한다. 커밋 전 실패는 전부 되돌아간다 — 다시 누르면 된다
    2. 커밋 뒤 카카오 연결을 끊는다. 실패해도 탈퇴는 200 이다 (_unlink_kakao)

    🚨 커밋 뒤에는 되돌릴 수 없다. 응답이 사라져도 탈퇴는 끝났을 수 있고,
       그 뒤 기존 세션은 401 이다.
    `Idempotency-Key` 는 받지 않는다 — 끝난 뒤 다시 보내면 세션이 없어 401, 동시에 들어온
    나중 요청은 withdraw_parent 의 잠금 뒤에서 이미 지워진 것을 보고 401 이다.
    """
    now = datetime.now(UTC)
    withdrawn = await withdraw_parent(session, parent_id=auth.parent_id, now=now)
    if withdrawn is None:
        raise ApiError(401, "unauthenticated", "다시 로그인해 주세요")
    await session.commit()
    _expire_refresh(response)
    _forget_in_memory(auth.parent_id, withdrawn.child_ids)

    if withdrawn.kakao_user_id is not None:
        await _unlink_kakao(withdrawn.kakao_user_id)
    return WithdrawResponse(deleted_at=now)


def _forget_in_memory(parent_id: UUID, child_ids: tuple[UUID, ...]) -> None:
    """원문이 든 메모리 보관함(되묻기 · 05 다시 시도)도 DB 처럼 그 자리에서 비운다 (#561).

    🚨 커밋 뒤에 부른다. 커밋 전에 실패하면 DB 가 되돌아가니 보관함도 남아 있어야 한다.
    함께 지운 아이의 것은 함께 보던 보호자의 것까지 지운다 — 일기 · 대화 기록과 같은 기준이다.
    """
    for store in (pending_reply, suggestion_retry):
        store.forget_parent(parent_id=parent_id)
        for child_id in child_ids:
            store.forget_child(child_id)


async def _unlink_kakao(user_id: str) -> None:
    """카카오 연결 끊기 — 실패해도 예외를 올리지 않는다.

    탈퇴는 이미 커밋됐다. 카카오가 먼저면 카카오 장애 때 탈퇴가 막힌다(약관 제9조 "탈퇴를 막지
    않는다"). 연결이 남아도 다시 로그인하면 신규 가입이라 지운 데이터는 돌아오지 않는다.

    🚨 재시도하지 않는다. 회원번호는 방금 함께 지워져 나중에 다시 부를 수도 없다 — 실패하면
       카카오 앱의 "연결된 서비스" 에 이 서비스가 남는 것이 운영상 한계다 (#379).
    🚨 로그에는 client 가 만든 문구(어느 호출 · 상태 코드 · 카카오 분류값)만 남는다.
       회원번호 · 어드민 키 · 응답 본문은 싣지 않는다 (§7-5).
    """
    async with kakao_admin() as admin:
        if admin is None:
            log.info("KAKAO_ADMIN_KEY 가 비어 있어 카카오 연결 끊기를 건너뛴다")
            return
        try:
            await admin.unlink(user_id)
        except KakaoApiError as exc:
            # ERROR 라 알림이 간다. 부팅 검사는 키가 비었는지만 보므로, 잘못 넣은 키(오타 ·
            # 재발급 전 키)는 여기서만 드러난다. 회원번호가 지워져 나중에 다시 끊을 수도 없다.
            log.error("카카오 연결 끊기 실패 — 탈퇴는 끝났다: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# 3-4. 교환 — 1회용 코드를 세션으로
# ──────────────────────────────────────────────────────────────────────────────


@provider_router.post("")
async def exchange(
    provider: AuthProvider,
    session: SessionDep,
    body: ExchangeRequest,
    response: Response,
) -> SessionResponse | ConsentRequiredResponse:
    """1회용 코드를 세션(기존 회원) 또는 가입 대기표(신규)로 바꾼다 — 명세 §3-4.

    테스트 A-01 · A-03 · A-04 · A-05 · A-12.

    🚨 응답에 세션 토큰이나 가입 대기표가 실린다. 캐시에 남으면 뒤로 가기나 공용 PC 의
       브라우저 캐시에서 그대로 꺼낼 수 있다 (명세 §3-4 의 Cache-Control: no-store).
       302 둘은 _redirect() 가 붙여주지만, pydantic 모델을 그대로 돌려주는 이쪽은
       Response 를 받아 직접 붙인다.
    """
    response.headers["Cache-Control"] = "no-store"

    _warn_if_unimplemented(provider)
    consumed = await consume_handoff(session, code_hash=hash_token(body.code), provider=provider)
    if consumed is None:
        # 없음·만료·이미 사용됨·다른 provider 의 코드를 구분하지 않는다. 구분해 알려주면
        # 공격자에게 정보를 준다 — 초대 코드 실패를 하나로 묶은 것과 같은 이유다
        # (§8-1 · A-04 · A-12 · #83).
        raise ApiError(401, "invalid_handoff", "로그인을 다시 시도해 주세요")

    if not hmac.compare_digest(consumed.bind_hash, hash_token(body.bind)):
        # 🚨 소비된 것으로 두고 실패시킨다. 롤백해서 재시도 기회를 주면 1회용 코드를
        #    가로챈 쪽이 bind 를 맞출 때까지 반복할 수 있다 (§7-2 · A-05).
        await session.commit()
        raise ApiError(401, "invalid_handoff", "로그인을 다시 시도해 주세요")

    if consumed.parent_id is None:
        # 처음 보는 회원번호. 🚨 아직 아무것도 만들지 않는다 — parent 는 필수 동의를
        # 검증한 signup 트랜잭션에서만 생긴다 (§6-1 · A-01).
        consent_code = secrets.token_urlsafe(32)
        await create_handoff(
            session,
            provider=provider,
            code_hash=hash_token(consent_code),
            bind_hash=consumed.bind_hash,
            parent_id=None,
            provider_user_id=consumed.provider_user_id,
            # 가입 대기표는 10분. 동의 화면을 읽을 시간이 필요하다 (§5-4).
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.SIGNUP_TICKET_TTL),
        )
        await session.commit()
        return ConsentRequiredResponse(consent_code=consent_code)

    issued = await _issue_session(session, response, parent_id=consumed.parent_id, is_new=False)
    await session.commit()
    return issued


# ──────────────────────────────────────────────────────────────────────────────
# 3-5. 가입 — 동의와 계정을 한 트랜잭션에
# ──────────────────────────────────────────────────────────────────────────────


@provider_router.post("/signup")
async def signup(
    provider: AuthProvider,
    session: SessionDep,
    body: SignupRequest,
    response: Response,
) -> SessionResponse:
    """필수 동의를 받고 계정을 만든다 — 명세 §3-5 · 테스트 A-02 · A-13.

    🚨 parent · auth_identity · consent · session 을 한 트랜잭션에서 만든다. 중간에
       실패하면 아무것도 남지 않아야 한다 — 동의 없는 계정이 DB 에 남는 것이 §6-1 이
       막으려는 바로 그 상태다.
    """
    response.headers["Cache-Control"] = "no-store"

    granted = {consent.scope for consent in body.consents}
    missing = [scope for scope in REQUIRED_ACCOUNT_SCOPES if scope not in granted]
    if missing:
        # 🚨 대기표를 소비하기 전에 본다. 필수 동의를 빼먹는 것은 공격이 아니라 사용자의
        #    선택이라, 여기서 소비하면 체크박스 하나 놓친 사람이 로그인부터 다시 해야
        #    한다. bind 불일치(공격 신호)와는 다르게 다룬다 (A-13).
        raise ApiError(
            403,
            "consent_required",
            "필수 항목에 동의해야 가입할 수 있어요",
            {"missing": [scope.value for scope in missing]},
        )

    # 🚨 대기표를 소비하기 전에 본다 — 위 필수 동의 검사와 같은 이유다. 등록되지 않은
    #    정책 버전은 공격이 아니라 낡은 화면을 띄워 둔 사용자다. 여기서 대기표를 태우면
    #    새로고침 한 번이면 될 일에 로그인부터 다시 시켜야 한다.
    #    등록·기간 검사를 통과한 버전만 동의로 남긴다 — 재현할 수 없는 문구에
    #    "동의했다" 고 적지 않는다 (노션 정책 정본 §5).
    # 🚨 받는 버전은 GET /policies 가 지금 보여 주는 그 버전 하나다 (#91). "유효하기만 하면"
    #    받으면, 새 버전을 등록하면서 옛 행에 ended_at 을 찍는 것을 잊었을 때 보여 주지도
    #    않는 옛 글에 "동의했다" 는 기록이 남는다. 사람이 기억할 일을 코드가 막는다.
    now = datetime.now(UTC)
    current = {row.scope: row for row in await find_active_versions(session, now=now)}
    versions: dict[ConsentScope, UUID] = {}
    for consent in body.consents:
        if consent.scope not in ACCOUNT_SCOPES:
            continue
        registered = current.get(consent.scope)
        if registered is None or registered.version != consent.policy_version:
            raise ApiError(
                400,
                "policy_version_invalid",
                "동의 화면을 다시 불러와 주세요",
                {"scope": consent.scope.value, "policy_version": consent.policy_version},
            )
        versions[consent.scope] = registered.id

    _warn_if_unimplemented(provider)
    consumed = await consume_handoff(
        session, code_hash=hash_token(body.consent_code), provider=provider
    )
    if consumed is None:
        raise ApiError(401, "invalid_handoff", "로그인을 다시 시도해 주세요")

    if not hmac.compare_digest(consumed.bind_hash, hash_token(body.bind)):
        # 교환과 같다 — 소비된 것으로 두고 실패시킨다 (§7-2).
        await session.commit()
        raise ApiError(401, "invalid_handoff", "로그인을 다시 시도해 주세요")

    if consumed.provider_user_id is None:
        # 기존 회원의 교환용 코드로 가입을 부른 것이다. 대기표와 교환용 코드는 같은
        # 테이블에 살고 둘 중 하나만 차 있으므로 여기서 갈린다.
        raise ApiError(401, "invalid_handoff", "로그인을 다시 시도해 주세요")

    parent = await create_parent(session, nickname=body.nickname)
    await create_identity(
        session,
        parent_id=parent.id,
        provider=provider,
        provider_user_id=consumed.provider_user_id,
    )
    for scope, policy_version_id in versions.items():
        await grant_account_scope(
            session,
            parent_id=parent.id,
            scope=scope,
            policy_version_id=policy_version_id,
        )

    issued = await _issue_session(session, response, parent_id=parent.id, is_new=True)
    await session.commit()
    return issued


# ──────────────────────────────────────────────────────────────────────────────
# 안쪽 도구
# ──────────────────────────────────────────────────────────────────────────────


async def _issue_session(
    session: SessionDep,
    response: Response,
    *,
    parent_id: UUID,
    is_new: bool,
) -> SessionResponse:
    """로그인 1건(refresh)과 access 1건을 만들고 응답을 짠다 — 교환과 가입이 같이 쓴다 (§3-4).

    access 는 본문으로, refresh 는 쿠키로 나간다 (#466).

    🚨 토큰 원문은 저장되지 않는다. 남기는 것은 해시뿐이고, 원문은 이 응답에 한 번
       실려 나가면 끝이다 (§5 · A-18).
    """
    parent = await find_parent(session, parent_id)
    if parent is None:
        # 방어용. 탈퇴하면 대기표도 보호자 행과 함께 지워져 교환은 그 전에 401 invalid_handoff
        # 로 끝나므로 실제로는 오지 않는다 (#379).
        raise ApiError(404, "not_found", "계정을 찾을 수 없어요")

    now = datetime.now(UTC)
    absolute_expires_at = now + timedelta(seconds=settings.REFRESH_ABSOLUTE_TTL)
    refresh = secrets.token_urlsafe(32)
    refresh_expires_at = min(
        now + timedelta(seconds=settings.REFRESH_IDLE_TTL), absolute_expires_at
    )
    session_id = await create_session(
        session,
        parent_id=parent_id,
        refresh_hash=hash_token(refresh),
        refresh_expires_at=refresh_expires_at,
        absolute_expires_at=absolute_expires_at,
    )
    _set_refresh(response, refresh, max_age=int((refresh_expires_at - now).total_seconds()))
    token, expires_in = await _issue_access(
        session, session_id=session_id, now=now, absolute_expires_at=absolute_expires_at
    )

    return SessionResponse(
        token=token,
        expires_in=expires_in,
        is_new=is_new,
        parent=ParentSummary(id=parent.id, nickname=parent.nickname),
        consent_required=await missing_account_scopes(session, parent_id=parent_id),
    )


async def _issue_access(
    session: SessionDep,
    *,
    session_id: UUID,
    now: datetime,
    absolute_expires_at: datetime,
) -> tuple[str, int]:
    """그 로그인에 access 1건을 달고 (원문, 수명 초) 를 돌려준다 — 로그인과 refresh 가 같이 쓴다.

    수명은 ACCESS_TTL 이되 로그인의 절대 만료를 넘지 않는다. 넘기면 상한 뒤에도 access 로
    잠깐 더 쓸 수 있다.
    """
    token = secrets.token_urlsafe(32)
    expires_at = min(now + timedelta(seconds=settings.ACCESS_TTL), absolute_expires_at)
    await create_access(
        session, session_id=session_id, token_hash=hash_token(token), expires_at=expires_at
    )
    return token, int((expires_at - now).total_seconds())


def _warn_if_unimplemented(provider: AuthProvider) -> None:
    """구현되지 않은 provider 로 코드를 내민 요청을 기록한다 (#83).

    거절은 여기서 하지 않는다 — consume_handoff 가 provider 까지 조건에 넣어 행을
    못 찾고, 호출부가 없는 코드와 똑같이 401 로 답한다(§8-1). 다만 카카오 외의
    provider 로 오는 요청은 정상 흐름에 존재할 수 없으므로 신호로 남길 값어치가 있다.

    🚨 코드나 bind 는 남기지 않는다 (§7-5).
    """
    if provider is not AuthProvider.KAKAO:
        log.warning("구현되지 않은 provider 로 1회용 코드 소비 시도: %s", provider.value)


def _missing_keys(provider: AuthProvider) -> list[str]:
    """이 provider 로 로그인을 시작할 수 없게 만드는 빈 설정.

    복귀 URL 은 여기서 보지 않는다 — 없으면 서버가 아예 뜨지 않기 때문이다
    (Settings 의 검증, #45). ready 는 "카카오 설정이 갖춰졌나" 라는 원래 뜻을 지킨다.
    """
    if provider is not AuthProvider.KAKAO:
        # 카카오 외에는 설정 자체가 없다. enum 에는 있으므로 422 가 아니라 ready: false 다.
        return [f"{provider.value.upper()} 미구현"]
    return settings.kakao_missing_keys


def _is_ready(provider: AuthProvider) -> bool:
    """이 provider 로 로그인을 시작할 수 있는가 — 빈 설정이 하나도 없을 때만."""
    return not _missing_keys(provider)


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
    """복귀 URL. 서버 환경변수에서만 온다 — 클라이언트는 URL 을 지정하지 못한다 (§2-3).

    둘 다 비어 있을 수 없다 — 하나라도 없으면 서버가 뜨지 않는다 (Settings 의 검증, #45).
    그래서 여기서 다시 확인하지 않는다.
    """
    return settings.AUTH_RETURN_URL_APP if target == "app" else settings.AUTH_RETURN_URL_WEB


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


def _set_refresh(response: Response, refresh: str, *, max_age: int) -> None:
    """refresh 를 쿠키로 심는다 (#466).

    🚨 max_age 를 빼지 않는다. 빠지면 세션 쿠키가 되어 앱을 끌 때 같이 사라진다 —
       sessionStorage 때문에 매번 다시 로그인하던 지금 증상이 그대로 돌아온다.
    🚨 Domain 을 주지 않는다. host-only 로 API 호스트에만 저장된다.
    SameSite=Strict 여도 웹(icatch.ai.kr)이 API(api.icatch.ai.kr)를 부를 때는 실린다 — ai.kr 이
    Public Suffix List 에 있어 둘이 같은 사이트다. 다른 사이트에서 오는 요청에는 실리지 않는다.
    """
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=max_age,
        path=AUTH_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="strict",
    )


def _expire_refresh(response: Response) -> None:
    """refresh 쿠키를 지운다 — 로그아웃 · 탈퇴. 속성이 심을 때와 같아야 지워진다."""
    response.delete_cookie(
        REFRESH_COOKIE, path=AUTH_COOKIE_PATH, httponly=True, secure=True, samesite="strict"
    )


def _expire_state(response: RedirectResponse) -> RedirectResponse:
    """쓴 쿠키는 콜백에서 만료시킨다 (§3-3 5번). Path 가 심을 때와 같아야 지워진다."""
    response.delete_cookie(STATE_COOKIE, path=AUTH_COOKIE_PATH)
    return response


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(encoded: str) -> bytes:
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
