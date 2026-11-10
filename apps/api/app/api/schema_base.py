"""API 스키마의 공통 바탕 — 필드 아래 설명 글을 Swagger(API 계약 정본)에 싣는다 (#438).

pydantic 은 `use_attribute_docstrings` 가 없으면 필드 바로 아래 설명 글(attribute docstring)을
버린다. 그러면 루트 CLAUDE.md §5 "필드의 뜻은 스키마의 docstring 에 적으면 Swagger 에 나온다" 가
거짓이 된다 — 한동안 `schemas/policies.py` 한 곳에만 이 설정이 있어 나머지 칸이 설명 없이 나갔다.

🚨 요청 · 응답 스키마는 BaseModel 대신 이것을 상속한다. 빠뜨리면
   `tests/unit/api/test_openapi_shape.py` 가 잡는다. 자기 `model_config` 를 둔 클래스도 그대로
   상속하면 pydantic 이 두 설정을 합친다(예: `extra="forbid"`).
"""

from pydantic import BaseModel, ConfigDict


class ApiSchema(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)
