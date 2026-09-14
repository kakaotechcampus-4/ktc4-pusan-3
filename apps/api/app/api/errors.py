"""계약서 §01 에러 봉투 — 모든 에러 응답이 여기를 통과한다.

FastAPI는 이 예외 처리기를 자동으로 등록하지 않으므로
register_error_handlers(app)를 app/main.py에서 한 번 직접 호출한다.

FastAPI 기본 에러 응답은 {"detail": "..."} 인데 계약은
{"error": {"code", "message", "detail"}} 다. 프론트 apps/web/src/lib/api/errors.ts 가
이 모양만 뜯을 줄 알고 MSW 목도 이 모양으로 돌고 있으므로, 봉투 구조는 결정 사항이
아니라 맞춰야 하는 값이다.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    """오류 코드, 사용자 메시지, 선택 상세 정보를 담는다."""

    code: str
    message: str
    detail: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    """봉투. 응답 생성과 OpenAPI 문서가 같은 정의를 쓴다 — 두 벌이 되면 어긋난다.

    라우터에서 문서에 싣는 법:
        @router.post("", responses={401: {"model": ErrorEnvelope}})
    """

    error: ErrorBody


class ApiError(Exception):
    """우리 코드가 던지는 유일한 에러.

    HTTP 상태와 서비스 오류 코드를 함께 들고 다니므로 핸들러가 그대로 봉투에 옮긴다.

    🚨 HTTPException 을 직접 던지지 않는다. 그쪽은 status 만 있고 code 가 없어서
       아래 _FRAMEWORK_CODE 의 추측에 기대게 된다.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def envelope(
    status: int,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """봉투를 만드는 유일한 함수. detail 이 없으면 키 자체를 빼고 내린다.

    🚨 mode="json" 이 빠지면 detail 에 UUID·datetime 이 들어온 순간 JSONResponse 의
       json.dumps 가 예외 핸들러 안에서 터진다. 그러면 봉투가 아니라 plain text 500 이
       나가고 프론트가 code 를 못 읽는다.
    """
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message, detail=detail))
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


# 프레임워크가 스스로 내는 status 만 적는다. 우리 코드는 ApiError 를 던진다.
#
# 🚨 상태와 코드를 항상 같은 기준으로 맞춘다. 코드만 바꾸고 상태를 그대로 두면
#    프론트가 둘 중 무엇으로 분기해야 할지 알 수 없다.
_FRAMEWORK_CODE: dict[int, str] = {
    401: "unauthenticated",
    404: "not_found",
}

_METHOD_NOT_ALLOWED = 405
"""405 는 404 로 바꿔 내린다.

"메서드가 다르다" 는 곧 "그 경로는 있다" 는 뜻이다. 상태를 405 로 두고 코드만 not_found
로 적으면 숨긴 것이 아니다 — 공격자는 코드가 아니라 상태를 본다. Allow 헤더도 같은
이유로 지운다. 셋 중 하나라도 남으면 경로 존재가 드러난다.
"""


def register_error_handlers(app: FastAPI) -> None:
    """애플리케이션에서 사용할 공통 예외 처리기를 등록한다."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return envelope(exc.status, exc.code, exc.message, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        status = exc.status_code
        # 봉투를 새로 만들면서 원래 응답의 헤더를 잃지 않는다 — 인증 스킴의
        # WWW-Authenticate, 앞으로 쓸 429 의 Retry-After 가 여기 실린다.
        headers = dict(exc.headers or {})

        if status == _METHOD_NOT_ALLOWED:
            status = 404
            headers = {name: value for name, value in headers.items() if name.lower() != "allow"}

        code = _FRAMEWORK_CODE.get(status)
        if code is None:
            # 여기 오는 것은 버그다 — 우리 코드가 HTTPException 을 직접 던졌다는 뜻.
            # 원래 상태는 로그에만 남기고 밖으로는 500 으로 나간다. 매핑이 없다는 것은
            # 그 상태로 내보낼 코드를 정한 적이 없다는 뜻이라, 짝이 안 맞는 응답을
            # 만들어 내보내지 않는다.
            log.warning("매핑 없는 HTTPException status=%s", status)
            return envelope(500, "internal_error", "요청을 처리할 수 없어요")

        return envelope(status, code, "요청을 처리할 수 없어요", headers=headers or None)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # 🚨 FastAPI 는 "경로 값이 enum 밖" 과 "바디 필드 누락" 을 같은 예외로 낸다.
        #    명세 §8-1 은 앞을 422, 뒤를 400 으로 정했으므로 loc 의 첫 칸으로 가른다.
        errors = exc.errors()
        # loc 는 없을 수도, 있지만 빈 튜플일 수도 있다. [0] 을 그냥 집으면
        # 예외 핸들러 안에서 IndexError 가 나서 500 이 된다.
        in_path = any((e.get("loc") or ("",))[0] == "path" for e in errors)
        fields = [".".join(str(part) for part in (e.get("loc") or ())[1:]) for e in errors]
        return envelope(
            422 if in_path else 400,
            "validation_failed",
            "요청 형식이 올바르지 않아요",
            {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # 마지막 그물.
        #
        # 🚨 예외 메시지를 응답에 넣지 않는다 — 쿼리·토큰·카카오 인가 코드가 섞여
        #    나간다 (명세 §7-5).
        # 참고: Starlette 은 이 응답을 보낸 뒤 예외를 다시 raise 한다. 테스트에서는
        #      예외가 그대로 올라오고(정상), 운영에서는 uvicorn 이 로그만 남긴다.
        log.exception("처리되지 않은 예외")
        return envelope(500, "internal_error", "잠시 후 다시 시도해 주세요")
