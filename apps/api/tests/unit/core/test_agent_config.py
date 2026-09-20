"""Agent 키 선언이 .env 템플릿·Agent 설정과 어긋나지 않는지 본다.

core Settings 는 extra=forbid 다. .env 에 선언되지 않은 키가 하나라도 있으면
import 단계에서 서버가 멈추고, pytest 는 수집조차 못 한다. 그런데 apps/api/.env 는
백엔드와 Agent 가 한 파일을 나눠 쓴다 — Agent 쪽에만 키를 추가하면 그 순간 부팅이 깨진다.
Agent 를 추가할 때(activity · growth · health) 한쪽만 고치는 실수를 여기서 잡는다.
"""

import re
from pathlib import Path

from app.agents.common.config import AgentSettings
from app.core.agent_config import AgentLLMSettings
from app.core.config import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"
_LLM_SUFFIXES = ("_API_KEY", "_BASE_URL", "_MODEL", "_REASONING_EFFORT")


def _example_keys() -> set[str]:
    return set(re.findall(r"^([A-Z_]+)=", _ENV_EXAMPLE.read_text(encoding="utf-8"), re.M))


def test_1() -> None:
    """.env.example 을 그대로 복사해도 부팅된다 — 템플릿의 키가 전부 선언돼 있다."""
    unknown = sorted(_example_keys() - set(Settings.model_fields))
    assert not unknown, f"Settings 에 없는 .env.example 키: {unknown}"


def test_2() -> None:
    """Agent 가 읽는 역할별 LLM 키를 core 도 전부 알고 있다."""
    agent_keys = {name for name in AgentSettings.model_fields if name.endswith(_LLM_SUFFIXES)}
    missing = sorted(agent_keys - set(Settings.model_fields))
    assert not missing, f"core Settings 에 없는 Agent 키: {missing}"


def test_3() -> None:
    """선언만 하고 템플릿에 안 적은 키가 없다 — 팀원이 존재를 모르게 되는 것을 막는다."""
    undocumented = sorted(set(Settings.model_fields) - _example_keys())
    assert not undocumented, f".env.example 에 없는 선언: {undocumented}"


# --- _blank_is_default validator ---


def test_blank_timeout_falls_back_to_default() -> None:
    """LLM_TIMEOUT_S 가 빈 문자열이면 기본값 60.0 을 쓴다."""
    s = AgentLLMSettings(LLM_TIMEOUT_S="", _env_file=None)
    assert s.LLM_TIMEOUT_S == 60.0


def test_explicit_timeout_is_preserved() -> None:
    """LLM_TIMEOUT_S 에 값을 넣으면 그대로 유지된다."""
    s = AgentLLMSettings(LLM_TIMEOUT_S=30.0, _env_file=None)
    assert s.LLM_TIMEOUT_S == 30.0


def test_blank_max_retries_falls_back_to_default() -> None:
    """LLM_MAX_RETRIES 가 빈 문자열이면 기본값 2 를 쓴다."""
    s = AgentLLMSettings(LLM_MAX_RETRIES="", _env_file=None)
    assert s.LLM_MAX_RETRIES == 2
