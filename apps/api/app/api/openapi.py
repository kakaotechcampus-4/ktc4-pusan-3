"""Swagger 의 형식 오류 모양을 실제 서버와 맞춘다 (#438 · docs/api/errors-v1.md).

서버는 요청 형식 오류(RequestValidationError)를 봉투(`ErrorEnvelope`)로 바꿔 보낸다 — 경로 값이면
422, 본문 · 쿼리 · 헤더면 400 (`app/api/errors.py` `_validation_error`). FastAPI 는 그걸 모르고
본문이나 경로 값을 받는 모든 주소에 자기 기본 422 모양(`HTTPValidationError`)을 붙인다. 그래서
Swagger(API 계약 정본)를 읽은 사람은 실제로 오지 않는 모양을 기대하게 된다.

여기서 문서를 만든 직후 한 번 고친다.
- 기본 422 → 경로 값이 있는 주소는 봉투로 바꾸고, 없는 주소는 지운다
  (형식 오류 422 는 경로에서만 난다)
- 본문 · 쿼리 · 헤더를 받는 주소에 400(봉투)이 없으면 더한다
- 라우터가 직접 적은 400 · 422(설명 · 모양이 이미 봉투)는 건드리지 않는다
- 아무도 가리키지 않게 된 `HTTPValidationError` · `ValidationError` 를 지운다

🚨 라우터마다 responses 에 400 · 422 를 손으로 적게 하지 않는다 — 70곳이 넘어 하나만 빠져도 다시
   어긋난다.
   어긋나면 `tests/unit/api/test_openapi_shape.py` 가 잡는다.
"""

from typing import Any

from fastapi import FastAPI

ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"
PATH_422 = "경로 값 형식이 맞지 않아요"
FORMAT_400 = "본문 · 쿼리 · 헤더 형식이 맞지 않아요"
_DEFAULT_SCHEMAS = ("HTTPValidationError", "ValidationError")


def install_openapi(app: FastAPI) -> None:
    """`app.openapi()` 가 고친 문서를 돌려주게 한다. FastAPI 처럼 처음 한 번 만들고 들고 있는다."""
    build = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        spec = build()
        align_validation_errors(spec)
        app.openapi_schema = spec
        return spec

    app.openapi = openapi  # type: ignore[method-assign]


def align_validation_errors(spec: dict[str, Any]) -> None:
    """문서를 그 자리에서 고친다."""
    for item in spec.get("paths", {}).values():
        for op in item.values():
            if isinstance(op, dict) and "responses" in op:
                _align_operation(op)
    schemas = spec.get("components", {}).get("schemas", {})
    for name in _DEFAULT_SCHEMAS:
        schemas.pop(name, None)


def _align_operation(op: dict[str, Any]) -> None:
    responses = op["responses"]
    where = {p.get("in") for p in op.get("parameters", [])}
    if _is_fastapi_default(responses.get("422")):
        if "path" in where:
            responses["422"] = _envelope(PATH_422)
        else:
            del responses["422"]
    takes_non_path = "requestBody" in op or bool(where - {"path"})
    if takes_non_path and "400" not in responses:
        responses["400"] = _envelope(FORMAT_400)


def _is_fastapi_default(response: dict[str, Any] | None) -> bool:
    if not response:
        return False
    schema = response.get("content", {}).get("application/json", {}).get("schema", {})
    return schema.get("$ref", "").endswith("/HTTPValidationError")


def _envelope(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": ENVELOPE_REF}}},
    }
