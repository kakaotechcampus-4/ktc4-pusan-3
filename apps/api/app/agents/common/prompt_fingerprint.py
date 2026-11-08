"""프롬프트 지문 — 모델에 보내는 프롬프트가 어떤 글이었는지 12자로 남긴다 (#770).

지문 = **고정 입력으로 렌더링한 system 프롬프트 + 그 이름이 쓰는 tool spec · 응답 스키마** 의
SHA-256 이다. 평가 문서(`docs/agents/eval/<이름>_EVAL.md`)의 측정 이력 마지막 행 지문과 이 값이
다르면 `tests/unit/agents/common/test_prompt_history.py` 가 실패한다 — 프롬프트를 바꿨으면 바꾼
프롬프트로 다시 재고 행을 더하라는 뜻이다.

**등록은 각 Agent 가 자기 `prompt.py` 에서 한다** (`register_prompt`). 이 파일은 도메인 Agent 를
import 하지 않는다 — common 은 Agent 패키지를 모른다(`test_common_imports.py`).
프롬프트를 만드는 모듈이 import 될 때 등록되므로, 그 모듈이 만든 프롬프트로 부르는
`LLMClient` 로그에서는 이름이 항상 등록돼 있다.
10곳을 한꺼번에 불러 모으는 일은 `app/agents/prompt_registry.py` 가 한다 (테스트 · 문서 검사용).

- 동적 입력(현재 시각 · 월령 · 문서 예시 · 날씨 · 검색 결과)은 **고정값**(`fixed_context` ·
  `fixed_gate`)으로 바꿔 렌더링한다. 그래야 run 마다 값이 흔들리지 않고, 코드만 바뀐 커밋은 지문이
  그대로다.
- task · 상태에 따라 구획이 갈리는 곳은 갈래마다 렌더링해 전부 이어 붙인다.
  어느 갈래의 글이 바뀌어도 지문이 바뀐다.
- 모델 · reasoning_effort 는 넣지 않는다 (역할별 `.env` 값). 평가 문서의 `조건` 칸에 따로 적는다.
- 한계: 문서 예시 한 줄의 **서식**(`_examples` 가 행을 글로 만드는 부분)과 사용자 메시지는 지문에
  들어가지 않는다 — 예시 행 자체가 DB 에서 오기 때문이다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from app.agents.common.gate import DataReady, Gate
from app.agents.common.tool_schema import ToolDefinition, build_tool_specs
from app.core.prompt_hash import canonical_json, fingerprint, short
from app.rules.age import LifeStage

# 지문을 만들 때 쓰는 고정 입력
FIXED_TZ = ZoneInfo("Asia/Seoul")
FIXED_NOW = datetime(2000, 1, 1, tzinfo=FIXED_TZ)
FIXED_MONTHS = 36

PartsFn = Callable[[], tuple[str, ...]]
_REGISTRY: dict[str, PartsFn] = {}


@dataclass(frozen=True)
class PromptPrint:
    name: str
    sha256: str

    @property
    def short(self) -> str:
        return short(self.sha256)


def register_prompt(name: str, parts: PartsFn) -> None:
    """이 이름의 프롬프트를 지문 대상으로 등록한다. 같은 이름이면 마지막 것이 쓰인다."""
    _REGISTRY[name] = parts


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def registered_names() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def prompt_parts(name: str) -> tuple[str, ...]:
    """지문의 입력 조각 — 렌더링한 system 프롬프트들과 tool spec · 응답 스키마.

    모르는 이름은 KeyError.
    """
    return _REGISTRY[name]()


def prompt_fingerprint(name: str) -> PromptPrint:
    return PromptPrint(name=name, sha256=fingerprint(*prompt_parts(name)))


@lru_cache(maxsize=None)
def logged_fingerprint(name: str) -> PromptPrint:
    """호출 로그에 다는 지문. 이름마다 한 번만 계산한다 (프롬프트는 실행 중 안 바뀐다)."""
    return prompt_fingerprint(name)


def fixed_context(**state: Any) -> SimpleNamespace:
    """프롬프트 함수가 읽는 값(시각 · timezone · run 상태)만 든 고정 context."""
    return SimpleNamespace(
        now=FIXED_NOW,
        timezone=FIXED_TZ,
        today=FIXED_NOW.date(),
        state=SimpleNamespace(**state),
    )


def fixed_gate(
    *, has_location: bool = False, places: bool = False, weather: bool = False, events: bool = False
) -> Gate:
    return Gate(
        stage=LifeStage(months=FIXED_MONTHS, stage="preschool", big="toddler"),
        consent_child_health=True,
        safety_ok=True,
        has_location=has_location,
        data=DataReady(places=places, weather=weather, events=events),
    )


def tools_text(definitions: list[ToolDefinition]) -> str:
    """tool 정의 목록을 모델에 보내는 spec 그대로 만든 뒤 키 순서와 무관한 글로."""
    return canonical_json(build_tool_specs(list(definitions)))
