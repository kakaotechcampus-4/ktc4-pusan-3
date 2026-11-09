"""Swagger(API 계약 정본)가 실제 서버와 같은 말을 하는지 — #438 🟡 두 줄.

1. 필드 아래 설명 글(attribute docstring)이 Swagger 에 나간다. pydantic 은
   `use_attribute_docstrings` 가 없으면 그 글을 버린다 — 한동안 `schemas/policies.py` 에만
   있었다.
2. 오류 모양이 실제와 같다. 서버는 형식 오류를 봉투(`ErrorEnvelope`)로 보낸다 — 본문 · 쿼리는 400,
   경로 값은 422 (`app/api/errors.py` · `docs/api/errors-v1.md`). FastAPI 는 그걸 모르고 모든 주소에
   자기 기본 422 모양(`HTTPValidationError`)을 붙였다.
"""

import pytest
from pydantic import BaseModel

from app.main import app

ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"


def _all_models(cls: type[BaseModel] = BaseModel) -> list[type[BaseModel]]:
    found = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_all_models(sub))
    return found


def _swagger_models() -> list[type[BaseModel]]:
    """Swagger 에 실린 스키마의 클래스 — 폴더가 아니라 "Swagger 에 실리는가" 로 고른다 (#893 리뷰).

    `app/api/v1/schemas/` 밖(예: `app/core/event_draft.py`)에 있어도 요청 · 응답에 쓰이면 실린다.
    FastAPI 가 만드는 multipart 본문(`Body_…`)은 우리 클래스가 아니라 뺀다.
    """
    names = {n for n in app.openapi()["components"]["schemas"] if not n.startswith("Body_")}
    return [m for m in _all_models() if m.__name__ in names and m.__module__.startswith("app.")]


def _operations():
    for path, item in app.openapi()["paths"].items():
        for method, op in item.items():
            yield method.upper(), path, op


def _ref(response: dict) -> str | None:
    return response.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")


def test_Swagger_에_실리는_스키마는_모두_필드_설명을_싣는다() -> None:
    models = _swagger_models()
    missing = [
        f"{m.__module__}.{m.__qualname__}"
        for m in models
        if not m.model_config.get("use_attribute_docstrings")
    ]

    assert len(models) > 100, "Swagger 스키마를 거의 못 찾았다 — 고르는 방법이 바뀌었다"
    assert missing == []


def test_필드_아래_설명_글이_Swagger_에_나간다() -> None:
    # schemas/auth.py — `ready: bool` 바로 아래 설명 글. policies.py 밖의 칸이다
    ready = app.openapi()["components"]["schemas"]["AuthStatusResponse"]["properties"]["ready"]

    assert "로그인을 시작할 수 있는" in ready.get("description", "")


def test_FastAPI_기본_422_모양이_남아_있지_않다() -> None:
    spec = app.openapi()
    defaults = [
        f"{m} {p}"
        for m, p, op in _operations()
        if (_ref(op.get("responses", {}).get("422", {})) or "").endswith("HTTPValidationError")
    ]

    assert defaults == []
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    assert "ValidationError" not in spec["components"]["schemas"]


@pytest.mark.parametrize(
    ("where", "status"),
    [("path", "422"), ("query", "400"), ("body", "400")],
)
def test_형식_오류를_받을_수_있는_주소는_그_코드를_봉투로_안내한다(where: str, status: str) -> None:
    """본문 · 쿼리 형식 오류는 400, 경로 값 형식 오류는 422 — 둘 다 봉투로 (errors.py)."""
    wrong = []
    for method, path, op in _operations():
        params = {p["in"] for p in op.get("parameters", [])}
        takes = "requestBody" in op if where == "body" else where in params
        if not takes:
            continue
        response = op.get("responses", {}).get(status)
        if response is None or _ref(response) != ENVELOPE_REF:
            wrong.append(f"{method} {path}")

    assert wrong == []


def test_경로_값이_없는_주소는_형식_오류_422_를_안내하지_않는다() -> None:
    """형식 오류 422 는 경로 값에서만 나온다. 라우터가 직접 422 를 적었으면(설명이 다르면) 둔다."""
    wrong = [
        f"{m} {p}"
        for m, p, op in _operations()
        if "422" in op.get("responses", {})
        and not any(x["in"] == "path" for x in op.get("parameters", []))
        and op["responses"]["422"].get("description") == "경로 값 형식이 맞지 않아요"
    ]

    assert wrong == []
