"""계약서 §01 에러 봉투 — 모든 에러 응답이 여기를 통과한다.

Spring 대응: @RestControllerAdvice + @ExceptionHandler 묶음.
    Spring 은 컴포넌트 스캔이 그것을 알아서 등록하지만 FastAPI 는 스캔이 없어서,
    register_error_handlers(app) 를 app/main.py 에서 한 번 직접 불러 준다.

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
    """Spring 대응: ErrorResponse record."""

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

    Spring 대응: RuntimeException 을 상속한 BusinessException + ErrorCode enum.
    status 와 code 를 짝지어 들고 다니므로 핸들러가 그대로 봉투에 옮긴다.

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
) -> JSONResponse:
    """봉투를 만드는 유일한 함수. detail 이 없으면 키 자체를 빼고 내린다."""
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message, detail=detail))
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True))


# 프레임워크가 스스로 내는 status 만 적는다. 우리 코드는 ApiError 를 던진다.
# 405 도 not_found 로 내린다 — 그 경로가 존재한다는 사실을 밖에 알리지 않는다.
_FRAMEWORK_CODE: dict[int, str] = {
    401: "unauthenticated",
    404: "not_found",
    405: "not_found",
}


# REVIEW: 이 함수는 웹 프레임워크의 예외를 공통 API 오류 응답으로 바꾸지만 `app.core`에 있다.
# `apps/api/CLAUDE.md`에는 core의 허용 의존성이 없어 파일 위치에 대한 판단 근거가 부족하다.
# 확인할 내용: 프레임워크 전용 예외 처리를 `app.core`에 둘지 `app.api`로 옮길지 결정한다.
# 검증: `uv run pytest tests/integration/api/test_errors.py -q` (오류 응답 형식)
def register_error_handlers(app: FastAPI) -> None:
    """Spring 의 @RestControllerAdvice 등록을 손으로 하는 지점."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return envelope(exc.status, exc.code, exc.message, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _FRAMEWORK_CODE.get(exc.status_code)
        if code is None:
            # 여기 오는 것은 버그다 — 우리 코드가 HTTPException 을 직접 던졌다는 뜻.
            log.warning("매핑 없는 HTTPException status=%s", exc.status_code)
            code = "internal_error"
        return envelope(exc.status_code, code, "요청을 처리할 수 없어요")

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Spring 대응: MethodArgumentNotValidException 핸들러.
        #
        # 🚨 FastAPI 는 "경로 값이 enum 밖" 과 "바디 필드 누락" 을 같은 예외로 낸다.
        #    명세 §8-1 은 앞을 422, 뒤를 400 으로 정했으므로 loc 의 첫 칸으로 가른다.
        errors = exc.errors()
        in_path = any(e.get("loc", ("",))[0] == "path" for e in errors)
        fields = [".".join(str(part) for part in e.get("loc", ())[1:]) for e in errors]
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
