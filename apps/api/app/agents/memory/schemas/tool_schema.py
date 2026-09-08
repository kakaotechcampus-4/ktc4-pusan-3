"""Pydantic 모델을 OpenAI function tool 스펙으로 바꾼다.

스키마 정본은 Pydantic 모델 하나뿐이다. 모델에서 JSON Schema를 뽑아 쓰기 때문에
검증 규칙과 모델에게 보내는 스펙이 어긋날 수 없다.

매 요청마다 tool 27개의 스키마가 통째로 실려 나가므로 토큰을 아낀다.
$ref 를 펼쳐 넣고 모델이 안 읽는 키는 뺀다.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

_NOISE_KEYS = frozenset({"title"})       # 모델 판단에 쓸모없는 키
_MAPPING_KEYS = frozenset({"properties", "$defs"})   # 키가 필드 이름인 자리


@dataclass(frozen=True)
class ToolDefinition:
    """tool 하나. description 에는 '무엇을 하는가'보다 '언제 호출하는가'를 적는다."""
    name: str
    description: str
    args: type[BaseModel]


def to_parameters(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 모델 → OpenAI function.parameters 용 JSON Schema."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return _clean(_inline_refs(schema, defs))


def build_tool_spec(definition: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": definition.name,
            "description": definition.description,
            "parameters": to_parameters(definition.args),
        },
    }


def build_tool_specs(definitions: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [build_tool_spec(definition) for definition in definitions]


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """$ref 를 실제 정의로 치환한다. enum 하나 때문에 $defs 를 통째로 보내지 않는다."""
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if ref is None:
        return {key: _inline_refs(value, defs) for key, value in node.items()}

    target = _inline_refs(defs[ref.rsplit("/", 1)[-1]], defs)
    overrides = {key: value for key, value in node.items() if key != "$ref"}
    return {**target, **overrides}  # $ref 옆에 붙은 description 우선


def _clean(node: Any) -> Any:
    """노이즈 키를 걷어내고 모든 object 에 additionalProperties=false 를 강제한다."""
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key in _NOISE_KEYS:
            continue
        # properties의 키는 스키마 키워드가 아니라 필드 이름
        if key in _MAPPING_KEYS and isinstance(value, dict):
            cleaned[key] = {name: _clean(sub) for name, sub in value.items()}
        else:
            cleaned[key] = _clean(value)

    if cleaned.get("type") == "object":
        cleaned["additionalProperties"] = False
    return cleaned
